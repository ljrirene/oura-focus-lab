const state = {
  dashboard: null,
  dailyItems: [],
  sync: null,
  installPrompt: null,
  mediaRecorder: null,
  voiceChunks: [],
  voiceStartedAt: null,
  voiceTimer: null,
};

const metricLabels = {
  sleepHours: ["实际睡眠", "小时"],
  readiness: ["Readiness", "分"],
  sleepScore: ["睡眠分", "分"],
  hrv: ["HRV", "ms"],
};

const supplementPresets = [
  {
    id: "fish-oil",
    name: "鱼油",
    aliases: ["鱼油", "fish oil", "omega 3", "omega-3"],
    time: "19:00",
    note: "随正餐（含脂肪）；全天没有明确最佳时点",
  },
  {
    id: "magnesium-l-threonate",
    name: "苏糖酸镁",
    aliases: ["苏糖酸镁", "magnesium l-threonate", "magnesium threonate"],
    time: "21:00",
    note: "固定晚间便于执行；没有可靠的最佳时点，按产品标签",
  },
  {
    id: "coq10",
    name: "辅酶 Q10",
    aliases: ["辅酶q10", "辅酶 q10", "coq10", "coenzyme q10"],
    time: "09:00",
    note: "随早餐；少数人会失眠，因此不默认放在晚上",
  },
  {
    id: "b-complex",
    name: "B Complex",
    aliases: ["b complex", "b-complex", "维生素b族", "b族维生素", "复合维生素b"],
    time: "09:00",
    note: "随早餐；没有明确最佳时点",
  },
  {
    id: "multivitamin",
    name: "复合维生素",
    aliases: ["复合维生素", "multivitamin", "multivitamins"],
    time: "12:30",
    note: "随午餐；若含铁或钙，需核对与处方药的间隔",
  },
];

