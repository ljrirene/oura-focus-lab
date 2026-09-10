# Deployment and Mobile Preview

## Recommended: Private HTTPS Preview

Keep the Python app, OAuth tokens, profile, and Oura CSV files on your computer. Set a strong `OURA_APP_PASSWORD`, start the app, then expose it through a temporary HTTPS tunnel:

```bash
OURA_APP_PASSWORD= python3 scripts/oura_app.py
python3 scripts/oura_app.py --port 8788 --no-auto-sync
cloudflared tunnel --url http://127.0.0.1:8788
```

The first process keeps localhost access password-free and owns the automatic sync loop. The second process is the password-protected tunnel origin and does not start a duplicate sync loop. Open the generated `https://...trycloudflare.com` URL on the phone and enter the configured Basic Auth credentials. The URL works only while the computer, both app processes, and tunnel remain online. Quick Tunnels are intended for testing and have no uptime guarantee.

On iPhone, use Safari's Share menu and choose **Add to Home Screen**. Browsers that support the install prompt show an **Install on phone** button in the Data view.

## GitHub Pages

GitHub Pages can host a static demo because it serves HTML, CSS, and JavaScript from a repository. It cannot run `scripts/oura_app.py`, refresh OAuth tokens, write private logs, or sync Oura data. Never commit real data or credentials to make a Pages demo work.

## Google Sites

Google Sites can embed an already hosted webpage, but it is not a Python application host. It adds no useful capability for this app and creates another sharing boundary to manage.

## Always-On Cloud Hosting

An always-on deployment needs all of the following:

- HTTPS and user authentication
- encrypted secret storage for OAuth credentials and tokens
- persistent private storage for the profile, logs, and merged data
- backup and token-revocation procedures

Do not use an ephemeral free web service unless persistent encrypted storage is configured. A temporary tunnel is simpler and keeps the health data local.
