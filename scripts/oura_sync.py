#!/usr/bin/env python3
"""Fetch personal Oura API V2 data into local JSON and CSV files.

This script intentionally uses only the Python standard library so it can run
on a fresh machine without dependency installation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import secrets
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


API_BASE = "https://api.ouraring.com"
AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("OURA_REQUEST_TIMEOUT_SECONDS", "30"))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOKEN_FILE = PROJECT_ROOT / ".oura" / "tokens.json"
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_CSV_DIR = PROJECT_ROOT / "data" / "csv"

DEFAULT_SCOPES = [
    "daily",
    "heartrate",
    "workout",
    "tag",
    "session",
    "spo2",
    "personal",
    "ring_configuration",
    "stress",
    "heart_health",
]


@dataclass(frozen=True)
class Endpoint:
    name: str
    kind: str
    description: str
    max_days: int


ENDPOINTS: dict[str, Endpoint] = {
    "daily_activity": Endpoint("daily_activity", "date", "Daily activity score, steps, MET, calories", 92),
    "daily_readiness": Endpoint("daily_readiness", "date", "Daily readiness score and contributors", 92),
    "daily_sleep": Endpoint("daily_sleep", "date", "Daily sleep score and contributors", 92),
    "daily_spo2": Endpoint("daily_spo2", "date", "Daily overnight SpO2", 92),
    "daily_stress": Endpoint("daily_stress", "date", "Daily stress and recovery durations", 92),
    "daily_resilience": Endpoint("daily_resilience", "date", "Resilience level and contributors", 92),
    "daily_cardiovascular_age": Endpoint(
        "daily_cardiovascular_age", "date", "Cardiovascular age and pulse wave velocity", 92
    ),
    "vO2_max": Endpoint("vO2_max", "date", "Estimated VO2 max", 92),
    "sleep": Endpoint("sleep", "date", "Detailed sleep periods, including naps", 92),
    "sleep_time": Endpoint("sleep_time", "date", "Recommended sleep timing", 92),
    "workout": Endpoint("workout", "date", "Workouts detected or entered in Oura", 92),
    "session": Endpoint("session", "date", "Guided and unguided sessions", 92),
    "enhanced_tag": Endpoint("enhanced_tag", "date", "Tags and notes", 92),
    "tag": Endpoint("tag", "date", "Legacy tags", 92),
    "rest_mode_period": Endpoint("rest_mode_period", "date", "Rest mode periods", 92),
    "ring_configuration": Endpoint("ring_configuration", "date", "Ring model, size, firmware, setup", 365),
    "heartrate": Endpoint("heartrate", "datetime", "Time-series heart rate", 30),
    "ring_battery_level": Endpoint("ring_battery_level", "datetime", "Time-series ring battery level", 30),
    "personal_info": Endpoint("personal_info", "single", "Profile metadata", 1),
}

DEFAULT_ENDPOINTS = [
    "daily_activity",
    "daily_readiness",
    "daily_sleep",
    "daily_spo2",
    "daily_stress",
    "daily_resilience",
    "daily_cardiovascular_age",
    "vO2_max",
    "sleep",
    "sleep_time",
    "workout",
    "session",
    "enhanced_tag",
    "rest_mode_period",
    "ring_configuration",
    "heartrate",
    "ring_battery_level",
]


class OuraError(RuntimeError):
    pass


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD, got {value!r}") from exc


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def token_expiry(expires_in: int | str | None) -> datetime | None:
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)


def write_json(path: Path, payload: Any, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if private:
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_tokens(path: Path, token_payload: dict[str, Any]) -> None:
    payload = dict(token_payload)
    payload["saved_at"] = iso_now()
    expiry = token_expiry(payload.get("expires_in"))
    if expiry:
        payload["expires_at"] = expiry.isoformat()
    write_json(path, payload, private=True)


def merge_refreshed_tokens(previous: dict[str, Any], refreshed: dict[str, Any]) -> dict[str, Any]:
    """Keep durable fields when an OAuth refresh response omits them."""
    merged = dict(refreshed)
    for key in ("refresh_token", "scope", "token_type"):
        if not merged.get(key) and previous.get(key):
            merged[key] = previous[key]
    return merged


def credential_arg(args: argparse.Namespace, attr: str, env_name: str) -> str:
    value = getattr(args, attr, None) or os.environ.get(env_name)
    if not value:
        raise OuraError(f"Missing {env_name}. Pass --{attr.replace('_', '-')} or add it to .env.")
    return value


def request_json(
    method: str,
    url: str,
    *,
    params: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    token: str | None = None,
    max_retries: int = 3,
) -> tuple[dict[str, Any], dict[str, str]]:
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    data = urllib.parse.urlencode(form).encode("utf-8") if form else None
    headers = {
        "Accept": "application/json",
        "User-Agent": "local-oura-analysis/0.1",
    }
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
                parsed = json.loads(body) if body else {}
                return parsed, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < max_retries:
                retry_after = exc.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                print(f"Rate limited. Waiting {wait_seconds}s before retrying...", file=sys.stderr)
                time.sleep(wait_seconds)
                continue
            raise OuraError(f"{method} {url} failed with HTTP {exc.code}: {body[:800]}") from exc
        except urllib.error.URLError as exc:
            raise OuraError(f"{method} {url} failed: {exc}") from exc
    raise OuraError(f"{method} {url} failed after retries")


def build_auth_url(client_id: str, redirect_uri: str, scopes: list[str], state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def parse_scopes(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [scope for scope in value.replace(",", " ").split() if scope]


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict[str, Any]:
    payload, _ = request_json(
        "POST",
        TOKEN_URL,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    return payload


def refresh_token(client_id: str, client_secret: str, refresh_token_value: str) -> dict[str, Any]:
    payload, _ = request_json(
        "POST",
        TOKEN_URL,
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token_value,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    return payload


def parse_redirect_server(redirect_uri: str) -> tuple[str, int, str]:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise OuraError("For this local helper, use a localhost redirect URI such as http://localhost:8765/callback.")
    return parsed.hostname, parsed.port or 80, parsed.path or "/"


def command_auth(args: argparse.Namespace) -> None:
    client_id = credential_arg(args, "client_id", "OURA_CLIENT_ID")
    client_secret = credential_arg(args, "client_secret", "OURA_CLIENT_SECRET")
    redirect_uri = args.redirect_uri or os.environ.get("OURA_REDIRECT_URI") or "http://localhost:8765/callback"
    host, port, callback_path = parse_redirect_server(redirect_uri)
    state = secrets.token_urlsafe(24)
    scopes = args.scopes or parse_scopes(os.environ.get("OURA_SCOPES")) or DEFAULT_SCOPES
    received: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - inherited interface
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return
            if params.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid OAuth state. You can close this tab.")
                received["error"] = "invalid_state"
                return
            if "error" in params:
                received["error"] = params.get("error", ["unknown"])[0]
            elif "code" in params:
                received["code"] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body><h1>Oura authorization received</h1>"
                "<p>You can return to the terminal.</p></body></html>".encode("utf-8")
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    auth_url = build_auth_url(client_id, redirect_uri, scopes, state)
    print("Open this URL and approve access:")
    print(auth_url)
    print(f"\nWaiting for OAuth callback on {redirect_uri} ...")

    with HTTPServer((host, port), CallbackHandler) as server:
        server.timeout = args.timeout
        server.handle_request()

    if received.get("error"):
        raise OuraError(f"Oura authorization failed: {received['error']}")
    code = received.get("code")
    if not code:
        raise OuraError("Timed out before receiving an OAuth callback.")

    tokens = exchange_code(client_id, client_secret, redirect_uri, code)
    save_tokens(args.token_file, tokens)
    print(f"Saved OAuth tokens to {args.token_file}")


def token_is_expiring(tokens: dict[str, Any], skew_seconds: int = 120) -> bool:
    expires_at = tokens.get("expires_at")
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) + timedelta(seconds=skew_seconds) >= expiry


def get_access_token(args: argparse.Namespace) -> str:
    explicit = getattr(args, "access_token", None) or os.environ.get("OURA_ACCESS_TOKEN")
    if explicit:
        return explicit

    if not args.token_file.exists():
        raise OuraError(f"No token file found at {args.token_file}. Run the auth command first.")

    tokens = read_json(args.token_file)
    if token_is_expiring(tokens):
        refresh_value = tokens.get("refresh_token")
        if not refresh_value:
            raise OuraError("Access token is expiring and no refresh_token is available. Run auth again.")
        client_id = credential_arg(args, "client_id", "OURA_CLIENT_ID")
        client_secret = credential_arg(args, "client_secret", "OURA_CLIENT_SECRET")
        print("Refreshing expired Oura access token...")
        tokens = merge_refreshed_tokens(tokens, refresh_token(client_id, client_secret, refresh_value))
        save_tokens(args.token_file, tokens)
    access = tokens.get("access_token")
    if not access:
        raise OuraError(f"Token file {args.token_file} does not contain access_token.")
    return access


def date_chunks(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    if start > end:
        raise OuraError("start-date must be on or before end-date.")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=max_days - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def datetime_chunk_params(start: date, end: date, max_days: int) -> list[dict[str, str]]:
    params_list: list[dict[str, str]] = []
    for chunk_start, chunk_end in date_chunks(start, end, max_days):
        exclusive_end = chunk_end + timedelta(days=1)
        params_list.append(
            {
                "start_datetime": f"{chunk_start.isoformat()}T00:00:00",
                "end_datetime": f"{exclusive_end.isoformat()}T00:00:00",
            }
        )
    return params_list


def fetch_paginated(endpoint: str, params: dict[str, str], token: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    next_token: str | None = None
    page_number = 0
    while True:
        page_number += 1
        page_params = dict(params)
        if next_token:
            page_params["next_token"] = next_token
        payload, headers = request_json("GET", f"{API_BASE}/v2/usercollection/{endpoint}", params=page_params, token=token)
        data = payload.get("data")
        if isinstance(data, list):
            rows.extend(data)
        elif data is None and endpoint == "personal_info":
            rows.append(payload)
        elif isinstance(data, dict):
            rows.append(data)
        elif payload:
            rows.append(payload)
        pages.append(
            {
                "page": page_number,
                "params": page_params,
                "count": len(data) if isinstance(data, list) else (1 if payload else 0),
                "rate_limit": {
                    "limit": headers.get("X-RateLimit-Limit"),
                    "remaining": headers.get("X-RateLimit-Remaining"),
                    "reset": headers.get("X-RateLimit-Reset"),
                },
            }
        )
        next_token = payload.get("next_token")
        if not next_token:
            return rows, pages


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flat: dict[str, Any] = {}
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flat.update(flatten(child, child_prefix))
        return flat
    if isinstance(value, list):
        return {prefix: json.dumps(value, ensure_ascii=False, separators=(",", ":"))}
    return {prefix: value}


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred_keys = ["id", "day", "timestamp", "timestamp_unix"]
    primary_key = next((key for key in preferred_keys if any(key in row and row.get(key) not in {None, ""} for row in rows)), None)
    if not primary_key:
        return rows
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(primary_key, ""))
        if key:
            deduped[key] = row
    return sorted(deduped.values(), key=lambda row: str(row.get(primary_key, "")))


def read_flat_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], merge_existing: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat_rows = [flatten(row) for row in rows]
    if merge_existing:
        flat_rows = [*read_flat_csv(path), *flat_rows]
    flat_rows = dedupe_rows(flat_rows)
    fieldnames = sorted({key for row in flat_rows for key in row.keys()})
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            if fieldnames:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(flat_rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def command_refresh(args: argparse.Namespace) -> None:
    client_id = credential_arg(args, "client_id", "OURA_CLIENT_ID")
    client_secret = credential_arg(args, "client_secret", "OURA_CLIENT_SECRET")
    tokens = read_json(args.token_file)
    refresh_value = tokens.get("refresh_token")
    if not refresh_value:
        raise OuraError(f"No refresh_token in {args.token_file}. Run auth again.")
    refreshed = merge_refreshed_tokens(tokens, refresh_token(client_id, client_secret, refresh_value))
    save_tokens(args.token_file, refreshed)
    print(f"Refreshed OAuth tokens in {args.token_file}")


def endpoint_params(endpoint: Endpoint, start: date, end: date) -> list[dict[str, str]]:
    if endpoint.kind == "single":
        return [{}]
    if endpoint.kind == "datetime":
        return datetime_chunk_params(start, end, endpoint.max_days)
    return [
        {
            "start_date": chunk_start.isoformat(),
            "end_date": chunk_end.isoformat(),
        }
        for chunk_start, chunk_end in date_chunks(start, end, endpoint.max_days)
    ]


def sync_endpoint(endpoint_name: str, start: date, end: date, token: str) -> dict[str, Any]:
    endpoint = ENDPOINTS[endpoint_name]
    all_rows: list[dict[str, Any]] = []
    all_pages: list[dict[str, Any]] = []
    errors: list[str] = []
    all_params = endpoint_params(endpoint, start, end)
    for index, params in enumerate(all_params, start=1):
        if len(all_params) > 1:
            print(f"  chunk {index}/{len(all_params)} {params}", flush=True)
        try:
            rows, pages = fetch_paginated(endpoint.name, params, token)
            all_rows.extend(rows)
            all_pages.extend(pages)
        except OuraError as exc:
            errors.append(str(exc))
            print(f"Warning: {endpoint.name} failed: {exc}", file=sys.stderr)
    return {
        "endpoint": endpoint.name,
        "kind": endpoint.kind,
        "description": endpoint.description,
        "fetched_at": iso_now(),
        "range": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "pages": all_pages,
        "errors": errors,
        "data": all_rows,
    }


def command_sync(args: argparse.Namespace) -> None:
    token = get_access_token(args)
    sync_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_run_dir = args.raw_dir / sync_id
    skip_raw = getattr(args, "skip_raw", False)
    selected = args.endpoints or DEFAULT_ENDPOINTS
    manifest: dict[str, Any] = {
        "sync_id": sync_id,
        "started_at": iso_now(),
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "endpoints": selected,
        "raw_dir": None if skip_raw else str(raw_run_dir),
        "csv_dir": str(args.csv_dir),
        "results": {},
    }

    for endpoint_name in selected:
        print(f"Fetching {endpoint_name} ...", flush=True)
        payload = sync_endpoint(endpoint_name, args.start_date, args.end_date, token)
        rows = payload["data"]
        raw_path = raw_run_dir / f"{endpoint_name}.json"
        csv_path = args.csv_dir / f"{endpoint_name}.csv"
        if not skip_raw:
            write_json(raw_path, payload)
        csv_updated = not payload["errors"]
        if csv_updated:
            write_csv(csv_path, rows, merge_existing=True)
        else:
            print(f"  preserving existing CSV because {len(payload['errors'])} request(s) failed", file=sys.stderr)
        manifest["results"][endpoint_name] = {
            "rows": len(rows),
            "errors": payload["errors"],
            "raw_path": None if skip_raw else str(raw_path),
            "csv_path": str(csv_path),
            "csv_updated": csv_updated,
        }
        if csv_updated:
            print(f"  {len(rows)} fetched rows merged into {csv_path}", flush=True)

    manifest["finished_at"] = iso_now()
    if not skip_raw:
        write_json(raw_run_dir / "manifest.json", manifest)
        print(f"\nDone. Raw data: {raw_run_dir}", flush=True)
    else:
        print("\nDone. Raw snapshot skipped for rolling sync.", flush=True)
    print(f"CSV data: {args.csv_dir}", flush=True)


def command_endpoints(_: argparse.Namespace) -> None:
    for name in sorted(ENDPOINTS):
        endpoint = ENDPOINTS[name]
        print(f"{name:26s} {endpoint.kind:8s} {endpoint.description}")


def command_hash_token_file(args: argparse.Namespace) -> None:
    if not args.token_file.exists():
        raise OuraError(f"No token file found at {args.token_file}")
    digest = hashlib.sha256(args.token_file.read_bytes()).hexdigest()
    print(f"{args.token_file}: sha256:{digest}")


def add_common_token_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE, help="OAuth token JSON path")
    parser.add_argument("--client-id", default=None, help="Oura OAuth client ID; defaults to OURA_CLIENT_ID")
    parser.add_argument("--client-secret", default=None, help="Oura OAuth client secret; defaults to OURA_CLIENT_SECRET")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Oura API V2 data for local analysis.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth", help="Run local OAuth authorization and save tokens")
    add_common_token_args(auth)
    auth.add_argument("--redirect-uri", default=None, help="Defaults to OURA_REDIRECT_URI or localhost callback")
    auth.add_argument("--scopes", nargs="+", default=None, help="OAuth scopes to request")
    auth.add_argument("--timeout", type=int, default=300, help="Seconds to wait for the OAuth callback")
    auth.set_defaults(func=command_auth)

    refresh = subparsers.add_parser("refresh", help="Refresh saved OAuth tokens")
    add_common_token_args(refresh)
    refresh.set_defaults(func=command_refresh)

    sync = subparsers.add_parser("sync", help="Fetch historical Oura data into data/raw and data/csv")
    add_common_token_args(sync)
    sync.add_argument("--access-token", default=None, help="Use an access token directly instead of token-file")
    sync.add_argument("--start-date", required=True, type=parse_day, help="Start date, YYYY-MM-DD")
    sync.add_argument("--end-date", type=parse_day, default=today_utc(), help="End date, YYYY-MM-DD")
    sync.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="Directory for raw JSON snapshots")
    sync.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR, help="Directory for flattened CSV files")
    sync.add_argument("--endpoints", nargs="+", choices=sorted(ENDPOINTS), default=None, help="Endpoints to fetch")
    sync.add_argument("--skip-raw", action="store_true", help="Update merged CSVs without saving raw snapshots")
    sync.set_defaults(func=command_sync)

    endpoints = subparsers.add_parser("endpoints", help="List supported Oura API endpoints")
    endpoints.set_defaults(func=command_endpoints)

    hash_token = subparsers.add_parser("hash-token-file", help="Print token-file hash without showing secrets")
    add_common_token_args(hash_token)
    hash_token.set_defaults(func=command_hash_token_file)

    return parser


def main() -> int:
    load_env_file(PROJECT_ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except OuraError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