function $(selector) { return document.querySelector(selector); }
function $all(selector) { return Array.from(document.querySelectorAll(selector)); }
function displayNumber(value, suffix = "") { return value == null ? "--" : `${value}${suffix}`; }
function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function hoursToText(value) {
  if (value == null) return "--";
  const minutes = Math.round(value * 60);
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

function localDateText(isoDate) {
  if (!isoDate) return "--";
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short" }).format(new Date(`${isoDate}T12:00:00`));
}

function localISODate() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function localTimeText(value) {
  if (!value) return "--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(parsed);
}

function dailyItemTimeValue(value) {
  const text = String(value || "").trim();
  const exact = /^(\d{1,2}):(\d{2})$/.exec(text);
  if (exact) {
    const hours = Number(exact[1]);
    const minutes = Number(exact[2]);
    if (hours < 24 && minutes < 60) return hours * 60 + minutes;
  }
  const namedTimes = {
    "起床后": 7 * 60,
    "早上": 8 * 60,
    morning: 8 * 60,
    "早餐": 8 * 60 + 30,
    "上午": 10 * 60,
    "中午": 12 * 60,
    "午餐": 12 * 60 + 30,
    noon: 12 * 60 + 30,
    "下午": 15 * 60,
    afternoon: 15 * 60,
    "晚餐": 19 * 60,
    dinner: 19 * 60,
    "晚间": 21 * 60,
    evening: 21 * 60,
    "睡前": 23 * 60,
    bedtime: 23 * 60,
  };
  return namedTimes[text.toLowerCase()] ?? 25 * 60;
}

function sortDailyItems(items) {
  const categoryOrder = { medication: 0, supplement: 1, other: 2 };
  return [...items].sort((left, right) =>
    dailyItemTimeValue(left.time) - dailyItemTimeValue(right.time)
    || (categoryOrder[left.category] ?? 3) - (categoryOrder[right.category] ?? 3)
    || String(left.name || "").localeCompare(String(right.name || ""), "zh-CN")
  );
}

function normalizedSupplementName(value) {
  return String(value || "").toLowerCase().replace(/[\s_–—-]+/g, "");
}

function supplementPresetForName(name) {
  const normalized = normalizedSupplementName(name);
  return supplementPresets.find((preset) => preset.aliases.some((alias) => normalizedSupplementName(alias) === normalized)) || null;
}

function readinessBand(score) {
  if (score == null) return { key: "neutral", label: "NO SCORE" };
  if (score >= 85) return { key: "optimal", label: "OPTIMAL" };
  if (score >= 70) return { key: "good", label: "GOOD" };
  if (score >= 60) return { key: "fair", label: "FAIR" };
  return { key: "attention", label: "PAY ATTENTION" };
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function setTab(name) {
  $all(".tab").forEach((button) => button.classList.toggle("is-active", button.dataset.tab === name));
  $all(".view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === name));
  history.replaceState(null, "", `#${name}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderProfile(profile) {
  const targets = profile?.targets || {};
  $("#target-sleep").textContent = targets.sleepHours == null ? "未设置目标" : `目标 ${hoursToText(targets.sleepHours)}`;
  $("#target-readiness").textContent = targets.readiness == null ? "未设置目标" : `${targets.readiness}+ 执行完整计划`;
  $("#target-sleep-score").textContent = targets.sleepScore == null ? "未设置目标" : `${targets.sleepScore}+ 执行完整计划`;
  $("#target-hrv").textContent = targets.hrv == null ? "使用个人趋势" : `个人目标 ${targets.hrv} ms`;
}

function renderStatus(data) {
  const { status, latest, sleepPlan } = data;
  const pill = $("#status-pill");
  const band = readinessBand(latest.readiness);
  pill.textContent = latest.readiness == null ? band.label : `${band.label} · ${Math.round(latest.readiness)}`;
  pill.className = `status-pill ${band.key}`;
  const guidance = $("#today-guidance");
  guidance.className = `today-guidance ${status.key}`;
  const reasonTitles = {
    green: "为什么今天可以正常推进",
    amber: "为什么今天需要减量",
    red: "为什么今天要优先补觉",
  };
  $("#latest-title").textContent = reasonTitles[status.key] || "昨晚数据";
  $("#status-title").textContent = status.title;
  $("#status-reason").textContent = status.reason || "根据最新睡眠与恢复指标调整。";
  $("#guidance-work").textContent = status.work;
  $("#guidance-exercise").textContent = status.exercise;
  $("#guidance-evening").textContent = status.evening;
  $("#latest-date").textContent = localDateText(latest.date);
  $("#metric-sleep").textContent = hoursToText(latest.sleepHours);
  $("#metric-readiness").textContent = displayNumber(latest.readiness);
  $("#metric-sleep-score").textContent = displayNumber(latest.sleepScore);
  $("#metric-hrv").textContent = displayNumber(latest.hrv, " ms");
  $("#sleep-phase").textContent = `入睡过渡 · ${sleepPlan.label}`;
  $("#sleep-lights").textContent = `${sleepPlan.lightsOut} 关灯`;
  $("#sleep-wake").textContent = `${sleepPlan.wake} 起床`;
  $("#sleep-instruction").textContent = sleepPlan.instruction;
  $("#clock-wind-down").textContent = sleepPlan.windDown;
  $("#clock-bed").textContent = sleepPlan.bed;
  $("#clock-lights").textContent = sleepPlan.lightsOut;
  $("#clock-wake").textContent = sleepPlan.wake;
}

function renderDailyPlan(data) {
  const plan = data.todayPlan || {};
  const ai = data.ai || {};
  const isAI = plan.source === "ai";
  $("#plan-source").textContent = isAI ? `AI DAILY PLAN · ${plan.model || "MODEL"}` : "FALLBACK PLAN";
  $("#plan-generated").textContent = isAI
    ? localTimeText(plan.generatedAt)
    : ai.status === "generating"
      ? "正在生成"
      : ai.configured
        ? "规则备用"
        : "未连接 AI";
  const rows = Array.isArray(plan.timeline) ? plan.timeline : [];
  $("#daily-plan-timeline").innerHTML = rows.map((item) => `
    <div class="timeline-row"><time>${escapeHTML(item.time)}</time><strong>${escapeHTML(item.title)}</strong><p>${escapeHTML(item.detail)}</p></div>
  `).join("") || `<p class="empty-state">暂无今日计划。</p>`;
  $("#ai-status").textContent = `AI：${ai.message || "状态未知"}`;
  $("#ai-plan-button").disabled = ai.status === "generating" || !ai.configured;
  $("#ai-plan-button").textContent = ai.status === "generating" ? "正在生成" : "重新生成今日计划";
}

function renderComparisons(data) {
  $("#comparison-grid").innerHTML = Object.entries(data.comparisons).map(([key, metric]) => {
    const [label, unit] = metricLabels[key];
    const current = key === "sleepHours" ? hoursToText(metric.current) : displayNumber(metric.current, ` ${unit}`);
    const delta = metric.delta == null ? "无基线" : `${metric.delta > 0 ? "+" : ""}${metric.delta} ${unit}`;
    const quality = metric.delta == null ? "" : metric.delta >= 0 ? "positive" : "negative";
    return `<article class="comparison-card"><span>${label}</span><strong>${current}</strong><small class="${quality}">${delta} · 较前 14 天</small></article>`;
  }).join("");
}

function renderExperiment(review) {
  $("#review-count").textContent = `${review.completed} / ${review.target}`;
  $("#review-day").textContent = review.completed ? `已自动收集 ${review.completed} 晚 · ${review.dueDate} 完整复盘` : `${review.startDate} 起自动收集`;
  $("#review-verdict").textContent = review.verdict;
  $("#progress-fill").style.width = `${Math.min(100, review.completed / review.target * 100)}%`;
  $("#experiment-grid").innerHTML = Object.values(review.metrics).map((metric) => {
    const baseline = metric.key === "sleepHours" ? hoursToText(metric.baseline) : `${metric.baseline} ${metric.unit}`;
    const second = metric.trial == null ? baseline : metric.key === "sleepHours" ? hoursToText(metric.trial) : `${metric.trial} ${metric.unit}`;
    const delta = metric.delta == null ? "基线" : `${metric.delta > 0 ? "+" : ""}${metric.delta} ${metric.unit}`;
    const quality = metric.improved == null ? "" : metric.improved ? "positive" : "negative";
    return `<article class="comparison-card"><span>${metric.label}</span><strong>${second}</strong><small class="${quality}">${delta}${metric.delta == null ? " · 前 14 晚" : " · 较基线"}</small></article>`;
  }).join("");
}

function renderVoiceReview(data) {
  const review = data?.review;
  $("#voice-review-date").textContent = localDateText(data?.date || localISODate());
  $("#voice-review-result").hidden = !review;
  if (!review) return;
  $("#voice-review-summary").textContent = review.summary || "已完成复盘";
  $("#voice-review-blockers").innerHTML = (review.blockers || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")
    || "<li>未识别到明确阻碍</li>";
  $("#voice-review-tomorrow").innerHTML = (review.tomorrow || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")
    || "<li>保留今天有效的部分</li>";
  $("#voice-review-transcript").textContent = review.transcript || "";
  $("#voice-review-status").textContent = `已复盘 · ${localTimeText(review.createdAt)}`;
}

async function loadVoiceReview() {
  try {
    const response = await fetch(`/api/voice-review?date=${localISODate()}`, { cache: "no-store" });
    if (!response.ok) throw new Error("语音复盘读取失败");
    renderVoiceReview(await response.json());
  } catch (error) {
    $("#voice-review-status").textContent = error.message;
  }
}

function stopVoiceReview() {
  if (state.mediaRecorder?.state === "recording") state.mediaRecorder.stop();
}

async function submitVoiceReview(blob) {
  const button = $("#voice-review-button");
  button.disabled = true;
  $("#voice-button-label").textContent = "正在转写";
  $("#voice-review-status").textContent = "Whisper 转写并生成复盘中";
  try {
    const response = await fetch(`/api/voice-review?date=${localISODate()}`, {
      method: "POST",
      headers: { "Content-Type": blob.type || "audio/webm" },
      body: blob,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "语音复盘失败");
    renderVoiceReview(data);
    showToast("语音复盘已生成");
  } catch (error) {
    $("#voice-review-status").textContent = error.message;
    showToast(error.message);
  } finally {
    button.disabled = false;
    $("#voice-button-label").textContent = "重新语音复盘";
    $("#voice-timer").textContent = "最长 90 秒";
  }
}

async function toggleVoiceReview() {
  if (state.mediaRecorder?.state === "recording") {
    stopVoiceReview();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showToast("当前浏览器不支持录音");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const preferredTypes = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"];
    const mimeType = preferredTypes.find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    state.mediaRecorder = recorder;
    state.voiceChunks = [];
    state.voiceStartedAt = Date.now();
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) state.voiceChunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      window.clearInterval(state.voiceTimer);
      $("#voice-review-button").classList.remove("is-recording");
      const blob = new Blob(state.voiceChunks, { type: recorder.mimeType || "audio/webm" });
      state.mediaRecorder = null;
      submitVoiceReview(blob);
    });
    recorder.start(500);
    $("#voice-review-button").classList.add("is-recording");
    $("#voice-button-label").textContent = "结束并生成复盘";
    $("#voice-review-status").textContent = "正在录音";
    state.voiceTimer = window.setInterval(() => {
      const elapsed = Math.min(90, Math.floor((Date.now() - state.voiceStartedAt) / 1000));
      $("#voice-timer").textContent = `${elapsed}s / 90s`;
      if (elapsed >= 90) stopVoiceReview();
    }, 250);
  } catch (error) {
    $("#voice-review-status").textContent = "需要麦克风权限才能录音";
    showToast("请允许麦克风权限");
  }
}

function drawScoreChart(rows) {
  const svg = $("#score-chart");
  const width = 900;
  const height = 280;
  const pad = { left: 42, right: 18, top: 18, bottom: 34 };
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  if (!rows.length) {
    svg.innerHTML = `<text x="450" y="140" text-anchor="middle" fill="#637069">暂无趋势数据</text>`;
    return;
  }
  const x = (index) => pad.left + index * ((width - pad.left - pad.right) / Math.max(1, rows.length - 1));
  const y = (value) => pad.top + (100 - value) / 50 * (height - pad.top - pad.bottom);
  const line = (key) => rows.map((row, index) => row[key] == null ? null : `${x(index).toFixed(1)},${y(row[key]).toFixed(1)}`).filter(Boolean).join(" ");
  const grid = [50, 60, 70, 80, 90, 100].map((value) => `
    <line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}" stroke="#e0e5e1" stroke-width="1" />
    <text x="${pad.left - 8}" y="${y(value) + 4}" text-anchor="end" fill="#7b8780" font-size="11">${value}</text>
  `).join("");
  const labelIndexes = [...new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1])];
  const labels = labelIndexes.map((index) => `<text x="${x(index)}" y="${height - 8}" text-anchor="middle" fill="#7b8780" font-size="11">${rows[index].date.slice(5)}</text>`).join("");
  svg.innerHTML = `${grid}${labels}
    <polyline points="${line("readiness")}" fill="none" stroke="#14745d" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round" />
    <polyline points="${line("sleepScore")}" fill="none" stroke="#4f7fd3" stroke-width="3.5" stroke-dasharray="10 7" stroke-linejoin="round" stroke-linecap="round" />`;
}

function renderDashboard(data) {
  state.dashboard = data;
  if (data.error) {
    showToast(data.error);
    return;
  }
  $("#data-through").textContent = `Oura 至 ${data.dataThrough}`;
  $("#today-date").textContent = localDateText(localISODate());
  $("#sync-time").textContent = `可用睡眠数据截至 ${data.dataThrough}`;
  renderProfile(data.profile);
  renderStatus(data);
  renderDailyPlan(data);
  renderComparisons(data);
  renderExperiment(data.review);
  drawScoreChart(data.trend);
}

function renderSyncStatus(data) {
  const labels = {
    idle: "等待自动同步",
    syncing: "正在同步 Oura",
    success: "数据已更新",
    error: "同步失败，将自动重试",
    needs_auth: "需要重新连接 Oura",
  };
  const previousSuccess = state.sync?.lastSuccess;
  state.sync = data;
  $("#sync-status").textContent = labels[data.status] || data.message || "同步状态未知";
  const range = data.range ? `${data.range.start} 至 ${data.range.end}` : `最近 ${data.lookbackDays || 3} 天`;
  const timing = data.lastSuccess ? `上次成功 ${localTimeText(data.lastSuccess)}` : "尚未完成自动同步";
  $("#sync-detail").textContent = `${timing} · 每 ${data.intervalMinutes || 60} 分钟 · ${range}`;
  $("#sync-button").disabled = data.status === "syncing";
  $("#sync-button").textContent = data.status === "syncing" ? "正在同步" : "立即同步 Oura";
  const dot = $(".sync-dot");
  dot.className = `sync-dot ${data.status || "idle"}`;
  if (data.lastSuccess && previousSuccess && data.lastSuccess !== previousSuccess) {
    Promise.all([fetchDashboard(), loadDailyItems()]);
    showToast("Oura 最新数据已载入");
  }
}

async function fetchSyncStatus() {
  try {
    const response = await fetch("/api/sync-status", { cache: "no-store" });
    if (!response.ok) throw new Error("同步状态读取失败");
    renderSyncStatus(await response.json());
  } catch (error) {
    $("#sync-status").textContent = "当前离线";
    $("#sync-detail").textContent = "恢复网络后自动重试";
    $(".sync-dot").className = "sync-dot error";
  }
}

async function requestSync() {
  const button = $("#sync-button");
  button.disabled = true;
  button.textContent = "正在启动";
  try {
    const response = await fetch("/api/sync", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "无法启动同步");
    renderSyncStatus(data);
  } catch (error) {
    showToast(error.message);
    button.disabled = false;
    button.textContent = "立即同步 Oura";
  }
}

async function requestAIPlan() {
  const button = $("#ai-plan-button");
  button.disabled = true;
  button.textContent = "正在启动";
  try {
    const response = await fetch("/api/ai-plan", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "无法生成计划");
    showToast(data.message || "AI 计划已开始生成");
    window.setTimeout(() => fetchDashboard(), 2500);
  } catch (error) {
    showToast(error.message);
  }
}

function setupInstallPrompt() {
  const button = $("#install-button");
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  if (isIOS && !window.matchMedia("(display-mode: standalone)").matches) button.hidden = false;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installPrompt = event;
    button.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!state.installPrompt) {
      showToast("Safari：分享 → 添加到主屏幕");
      return;
    }
    state.installPrompt.prompt();
    await state.installPrompt.userChoice;
    state.installPrompt = null;
    button.hidden = true;
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
}

async function fetchDashboard(showMessage = false) {
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取本地数据");
    renderDashboard(await response.json());
    if (showMessage) showToast("已重新读取本地 Oura 数据");
  } catch (error) {
    showToast(error.message);
  }
}

function renderDailyItems(data) {
  const categoryLabels = { medication: "药品", supplement: "补剂", other: "其他" };
  const categoryClasses = { medication: "pigment-ultramarine", supplement: "pigment-viridian", other: "pigment-madder" };
  const items = sortDailyItems(Array.isArray(data.items) ? data.items : []);
  state.dailyItems = items;
  const groups = new Map();
  items.forEach((item) => {
    const key = item.time || "未定时间";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  $("#daily-item-options").innerHTML = [...groups.entries()].map(([time, groupItems]) => `
    <div class="supplement-group">
      <div class="supplement-time"><strong>${escapeHTML(time)}</strong><span>${groupItems.length} 项</span></div>
      <div class="supplement-options">${groupItems.map((item) => `
        <label class="supplement-choice ${categoryClasses[item.category] || categoryClasses.other}">
          <input type="checkbox" data-item-id="${escapeHTML(item.id)}" ${item.taken ? "checked" : ""}>
          <span>${escapeHTML(item.name)}</span>
        </label>
      `).join("")}</div>
    </div>
  `).join("");
  $("#daily-item-count").textContent = `${items.filter((item) => item.taken).length} / ${items.length}`;
  $("#daily-item-empty").hidden = items.length > 0;
  const recommendedUpdates = items.filter((item) => {
    const preset = item.category === "supplement" ? supplementPresetForName(item.name) : null;
    return preset && item.time !== preset.time;
  });
  const recommendationAction = recommendedUpdates.length ? `
    <div class="recommendation-action">
      <div><strong>${recommendedUpdates.length} 项补剂可使用推荐时间</strong><span>只更新时间，不覆盖你的备注。</span></div>
      <button type="button" data-apply-recommendations>应用全部</button>
    </div>
  ` : "";
  const configuredItems = items.map((item) => {
    const preset = item.category === "supplement" ? supplementPresetForName(item.name) : null;
    return `
      <div class="configured-item">
        <div><strong>${escapeHTML(item.name)}</strong><span>${escapeHTML(categoryLabels[item.category] || "其他")} · ${escapeHTML(item.time || "未定时间")}${item.note ? ` · ${escapeHTML(item.note)}` : ""}</span>${preset ? `<small>推荐 ${preset.time} · ${escapeHTML(preset.note)}</small>` : ""}</div>
        <button type="button" data-delete-item="${escapeHTML(item.id)}" aria-label="删除 ${escapeHTML(item.name)}">删除</button>
      </div>
    `;
  }).join("");
  $("#configured-items").innerHTML = recommendationAction + (configuredItems || `<p class="empty-state">尚未添加项目。</p>`);
}

async function loadDailyItems() {
  try {
    const response = await fetch(`/api/daily-items?date=${localISODate()}`, { cache: "no-store" });
    if (!response.ok) throw new Error("服用项目读取失败");
    renderDailyItems(await response.json());
  } catch (error) {
    showToast(error.message);
  }
}

async function toggleDailyItem(box) {
  box.disabled = true;
  try {
    const response = await fetch("/api/daily-item", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: localISODate(), id: box.dataset.itemId, taken: box.checked }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "保存失败");
    renderDailyItems(data);
  } catch (error) {
    box.checked = !box.checked;
    showToast(error.message);
  } finally {
    box.disabled = false;
  }
}

async function addDailyItem(event) {
  event.preventDefault();
  const payload = {
    name: $("#item-name").value,
    category: $("#item-category").value,
    time: $("#item-time").value,
    note: $("#item-note").value,
  };
  try {
    const response = await fetch("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "添加失败");
    event.currentTarget.reset();
    syncSupplementForm();
    await loadDailyItems();
    showToast("已添加到本地清单");
  } catch (error) {
    showToast(error.message);
  }
}

async function applySupplementRecommendations() {
  const updates = state.dailyItems.map((item) => ({
    item,
    preset: item.category === "supplement" ? supplementPresetForName(item.name) : null,
  })).filter(({ item, preset }) => preset && item.time !== preset.time);
  if (!updates.length) return;
  try {
    const responses = await Promise.all(updates.map(({ item, preset }) => fetch("/api/items", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: item.id, time: preset.time }),
    })));
    if (responses.some((response) => !response.ok)) throw new Error("推荐时间保存失败");
    await loadDailyItems();
    showToast(`已更新 ${updates.length} 项补剂时间`);
  } catch (error) {
    showToast(error.message);
  }
}

function syncSupplementForm(applySelected = false) {
  const isSupplement = $("#item-category").value === "supplement";
  $("#supplement-preset-field").hidden = !isSupplement;
  if (!isSupplement) {
    $("#timing-advice").hidden = true;
    return;
  }
  const selected = supplementPresets.find((preset) => preset.id === $("#supplement-preset").value);
  if (selected && applySelected) {
    $("#item-name").value = selected.name;
    $("#item-time").value = selected.time;
    $("#item-note").value = selected.note;
  }
  const preset = selected || supplementPresetForName($("#item-name").value);
  $("#timing-advice").hidden = !preset;
  $("#timing-advice").textContent = preset ? `推荐 ${preset.time} · ${preset.note}。时间可改，以产品标签和医生/药师建议为准。` : "";
}

async function downloadCalendar() {
  const button = $("#calendar-button");
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "正在生成…";
  try {
    const response = await fetch("/api/reminders.ics", { cache: "no-store" });
    if (!response.ok) throw new Error("日历生成失败");
    const blob = await response.blob();
    const file = new File([blob], "oura-focus-reminders.ics", { type: "text/calendar" });
    let shared = false;
    if (navigator.canShare?.({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: "Oura Focus Lab 提醒" });
        shared = true;
        showToast("已打开系统分享，请导入日历");
      } catch (error) {
        if (error.name === "AbortError") return;
      }
    }
    if (!shared) {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = file.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast("已下载 .ics，打开文件即可导入日历");
    }
  } catch (error) {
    showToast(error.message || "日历导出失败");
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

async function deleteDailyItem(itemId) {
  try {
    const response = await fetch(`/api/items?id=${encodeURIComponent(itemId)}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "删除失败");
    await loadDailyItems();
    showToast("已删除");
  } catch (error) {
    showToast(error.message);
  }
}

async function enableNotifications() {
  if (!("Notification" in window)) {
    showToast("当前浏览器不支持页面通知，请下载系统日历提醒");
    return;
  }
  const permission = await Notification.requestPermission();
  $("#notification-button").textContent = permission === "granted" ? "页面通知已开启" : "页面通知未开启";
  showToast(permission === "granted" ? "页面打开时会按时提醒" : "可改用系统日历提醒");
}

function checkReminderClock() {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const now = new Date();
  const time = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  const plan = state.dashboard?.sleepPlan;
  if (!plan) return;
  const reminders = {
    [plan.windDown]: ["降光", `${plan.bed} 上床。`],
    [plan.bed]: ["上床", `${plan.lightsOut} 关灯。`],
    [plan.wake]: ["起床", "开始你配置的晨间流程。"],
  };
  state.dailyItems.filter((item) => item.time).forEach((item) => {
    reminders[item.time] = [item.name, item.note || "打开 Oura Focus Lab 确认完成。"];
  });
  if (!reminders[time]) return;
  const key = `reminder-${now.toISOString().slice(0, 10)}-${time}`;
  if (localStorage.getItem(key)) return;
  new Notification(reminders[time][0], { body: reminders[time][1] });
  localStorage.setItem(key, "sent");
}

document.addEventListener("DOMContentLoaded", async () => {
  $all(".tab").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
  $("#refresh-button").addEventListener("click", () => fetchDashboard(true));
  $("#sync-button").addEventListener("click", requestSync);
  $("#ai-plan-button").addEventListener("click", requestAIPlan);
  $("#notification-button").addEventListener("click", enableNotifications);
  $("#calendar-button").addEventListener("click", downloadCalendar);
  $("#voice-review-button").addEventListener("click", toggleVoiceReview);
  $("#daily-item-options").addEventListener("change", (event) => {
    if (event.target.matches("[data-item-id]")) toggleDailyItem(event.target);
  });
  $("#item-form").addEventListener("submit", addDailyItem);
  $("#item-category").addEventListener("change", () => syncSupplementForm());
  $("#item-name").addEventListener("input", () => syncSupplementForm());
  $("#supplement-preset").addEventListener("change", () => syncSupplementForm(true));
  $("#configured-items").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-item]");
    if (button) deleteDailyItem(button.dataset.deleteItem);
    if (event.target.closest("[data-apply-recommendations]")) applySupplementRecommendations();
  });
  $("#supplement-preset").innerHTML += supplementPresets.map((preset) => `<option value="${preset.id}">${preset.name}</option>`).join("");
  syncSupplementForm();
  const initialTab = location.hash.slice(1);
  setTab(["today", "review", "data"].includes(initialTab) ? initialTab : "today");
  setupInstallPrompt();
  await Promise.all([fetchDashboard(), loadDailyItems(), fetchSyncStatus(), loadVoiceReview()]);
  checkReminderClock();
  window.setInterval(checkReminderClock, 30000);
  window.setInterval(fetchSyncStatus, 15 * 1000);
  window.setInterval(fetchDashboard, 15 * 60 * 1000);
});
