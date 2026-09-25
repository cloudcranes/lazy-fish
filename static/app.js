/* ==========================================================================
   咸鱼之王助手 · 前端控制器
   设计系统驱动的单页控制台：视图路由、状态轮询、方案与模板管理、框选采样
   ========================================================================== */

const $ = (id) => document.getElementById(id);

const RECOMMENDED_FIRST = "video-first-open10.png";
const RECOMMENDED_REPEAT = "video-repeat10.png";
// 多尺度识别默认值，与后端 matcher.DEFAULT_* 保持一致（见 docs/RECOGNITION-RESEARCH.md）
const DEFAULT_SCALE_MIN = 0.78;
const DEFAULT_SCALE_MAX = 1.3;
const DEFAULT_SCALE_STEP = 0.035;
const DEFAULT_ROI_BAND = [0.55, 0.80];
const DEFAULT_FREEZE_THRESHOLD = 3.0;
const DEFAULT_FREEZE_MAX_WAITS = 4;
const DEFAULT_FREEZE_GUARD = true;
// 尺度档位上限：档位越多越慢，超过就说明参数配错了
const MAX_SCALE_STEPS = 60;
const STATUS_TEXT = {
  idle: "空闲",
  starting: "准备中",
  running: "运行中",
  done: "已完成",
  paused: "已暂停",
  error: "出错",
  stopped: "已停止",
};
const STATUS_TONE = {
  idle: "pill-idle",
  starting: "pill-info",
  running: "pill-success",
  done: "pill-success",
  paused: "pill-warn",
  error: "pill-danger",
  stopped: "pill-danger",
};
const RING_TONE = {
  idle: "status-tone-idle",
  starting: "status-tone-running",
  running: "status-tone-running",
  done: "status-tone-running",
  paused: "status-tone-paused",
  error: "status-tone-error",
  stopped: "status-tone-error",
};

const NAV_ITEMS = [
  ["console", "控制台"],
  ["plans", "方案与参数"],
  ["templates", "模板管理"],
  ["capture", "截图采样"],
  ["logs", "运行日志"],
];
const NAV_VIEWS = NAV_ITEMS.map(([key]) => key);

let templatesCache = [];
let plansCache = [];
let activePlanFilename = null;   // 真正已载入参数的方案（不是"浏览器里高亮的那张"）
let activePlanSnapshot = null;   // 载入时的参数指纹，用于判断是否有未保存改动
let lastPayload = null;
let selection = null;
let dragStart = null;
let lastLogs = [];
let stateStatus = "idle";
let shownShot = null;      // 已渲染的运行截图文件名，避免重复请求
let observedRun = false;   // 本次会话是否观察到任务真的跑起来过
let alertSignature = null; // 当前告警条对应的状态指纹
let dismissedAlert = null; // 已被用户关掉的告警指纹
let isLocked = false;      // 任务是否运行中（disabled 状态的唯一真相）

/* applyLock 命中集合：缓存 querySelectorAll 的结果，按 view 分桶。
   状态轮询每秒都会触发 applyLock，全树扫描的代价不低；
   模块作用域缓存 + 切视图 / 动态渲染后失效 = 节流不丢正确性。 */
let lockedNodes = new Set();
let lockedNodesByView = new Map();

/* 执行次数快捷档位 */
const COUNT_PRESETS = [10, 20, 30, 50, 100];
const COUNT_MIN = 1;
const COUNT_MAX = 10000;
/* 每轮固定开销：截图 + 模板匹配，实测约 0.3–0.4s */
const ROUND_OVERHEAD_SECONDS = 0.35;
/* 超过该时长视为长时挂机，给出提示 */
const LONG_RUN_SECONDS = 30 * 60;

/* --------------------------------------------------------------------------
   基础工具
   -------------------------------------------------------------------------- */

const TOAST_MAX = 5;

function toast(message, type = "error") {
  const host = $("toastHost");
  // 上限 5 条：超出时按 FIFO 把最早一条立刻移除，避免错误风暴把屏幕挤满。
  while (host.children.length >= TOAST_MAX) {
    const oldest = host.firstElementChild;
    if (!oldest) break;
    host.removeChild(oldest);
  }
  const item = document.createElement("div");
  item.className = `toast is-${type}`;
  item.setAttribute("role", "status");
  const bar = document.createElement("i");
  bar.className = "toast-bar";
  const text = document.createElement("span");
  text.textContent = friendlyError(message);
  item.append(bar, text);
  host.appendChild(item);
  const remove = () => {
    item.classList.add("is-out");
    setTimeout(() => item.remove(), 200);
  };
  setTimeout(remove, 3600);
}

function friendlyError(message) {
  try {
    const data = JSON.parse(message);
    return data.detail || message;
  } catch {
    return String(message).replace(/^Error:\s*/, "").trim();
  }
}

async function api(url, options = {}) {
  const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : res.json();
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

function fmtNum(value, digits = 1) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits).replace(/\.0$/, "") : "—";
}

/* --------------------------------------------------------------------------
   耗时估算
   后端单轮流程：截图 → 匹配 → 补点 → 等待 post_click_wait + jitter
   jitter 是 uniform(0, jitter)，数学期望为 jitter/2；识别开销取经验值。
   注意：这是"全部命中"的乐观值，匹配失败时走 interval 重试会额外变慢。
   -------------------------------------------------------------------------- */

function roundSeconds(p) {
  const taps = (Number(p.repeat_tap_count) || 0) * (Number(p.repeat_tap_gap_seconds) || 0);
  const wait = Number(p.post_click_wait_seconds) || 0;
  const jitter = (Number(p.jitter_seconds) || 0) / 2;
  return ROUND_OVERHEAD_SECONDS + taps + wait + jitter;
}

function estimateSeconds(p) {
  const count = Number(p.click_count) || 0;
  return count > 0 ? count * roundSeconds(p) : 0;
}

function fmtDuration(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分 ${secs} 秒`;
  return `${secs} 秒`;
}

/* 参数指纹：用于判断"当前表单"是否与已载入方案一致。
   device_id 属于环境状态（ADB 重连就会变），不计入参数改动。
   识别参数要按默认值归一化——改造前保存的方案文件里没有这些键，
   不补默认值会把它们全判成"有未保存改动"。 */
function planFingerprint(p) {
  return JSON.stringify({
    first: [...(p.first_template_names || [])].sort(),
    repeats: [...(p.template_names || [])].sort(),
    click_count: Number(p.click_count) || 0,
    interval: Number(p.interval_seconds),
    jitter: Number(p.jitter_seconds),
    wait: Number(p.post_click_wait_seconds),
    tap_count: Number(p.repeat_tap_count),
    tap_gap: Number(p.repeat_tap_gap_seconds),
    threshold: Number(p.threshold),
    max_misses: Number(p.max_misses),
    scale_min: Number(p.scale_min ?? DEFAULT_SCALE_MIN),
    scale_max: Number(p.scale_max ?? DEFAULT_SCALE_MAX),
    scale_step: Number(p.scale_step ?? DEFAULT_SCALE_STEP),
    roi_band: JSON.stringify(p.roi_band ?? DEFAULT_ROI_BAND),
    freeze_threshold: Number(p.freeze_threshold ?? DEFAULT_FREEZE_THRESHOLD),
    freeze_max_waits: Number(p.freeze_max_waits ?? DEFAULT_FREEZE_MAX_WAITS),
    freeze_guard: p.freeze_guard !== false,
  });
}

/* 与后端 MatchProfile.scales() 同一套规则：以 1.0 为锚点向两侧展开，再补上 range 两端。
   两边必须严格一致，否则标题行回读的档数会与实际扫描的档数不符。
   这里只用于回显"会扫多少档"，真实匹配始终以后端为准。 */
function round4(value) {
  return Math.round(value * 10000) / 10000;
}

function scaleLadder(p) {
  const min = Number(p?.scale_min ?? DEFAULT_SCALE_MIN);
  const max = Number(p?.scale_max ?? DEFAULT_SCALE_MAX);
  const step = Number(p?.scale_step ?? DEFAULT_SCALE_STEP);
  if (!Number.isFinite(min) || !Number.isFinite(max) || !Number.isFinite(step)) return null;
  if (!(step > 0) || max < min) return null;
  const values = new Set([round4(Math.min(max, Math.max(min, 1)))]);
  for (const direction of [1, -1]) {
    const span = direction > 0 ? max - 1 : 1 - min;
    if (span <= 0) continue;
    for (let index = 1; index <= Math.floor(span / step); index += 1) {
      values.add(round4(1 + direction * index * step));
    }
  }
  values.add(round4(min));
  values.add(round4(max));
  return [...values].filter((value) => value >= min - 1e-9 && value <= max + 1e-9).sort((a, b) => a - b);
}

function scaleLadderText(p) {
  const values = scaleLadder(p);
  if (!values) return "参数无效";
  return `${values[0].toFixed(2)}–${values[values.length - 1].toFixed(2)} · ${values.length} 档`;
}

function clampCount(value) {
  return Math.min(COUNT_MAX, Math.max(COUNT_MIN, value));
}

/* --------------------------------------------------------------------------
   视图路由
   -------------------------------------------------------------------------- */

function currentView() {
  return NAV_ITEMS.some(([key]) => `#${key}` === location.hash) ? location.hash.slice(1) : "console";
}

function setView(key) {
  NAV_ITEMS.forEach(([item]) => {
    const view = $(`view-${item}`);
    if (view) view.classList.toggle("is-active", item === key);
  });
  document.querySelectorAll("[data-nav]").forEach((btn) => {
    const isActive = btn.dataset.nav === key;
    btn.setAttribute("aria-current", isActive ? "page" : "false");
    btn.setAttribute("aria-selected", String(isActive));
    btn.tabIndex = isActive ? 0 : -1;
  });
  const label = NAV_ITEMS.find(([item]) => item === key)?.[1] || "控制台";
  $("topbarTitle").textContent = label;
  if (key === "logs") scrollLogs();
  // 切视图会让其他 view 的 [data-lock] 节点脱离可视 DOM，但 disabled 状态仍生效；
  // 重建缓存让下一次的 applyLock 直接走命中集合，不再扫全树。
  lockedNodes = new Set();
}

function focusNavItem(index) {
  const buttons = document.querySelectorAll("[data-nav]");
  if (!buttons.length) return;
  const next = (index + buttons.length) % buttons.length;
  const btn = buttons[next];
  location.hash = `#${btn.dataset.nav}`;
  setView(btn.dataset.nav);
  btn.focus();
}

function bindNavKeyboard() {
  const navList = document.querySelector(".nav");
  if (!navList) return;
  navList.setAttribute("role", "tablist");
  navList.setAttribute("aria-orientation", "vertical");
  document.querySelectorAll("[data-nav]").forEach((btn) => {
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-controls", `view-${btn.dataset.nav}`);
    const panel = $(`view-${btn.dataset.nav}`);
    if (panel) panel.setAttribute("aria-labelledby", btn.id || "");
  });
  navList.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const buttons = [...document.querySelectorAll("[data-nav]")];
    const current = buttons.indexOf(document.activeElement);
    if (current === -1) return;
    event.preventDefault();
    const direction = event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : -1;
    focusNavItem(current + direction);
  });
}

window.addEventListener("hashchange", () => setView(currentView()));

/* --------------------------------------------------------------------------
   外观偏好
   -------------------------------------------------------------------------- */

function initAppearance() {
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const fromUrl = new URLSearchParams(location.search).get("theme");
  const saved = fromUrl === "light" || fromUrl === "dark" ? fromUrl : localStorage.getItem("xyzw.theme");
  $("appShell").dataset.theme = saved || (prefersDark ? "dark" : "light");
  document.documentElement.dataset.theme = $("appShell").dataset.theme;
  if (localStorage.getItem("xyzw.navCollapsed") === "1") $("appShell").classList.add("nav-collapsed");
}

function toggleTheme() {
  const next = $("appShell").dataset.theme === "dark" ? "light" : "dark";
  $("appShell").dataset.theme = next;
  document.documentElement.dataset.theme = next;
  localStorage.setItem("xyzw.theme", next);
}

function toggleNav() {
  const collapsed = $("appShell").classList.toggle("nav-collapsed");
  localStorage.setItem("xyzw.navCollapsed", collapsed ? "1" : "0");
}

/* --------------------------------------------------------------------------
   参数读写
   -------------------------------------------------------------------------- */

function deviceId() {
  return $("deviceId").value.trim() || $("deviceSelect").value || null;
}

function captureDeviceId() {
  return $("captureDevice").value || deviceId();
}

function selectedFirst() {
  return document.querySelector('#templates input[name="firstTemplate"]:checked')?.value || null;
}

function selectedRepeats() {
  return [...document.querySelectorAll('#templates input[name="repeatTemplate"]:checked')].map((item) => item.value);
}

function currentPayload() {
  return {
    device_id: deviceId(),
    template_names: selectedRepeats(),
    first_template_names: selectedFirst() ? [selectedFirst()] : null,
    click_count: Number($("clickCount").value),
    interval_seconds: Number($("interval").value),
    jitter_seconds: Number($("jitter").value),
    post_click_wait_seconds: Number($("postClickWait").value),
    repeat_tap_count: Number($("repeatTapCount").value),
    repeat_tap_gap_seconds: Number($("repeatTapGap").value),
    threshold: Number($("threshold").value),
    max_misses: Number($("maxMisses").value),
    scale_min: Number($("scaleMin").value),
    scale_max: Number($("scaleMax").value),
    scale_step: Number($("scaleStep").value),
    freeze_guard: $("freezeGuard").checked,
  };
}

function applyPayload(payload) {
  const set = (id, value, fallback) => {
    if (value === undefined || value === null || value === "") return;
    const el = $(id);
    el.value = value;
  };
  set("clickCount", payload.click_count, 10);
  set("interval", payload.interval_seconds, 0.8);
  set("jitter", payload.jitter_seconds, 0.2);
  set("postClickWait", payload.post_click_wait_seconds, 4.8);
  set("repeatTapCount", payload.repeat_tap_count, 2);
  set("repeatTapGap", payload.repeat_tap_gap_seconds, 0.12);
  set("threshold", payload.threshold, 0.86);
  set("maxMisses", payload.max_misses, 8);
  set("scaleMin", payload.scale_min, DEFAULT_SCALE_MIN);
  set("scaleMax", payload.scale_max, DEFAULT_SCALE_MAX);
  set("scaleStep", payload.scale_step, DEFAULT_SCALE_STEP);
  $("freezeGuard").checked = payload.freeze_guard !== false;
  if (payload.device_id) $("deviceId").value = payload.device_id;
  if (payload.template_names?.length) {
    const names = new Set(payload.template_names);
    document.querySelectorAll('#templates input[name="repeatTemplate"]').forEach((el) => {
      el.checked = names.has(el.value);
    });
  }
  if (payload.first_template_names?.length) {
    const first = payload.first_template_names[0];
    const radio = document.querySelector(`#templates input[name="firstTemplate"][value="${CSS.escape(first)}"]`);
    if (radio) radio.checked = true;
  }
  refreshTemplateRoles();
  renderSummaries();
}

function recommendedPayload() {
  return {
    device_id: deviceId(),
    first_template_names: [RECOMMENDED_FIRST],
    template_names: [RECOMMENDED_REPEAT],
    click_count: 10,
    interval_seconds: 0.8,
    jitter_seconds: 0.2,
    post_click_wait_seconds: 4.8,
    repeat_tap_count: 2,
    repeat_tap_gap_seconds: 0.12,
    threshold: 0.86,
    max_misses: 8,
  };
}

function missingTemplates(payload) {
  const names = [...(payload.first_template_names || []), ...(payload.template_names || [])];
  return names.filter((name) => !templatesCache.includes(name));
}

function validatePayload(payload) {
  clearTemplateMissing();
  if (!payload.device_id) throw new Error("请先检测或填写目标设备");
  if (!payload.click_count || payload.click_count <= 0) throw new Error("十连次数必须大于 0");
  const ladder = scaleLadder(payload);
  if (!ladder) throw new Error("识别尺度无效：需满足 0 < 最小尺度 ≤ 最大尺度，且步长 > 0");
  if (ladder.length > MAX_SCALE_STEPS) {
    throw new Error(`识别尺度有 ${ladder.length} 档（上限 ${MAX_SCALE_STEPS}），请调大步长或收窄范围`);
  }
  if (!payload.first_template_names?.length) throw new Error("请选择一张「首次」模板");
  if (!payload.template_names.length) throw new Error("请至少选择一张「后续」模板");
  const missing = missingTemplates(payload);
  if (missing.length) {
    markTemplateMissing(missing);
    renderSummaries();
    setView("templates");
    throw new Error(`模板文件不存在：${missing.join("、")}`);
  }
}

/* --------------------------------------------------------------------------
   概要与摘要渲染
   -------------------------------------------------------------------------- */

function renderSummaries() {
  const p = currentPayload();
  const first = p.first_template_names?.[0] || "—";
  const repeats = p.template_names.length ? p.template_names.join("、") : "—";

  $("outPostClickWait").textContent = `${fmtNum(p.post_click_wait_seconds)} s`;
  $("outRepeatTapCount").textContent = p.repeat_tap_count ?? "—";
  $("outRepeatTapGap").textContent = `${fmtNum(p.repeat_tap_gap_seconds)} s`;
  $("outThreshold").textContent = p.threshold != null ? Number(p.threshold).toFixed(2) : "—";
  $("outInterval").textContent = `${fmtNum(p.interval_seconds)} s`;
  $("outJitter").textContent = `${fmtNum(p.jitter_seconds)} s`;
  $("outMaxMisses").textContent = p.max_misses ?? "—";
  const ladder = scaleLadder(p);
  $("outScaleLadder").textContent = scaleLadderText(p);
  $("outScaleLadder").classList.toggle("is-danger", ladder === null || ladder.length > MAX_SCALE_STEPS);

  $("summaryFirst").textContent = first;
  $("summaryRepeats").textContent = repeats;
  $("summaryFirst2").textContent = first;
  $("summaryRepeats2").textContent = repeats;
  $("summaryClickCount").textContent = p.click_count > 0 ? `${p.click_count} 次` : "—";
  $("summaryWait").textContent = `${fmtNum(p.post_click_wait_seconds)} 秒`;
  $("summaryRepeat").textContent = `${p.repeat_tap_count ?? "—"} 次 · 间隔 ${fmtNum(p.repeat_tap_gap_seconds)}s`;
  $("summaryThreshold").textContent = Number(p.threshold).toFixed(2);
  $("metricClickCount").textContent = p.click_count > 0 ? `${p.click_count}` : "—";

  renderCountState(p);
  renderActivePlan();

  const device = p.device_id || "未检测";
  $("currentDeviceText").textContent = device;
  $("topbarDevice").textContent = device === "未检测" ? "设备未连接" : `设备 ${device}`;
  const missingCount = missingTemplates(p).length;
  $("navTemplateBadge").hidden = missingCount === 0;
  $("navTemplateBadge").textContent = String(missingCount);
  refreshTemplateRoles();
  updateHeroCopy();
}

/* 列出所有可被提交前检查的数字输入 id 与对应可读名。
   checkValidity() 在提交前会扫一遍，把超界 / 类型不对的输入一次性报出来，
   比只挑 #clickCount 一个字段更全面（threshold / interval 等同样能打错）。 */
const NUMBER_FIELDS = [
  ["clickCount", "十连次数"],
  ["consoleCount", "十连次数（控制台镜像）"],
  ["postClickWait", "点后等待"],
  ["repeatTapCount", "后续连点次数"],
  ["repeatTapGap", "连点间隔"],
  ["interval", "匹配重试间隔"],
  ["jitter", "随机抖动"],
  ["maxMisses", "连续失败暂停"],
  ["scaleMin", "最小尺度"],
  ["scaleMax", "最大尺度"],
  ["scaleStep", "尺度步长"],
];

function invalidNumberFields() {
  const bad = [];
  for (const [id, label] of NUMBER_FIELDS) {
    const el = $(id);
    if (!el) continue;
    if (el.checkValidity && !el.checkValidity()) {
      bad.push({ id, label, message: el.validationMessage || "输入不合法" });
    }
  }
  return bad;
}

/* 执行次数的实时状态：档位高亮、校验、三处耗时估算。
   控制台与方案页用的是同一个 #clickCount 真相，这里负责把镜像同步过去。 */
function renderCountState(p = currentPayload()) {
  const raw = $("clickCount").value.trim();
  const count = Number(raw);
  const invalid = raw === "" || !Number.isFinite(count) || !Number.isInteger(count) || count < COUNT_MIN || count > COUNT_MAX;
  $("clickCount").classList.toggle("is-invalid", invalid);
  $("consoleCount").classList.toggle("is-invalid", invalid);
  // .checkValidity() 走浏览器原生约束：min/max/step 都会校验，错误时挂红框。
  const countEl = $("clickCount");
  if (countEl.checkValidity && !countEl.checkValidity()) {
    countEl.classList.add("is-invalid");
    $("consoleCount").classList.add("is-invalid");
  }

  for (const groupId of ["clickCountPicks", "consoleCountPicks"]) {
    for (const btn of $(groupId).querySelectorAll("button[data-value]")) {
      btn.setAttribute("aria-pressed", String(!invalid && Number(btn.dataset.value) === count));
    }
  }
  // 镜像输入框：用户正在里面打字时不要夺走输入焦点与光标
  const mirror = $("consoleCount");
  if (document.activeElement !== mirror && mirror.value !== $("clickCount").value) {
    mirror.value = $("clickCount").value;
  }
  applyLock();

  const seconds = invalid ? 0 : estimateSeconds(p);
  const duration = fmtDuration(seconds);
  const long = seconds >= LONG_RUN_SECONDS;

  const setEstimate = (el) => {
    if (!el) return;
    el.querySelector("b").textContent = invalid ? "—" : duration;
    el.classList.toggle("is-long", long && !invalid);
  };
  setEstimate($("countEstimate"));
  setEstimate($("plansEstimate"));
  setEstimate($("consoleEstimate"));
  $("summaryDuration").textContent = invalid ? "—" : duration;
  $("metricClickCountNote").textContent = invalid ? "单次运行完成多少轮" : `预计 ${duration}`;

  const hintText = invalid
    ? `请填 ${COUNT_MIN}–${COUNT_MAX} 之间的整数`
    : long
      ? "预计超过 30 分钟，运行中可随时停止；按全部命中估算，实际会更久"
      : "一轮宝箱活动算一次；按全部命中估算，实际会更久";
  const hintClass = invalid ? "field-hint is-danger" : long ? "field-hint is-warn" : "field-hint";
  for (const id of ["countHint", "consoleCountHint"]) {
    $(id).className = hintClass;
    $(id).textContent = hintText;
  }
}

function readiness() {
  const p = currentPayload();
  const missing = missingTemplates(p);
  if (missing.length) return { ready: false, title: "模板缺失", desc: `有 ${missing.length} 个模板文件不在模板目录中，请先到「模板管理」重新指派。` };
  if (!p.first_template_names?.length || !p.template_names.length) return { ready: false, title: "尚未指派模板", desc: "请到「模板管理」为按钮图片指定「首次」与「后续」角色。" };
  if (!p.device_id) return { ready: false, title: "尚未连接设备", desc: "请开启模拟器 ADB 调试后点击「检测设备」，再开始执行。" };
  if (!Number.isInteger(p.click_count) || p.click_count < COUNT_MIN || p.click_count > COUNT_MAX) {
    return { ready: false, title: "次数无效", desc: `十连次数需要是 ${COUNT_MIN}–${COUNT_MAX} 之间的整数，请回到「方案与参数」调整。` };
  }
  const seconds = estimateSeconds(p);
  return {
    ready: true,
    title: "准备就绪",
    desc: `${p.click_count} 轮十连 · 预计 ${fmtDuration(seconds)}（按全部命中估算）· 第 1 轮用首次模板，之后用后续模板。`,
  };
}

function updateHeroCopy() {
  if (stateStatus === "starting" || stateStatus === "running") return;
  const info = readiness();
  $("heroTitle").textContent = info.title;
  $("heroDesc").textContent = info.desc;
}

/* 方案状态：方案卡高亮 + 顶部状态条 + 控制台指标卡，
   全部以「已载入参数的方案」(activePlanFilename) 为唯一依据 */
function renderActivePlan() {
  const plan = plansCache.find((item) => item.filename === activePlanFilename) || null;
  const dirty = Boolean(plan) && planFingerprint(currentPayload()) !== activePlanSnapshot;

  document.querySelectorAll(".plan-card").forEach((card) => {
    const isActive = card.dataset.filename === activePlanFilename;
    card.classList.toggle("is-active", isActive);
    card.querySelector(".plan-card-hit")?.setAttribute("aria-pressed", String(isActive));
  });
  // 控制台芯片与控制台说明跟随同一个 activePlanFilename
  document.querySelectorAll(".plan-chip").forEach((chip) => {
    const isActive = chip.dataset.filename === activePlanFilename;
    chip.classList.toggle("is-active", isActive);
    chip.setAttribute("aria-pressed", String(isActive));
  });

  $("activePlanBadge").className = `pill ${plan ? "pill-brand" : "pill-idle"}`;
  $("activePlanBadge").textContent = plan ? (plan.default ? "默认方案" : "已载入") : "未载入";
  $("activePlanName").textContent = plan ? plan.name : "未选择方案";
  $("activePlanNote").textContent = plan
    ? (dirty ? "参数已改动，可覆盖保存回该方案" : "当前参数与该方案一致")
    : "点击下方任意方案卡即可载入其全部参数";
  $("planDirty").hidden = !dirty;
  const updateBtn = $("updatePlan");
  updateBtn.hidden = !dirty;
  updateBtn.disabled = isLocked;

  $("currentPlanName").textContent = plan ? plan.name : "未加载";
  $("planNote").textContent = plan ? `${dirty ? "有未保存改动 · " : ""}存档于 ${plan.filename}` : "尚未保存过方案";

  // 控制台运行配置卡：当前方案摘要 + 未保存改动标记
  $("consoleDirty").hidden = !dirty;
  $("consoleConfigNote").textContent = plan
    ? `${plan.name} · ${planMeta(plan)}`
    : (plansCache.length ? "选一个方案，调好次数即可开始" : "还没有方案，去「方案与参数」保存一组");
}

/* --------------------------------------------------------------------------
   设备
   -------------------------------------------------------------------------- */

async function loadDevices({ quiet = false } = {}) {
  const data = await api("/api/devices");
  const options = data.devices.map((device) => ({ value: device.id, label: `${device.id} (${device.status})` }));
  for (const id of ["deviceSelect", "captureDevice"]) {
    const el = $(id);
    const keep = el.value;
    el.innerHTML = "";
    for (const option of options) {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      el.appendChild(node);
    }
    if (keep && options.some((option) => option.value === keep)) el.value = keep;
    if (!el.value && options.length) el.value = options[0].value;
    if (!el.value && $("deviceId").value.trim()) el.value = $("deviceId").value.trim();
  }
  // 手动输入框跟随后端返回的选中设备，避免浏览器自动填充的旧值盖掉真实设备
  if (options.length) $("deviceId").value = $("deviceSelect").value;
  renderSummaries();
  if (!quiet) toast(data.devices.length ? "设备检测完成" : "未发现设备，请检查 ADB 连接", data.devices.length ? "success" : "warn");
}

/* --------------------------------------------------------------------------
   方案
   -------------------------------------------------------------------------- */

function planMeta(plan) {
  const p = plan.payload || {};
  const parts = [];
  if (p.click_count != null) parts.push(`${p.click_count} 连`);
  if (p.post_click_wait_seconds != null) parts.push(`等待 ${fmtNum(p.post_click_wait_seconds)}s`);
  if (p.threshold != null) parts.push(`置信度 ${Number(p.threshold).toFixed(2)}`);
  return parts.join(" · ") || "—";
}

function planEta(plan) {
  const p = plan.payload || {};
  if (!(p.click_count > 0)) return "";
  return `约 ${fmtDuration(estimateSeconds(p))}`;
}

function planRoles(plan) {
  const p = plan.payload || {};
  const first = p.first_template_names?.[0];
  const repeats = p.template_names || [];
  return `首次 <b>${first ? escapeHtml(first) : "未指定"}</b> · 后续 <b>${repeats.length} 张</b>`;
}

/* 控制台上的方案芯片：与方案卡同语义 —— 点一下即载入该方案的全部参数 */
function renderPlanChips() {
  const box = $("consolePlanChips");
  box.innerHTML = "";
  box.hidden = plansCache.length === 0;
  $("consolePlanEmpty").hidden = plansCache.length > 0;
  for (const plan of plansCache) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "plan-chip";
    chip.dataset.filename = plan.filename;
    chip.dataset.lock = "";
    chip.setAttribute("aria-pressed", "false");
    chip.title = planMeta(plan);
    chip.innerHTML = `
      <span class="plan-chip-name">
        <span>${escapeHtml(plan.name)}</span>
        ${plan.default ? '<span class="plan-chip-tag">默认</span>' : ""}
      </span>
      <span class="plan-chip-meta">${escapeHtml(planMeta(plan))}</span>`;
    box.appendChild(chip);
  }
  applyLock();
}

/* 方案卡的操作行。删除走**内联二次确认**，不用原生 confirm()：原生对话框在嵌入式
   预览面板（沙箱 iframe 未开 allow-modals）或用户勾过"阻止此页面创建更多对话框"之后，
   会静默返回 false —— 表现就是"点了删除没反应，且没有任何提示"。
   内联确认不依赖宿主环境，还能把危险操作留在操作对象身边。 */
function planActionsInner(plan, armed = false) {
  if (armed) {
    return `
        <span class="plan-card-confirm">
          <span class="plan-card-confirm-text">删除「${escapeHtml(plan.name)}」后将从列表移除</span>
          <span class="plan-card-confirm-buttons">
            <button class="btn btn-sm btn-danger" data-lock type="button" data-act="confirm-delete">确认删除</button>
            <button class="btn btn-sm btn-ghost" data-lock type="button" data-act="cancel-delete">取消</button>
          </span>
        </span>`;
  }
  return `
        <button class="btn btn-sm btn-ghost" type="button" data-act="default" ${plan.default ? "disabled" : ""}>
          ${plan.default ? "已是默认" : "设为默认"}
        </button>
        <button class="btn btn-sm btn-quiet-danger" data-lock type="button" data-act="delete">删除</button>`;
}

function renderPlanActions(card, plan, armed) {
  const row = card.querySelector(".plan-card-actions");
  row.classList.toggle("is-armed", armed);
  row.innerHTML = planActionsInner(plan, armed);
  card.classList.toggle("is-armed", armed);
  if (armed) row.querySelector('[data-act="confirm-delete"]')?.focus();
  // 新插入的节点要立刻跟上锁定状态，否则运行中会出现 1 秒的可点窗口
  lockedNodes = new Set();
  applyLock();
}

function armPlanDelete(card) {
  disarmAllPlanDeletes();
  const plan = plansCache.find((item) => item.filename === card.dataset.filename);
  if (plan) renderPlanActions(card, plan, true);
}

function disarmPlanDelete(card) {
  const plan = plansCache.find((item) => item.filename === card.dataset.filename);
  if (plan) renderPlanActions(card, plan, false);
}

function disarmAllPlanDeletes() {
  document.querySelectorAll(".plan-card.is-armed").forEach(disarmPlanDelete);
}

function renderPlans() {
  $("planCount").textContent = `${plansCache.length} 个`;
  $("planEmpty").hidden = plansCache.length > 0;
  renderPlanChips();
  const grid = $("planGrid");
  grid.innerHTML = "";
  // 方案卡是动态生成的：清缓存让 applyLock 重建命中集合
  lockedNodes = new Set();
  for (const plan of plansCache) {
    const card = document.createElement("div");
    card.className = "plan-card";
    card.dataset.filename = plan.filename;
    card.innerHTML = `
      <button class="plan-card-hit" type="button" data-act="load"
              aria-label="载入方案 ${escapeHtml(plan.name)}" aria-pressed="false"></button>
      <span class="plan-card-name">
        <span>${escapeHtml(plan.name)}</span>
        ${plan.default ? '<span class="pill pill-brand">默认</span>' : ""}
      </span>
      <span class="plan-card-meta">${planMeta(plan)}</span>
      <span class="plan-card-eta">${planEta(plan)}</span>
      <span class="plan-card-roles">${planRoles(plan)}</span>
      <span class="plan-card-actions">
${planActionsInner(plan)}
      </span>`;
    grid.appendChild(card);
  }
  if (activePlanFilename && !plansCache.some((item) => item.filename === activePlanFilename)) {
    const fallback = plansCache.find((item) => item.default) || plansCache[0];
    activePlanFilename = fallback ? fallback.filename : null;
    activePlanSnapshot = null;
  }
  renderActivePlan();
}

/* 点击方案卡 = 载入参数（这是用户最想做的事，不该只做高亮） */
async function selectPlan(filename, { quiet = false } = {}) {
  const plan = await api(`/api/plans/${encodeURIComponent(filename)}`);
  activePlanFilename = plan.filename;
  $("planName").value = plan.name;
  $("planDefault").checked = Boolean(plan.default);
  applyPayload(plan.payload || {});
  lastPayload = currentPayload();
  activePlanSnapshot = planFingerprint(plan.payload || {});
  renderActivePlan();
  if (!quiet) toast(`已载入「${plan.name}」的参数`, "success");
}

async function loadPlans() {
  const data = await api("/api/plans");
  plansCache = Array.isArray(data.plans) ? data.plans : [];
  renderPlans();
}

async function savePlan({ overwrite = false } = {}) {
  const plan = overwrite ? plansCache.find((item) => item.filename === activePlanFilename) : null;
  const name = plan ? plan.name : $("planName").value.trim();
  if (!name) throw new Error("请先填写方案名称");
  const payload = currentPayload();
  // 保存方案前同样做一次原生约束检查，否则后端 400 信息不会直达用户。
  const bad = invalidNumberFields();
  if (bad.length) {
    const summary = bad.map(({ label, message }) => `${label}：${message}`).join("；");
    const first = bad[0];
    toast(`参数超出范围——${summary}`, "error");
    $(first.id)?.focus();
    $(first.id)?.select?.();
    return;
  }
  validatePayload(payload);
  const isDefault = plan ? plan.default : $("planDefault").checked;
  const saved = await api("/api/plans", { method: "POST", body: JSON.stringify({ name, payload, default: isDefault }) });
  await loadPlans();
  activePlanFilename = saved.filename;
  activePlanSnapshot = planFingerprint(payload);
  renderActivePlan();
  toast(overwrite ? `已覆盖保存「${name}」` : `方案「${name}」已保存`, "success");
  return saved;
}

async function setDefaultPlan(filename) {
  const plan = plansCache.find((item) => item.filename === filename);
  if (!plan) throw new Error("方案不存在，请刷新后重试");
  await api("/api/plans", {
    method: "POST",
    body: JSON.stringify({ name: plan.name, payload: plan.payload, default: true }),
  });
  await loadPlans();
  toast(`「${plan.name}」已设为默认`, "success");
}

/* 二次确认已经内联在卡片上（见 planActionsInner），这里不再弹原生 confirm */
async function deletePlan(filename) {
  const plan = plansCache.find((item) => item.filename === filename);
  if (!plan) return;
  await api(`/api/plans/${encodeURIComponent(filename)}`, { method: "DELETE" });
  if (activePlanFilename === filename) {
    activePlanFilename = null;
    activePlanSnapshot = null;
  }
  await loadPlans();
  toast(`方案「${plan.name}」已删除`, "success");
}

async function applyRecommended() {
  await loadTemplates({ keepPayload: false });
  const payload = recommendedPayload();
  applyPayload(payload);
  $("planName").value = "推荐宝箱方案";
  const match = plansCache.find((item) => item.name === "推荐宝箱方案");
  activePlanFilename = match ? match.filename : null;
  // 套用后立即把"已载入快照"对齐到当前表单，避免把"点击推荐按钮"本身当作一次改动。
  // 推荐参数若与已保存的同名方案一致，dirty 自然为 false；若不一致，用户改完再保存即可。
  activePlanSnapshot = planFingerprint(currentPayload());
  lastPayload = currentPayload();
  renderActivePlan();
  toast("已套用推荐方案", "success");
  updateHeroCopy();
}

/* --------------------------------------------------------------------------
   模板
   -------------------------------------------------------------------------- */

async function loadTemplates({ keepPayload = true } = {}) {
  const data = await api("/api/templates");
  templatesCache = data.templates;
  const grid = $("templates");
  const payload = keepPayload ? currentPayload() : recommendedPayload();
  grid.innerHTML = "";
  if (!templatesCache.length) {
    grid.innerHTML = "";
    $("templatesEmpty").hidden = false;
  } else {
    $("templatesEmpty").hidden = true;
  }
  for (const name of templatesCache) {
    const recommended = name === RECOMMENDED_FIRST || name === RECOMMENDED_REPEAT;
    const card = document.createElement("div");
    card.className = "tpl-card";
    card.dataset.name = name;
    card.innerHTML = `
      ${recommended ? '<span class="tpl-flag">推荐</span>' : ""}
      <div class="tpl-thumb">
        <img src="/api/templates/${encodeURIComponent(name)}?t=${Date.now()}" alt="${escapeHtml(name)}" loading="lazy" />
      </div>
      <div class="tpl-name">${escapeHtml(name)}</div>
      <div class="tpl-roles">
        <label class="checkline"><input type="radio" name="firstTemplate" value="${escapeHtml(name)}" /> 首次</label>
        <label class="checkline"><input type="checkbox" name="repeatTemplate" value="${escapeHtml(name)}" /> 后续</label>
      </div>`;
    grid.appendChild(card);
  }
  grid.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.name === "firstTemplate" && input.checked) {
        grid.querySelectorAll('input[name="firstTemplate"]').forEach((other) => {
          if (other !== input) other.checked = false;
        });
      }
      refreshTemplateRoles();
      renderSummaries();
      updateHeroCopy();
    });
  });
  applyPayload(payload);
  clearTemplateMissing();
  renderTemplateOptions();
  // 模板网格是动态重建的：清掉缓存让下一次 applyLock 走命中集合
  lockedNodes = new Set();
  renderSummaries();
}

function refreshTemplateRoles() {
  const first = selectedFirst();
  const repeats = new Set(selectedRepeats());
  document.querySelectorAll(".tpl-card").forEach((card) => {
    const name = card.dataset.name;
    card.classList.toggle("is-first", first === name);
    card.classList.toggle("is-repeat", repeats.has(name));
  });
  const total = templatesCache.length;
  const picked = (first ? 1 : 0) + repeats.size;
  $("templateSummary").textContent = `共 ${total} 个 · 已指派 ${picked}`;
}

function markTemplateMissing(names) {
  for (const name of names) {
    document.querySelector(`.tpl-card[data-name="${CSS.escape(name)}"]`)?.classList.add("is-missing");
  }
}

function clearTemplateMissing() {
  document.querySelectorAll(".tpl-card.is-missing").forEach((card) => card.classList.remove("is-missing"));
}

/* --------------------------------------------------------------------------
   截图采样
   -------------------------------------------------------------------------- */

async function takeShot() {
  const data = await api("/api/screenshot", { method: "POST", body: JSON.stringify({ device_id: captureDeviceId() }) });
  const img = $("shot");
  img.hidden = false;
  $("shotEmpty").hidden = true;
  $("shotStage").classList.add("has-shot");
  img.src = `${data.url}?t=${Date.now()}`;
  $("shotSource").textContent = "手动截图 · 拖拽框选按钮区域";
  selection = null;
  dragStart = null;
  $("selection").style.display = "none";
  $("coordReadout").textContent = "未框选";
  toast("截图已更新，可拖拽框选按钮区域", "success");
}

function imageScale() {
  const img = $("shot");
  return { x: img.naturalWidth / img.clientWidth, y: img.naturalHeight / img.clientHeight };
}

function bindSelection() {
  const stage = $("shotStage");
  const img = $("shot");
  const box = $("selection");

  stage.addEventListener("mousedown", (event) => {
    if (!img.src || img.hidden) return;
    const rect = img.getBoundingClientRect();
    const startX = event.clientX - rect.left;
    const startY = event.clientY - rect.top;
    if (startX < 0 || startY < 0 || startX > rect.width || startY > rect.height) return;
    event.preventDefault();
    dragStart = { x: startX, y: startY };
    box.style.display = "block";
  });

  window.addEventListener("mousemove", (event) => {
    if (!dragStart) return;
    const rect = img.getBoundingClientRect();
    const x = Math.max(0, Math.min(event.clientX - rect.left, rect.width));
    const y = Math.max(0, Math.min(event.clientY - rect.top, rect.height));
    const left = Math.min(dragStart.x, x);
    const top = Math.min(dragStart.y, y);
    const width = Math.abs(x - dragStart.x);
    const height = Math.abs(y - dragStart.y);
    selection = { left, top, width, height };
    box.style.left = `${img.offsetLeft + left}px`;
    box.style.top = `${img.offsetTop + top}px`;
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;
    $("selectionSize").textContent = `${Math.round(width)} × ${Math.round(height)}`;
    const scale = imageScale();
    $("coordReadout").textContent = `起点 ${Math.round(left * scale.x)}, ${Math.round(top * scale.y)} · 尺寸 ${Math.round(width * scale.x)} × ${Math.round(height * scale.y)}`;
  });

  window.addEventListener("mouseup", () => { dragStart = null; });
}

/* 采样页：已有模板名下拉（重采样旧按钮时不必重新输入），以及"将覆盖"提示 */
function renderTemplateOptions() {
  const select = $("templatePick");
  const keep = select.value;
  select.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "＋ 新建模板";
  select.appendChild(blank);
  for (const name of templatesCache) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  if (keep && templatesCache.includes(keep)) select.value = keep;
  renderTemplateTarget();
}

/* 名称命中已有模板时，亮出「将覆盖」预览，并把主按钮改成覆盖语义 —— 覆盖是不可逆的，不能悄悄发生 */
function renderTemplateTarget() {
  const name = $("templateName").value.trim();
  const exists = Boolean(name) && templatesCache.includes(name);
  $("templateTarget").hidden = !exists;
  if (exists) {
    $("templateTargetImg").src = `/api/templates/${encodeURIComponent(name)}?t=${Date.now()}`;
    // 侧栏只有 260px，正文只放文件名，长解释放到按钮下方的常驻说明里
    $("templateTargetNote").textContent = name;
  }
  $("saveTemplate").textContent = exists ? "覆盖该模板" : "保存框选区域";
  const pick = $("templatePick");
  if (pick.value !== (exists ? name : "")) pick.value = exists ? name : "";
  return exists;
}

async function saveTemplate() {
  if (!selection || selection.width < 5 || selection.height < 5) throw new Error("请先在画面上拖拽框选按钮区域");
  const name = $("templateName").value.trim();
  if (!name) throw new Error("请先填写模板名称");
  const overwrite = templatesCache.includes(name);
  const scale = imageScale();
  await api("/api/templates/crop", {
    method: "POST",
    body: JSON.stringify({
      name,
      device_id: captureDeviceId(),
      x: Math.round(selection.left * scale.x),
      y: Math.round(selection.top * scale.y),
      width: Math.round(selection.width * scale.x),
      height: Math.round(selection.height * scale.y),
    }),
  });
  selection = null;
  $("selection").style.display = "none";
  $("coordReadout").textContent = "未框选";
  $("templateName").value = "";
  await loadTemplates();
  toast(overwrite ? `模板「${name}」已覆盖保存` : "模板已保存", "success");
}

/* --------------------------------------------------------------------------
   任务控制
   -------------------------------------------------------------------------- */

async function startTask() {
  const payload = currentPayload();
  // 提交前先用浏览器原生的 checkValidity() 把超界 / 类型不对的数字输入一次扫干净，
  // 比依赖后端 422 更早给反馈；同时按 id 定位到字段，反馈比后端的 detail 更具体。
  const bad = invalidNumberFields();
  if (bad.length) {
    const summary = bad.map(({ label, message }) => `${label}：${message}`).join("；");
    const first = bad[0];
    toast(`参数超出范围——${summary}`, "error");
    $(first.id)?.focus();
    $(first.id)?.select?.();
    return;
  }
  validatePayload(payload);
  lastPayload = payload;
  dismissedAlert = null;
  await api("/api/tasks/chest/start", { method: "POST", body: JSON.stringify(payload) });
  toast("任务已启动，3 秒后开始执行", "success");
}

async function stopTask() {
  await api("/api/tasks/stop", { method: "POST" });
  toast("已通知任务停止", "warn");
}

async function resumeTask() {
  if (!lastPayload) throw new Error("暂无上次方案，请先加载或保存一个方案");
  validatePayload(lastPayload);
  dismissedAlert = null;
  await api("/api/tasks/chest/start", { method: "POST", body: JSON.stringify(lastPayload) });
  toast("已按上次方案继续", "success");
}

async function clearStop() {
  await api("/api/stop-file", { method: "DELETE" });
  toast("STOP 文件已清除", "success");
}

function setLocked(isRunning) {
  isLocked = isRunning;
  applyLock();
}

/* disabled 状态的唯一归属地：派发锁定 + 步进器的边界禁用。
   若各处分散设置 disabled，运行中就会被后续 renderSummaries 覆盖掉。

   querySelectorAll 在每帧都执行一次成本不低（状态轮询每秒会触发多次）；
   把命中集合缓存到 module scope，按 view 重建——切视图时旧视图的 disabled
   节点会被 DOM 卸载，下次重建即可，无需每次全树扫描。 */
function rebuildLockedNodes() {
  const nodes = document.querySelectorAll(
    "[data-lock], #savePlan, #applyRecommended, #applyRecommended2, #loadDevices, #shotBtn, #saveTemplate, #refreshTemplates, #savePlanFromConsole, #resumeBtn, #runAlertAction, #updatePlan, #startBtn, #stopBtn, #clickCountMinus, #clickCountPlus, #consoleCountMinus, #consoleCountPlus",
  );
  lockedNodes = new Set(nodes);
  // 按 view 分桶：当前活跃 view 的节点全集 = lockedNodes ∪ 该 view 的节点
  for (const key of NAV_VIEWS) lockedNodesByView.set(key, new Set());
  for (const node of nodes) {
    const view = node.closest("[id^=\"view-\"]");
    if (!view) continue;
    const key = view.id.slice("view-".length);
    lockedNodesByView.get(key)?.add(node);
  }
}

function lockedNodesForView(viewKey) {
  // viewKey=null 表示「当前无活跃 view」，仍要保留 module-scope 节点（顶栏按钮等）
  if (!lockedNodesByView.size) rebuildLockedNodes();
  if (!viewKey) return lockedNodes;
  const viewNodes = lockedNodesByView.get(viewKey);
  if (!viewNodes) return lockedNodes;
  const union = new Set(lockedNodes);
  for (const node of viewNodes) union.add(node);
  return union;
}

function applyLock() {
  // 首次调用 / 视图切换后重建缓存（renderPlans / renderTemplates 等会替换 grid）
  if (!lockedNodes.size) rebuildLockedNodes();
  const nodes = lockedNodesForView(currentView());
  for (const item of nodes) {
    if (item.id === "runAlertAction" && !isLocked) {
      item.disabled = false;
      continue;
    }
    if (item.id === "updatePlan") {
      item.disabled = isLocked || item.hidden;
      continue;
    }
    item.disabled = isLocked;
  }
  $("startBtn").disabled = isLocked;
  $("stopBtn").disabled = !isLocked;

  if (isLocked) return;
  const count = Number($("clickCount").value);
  const valid = Number.isInteger(count);
  const atMin = valid && count <= COUNT_MIN;
  const atMax = valid && count >= COUNT_MAX;
  for (const [minus, plus] of [["clickCountMinus", "clickCountPlus"], ["consoleCountMinus", "consoleCountPlus"]]) {
    $(minus).disabled = atMin;
    $(plus).disabled = atMax;
  }
}

/* --------------------------------------------------------------------------
   状态轮询
   -------------------------------------------------------------------------- */

function formatLogs(lines) {
  return lines
    .map((line) => {
      const text = escapeHtml(line);
      const level = /\[(ERROR|错误)\]|失败|异常/.test(line) ? "lvl-error" : /警告|WARN/.test(line) ? "lvl-warn" : "";
      return level ? `<span class="${level}">${text}</span>` : text;
    })
    .join("\n");
}

function renderLogs(logs) {
  lastLogs = logs || [];
  const count = `${lastLogs.length} 行`;
  $("logLines").textContent = count;
  $("logPreviewLines").textContent = count;
  $("logs").innerHTML = formatLogs(lastLogs);
  $("logPreview").innerHTML = formatLogs(lastLogs.slice(-40));
  if (currentView() === "logs") scrollLogs();
}

function scrollLogs() {
  const el = $("logs");
  el.scrollTop = el.scrollHeight;
}

async function pollState() {
  try {
    const state = await api("/api/tasks/state");
    const status = state.status || "idle";
    const text = STATUS_TEXT[status] || status;
    const running = Boolean(state.is_running);
    stateStatus = status;
    if (running) observedRun = true;

    const pillClass = `pill ${STATUS_TONE[status] || "pill-idle"}`;
    $("topbarStatus").className = running ? `${pillClass} live` : pillClass;
    $("topbarStatus").lastElementChild.textContent = text;
    $("heroStatusPill").className = `pill ${STATUS_TONE[status] || "pill-idle"}`;
    $("heroStatusPill").lastElementChild.textContent = text;
    $("ringLabel").textContent = text;

    const percent = Number(state.progress_percent || 0);
    const target = Number(state.target || 0);
    const clicked = Number(state.clicked || 0);
    $("ringFill").style.strokeDashoffset = String(339.292 * (1 - percent / 100));
    $("ringFill").setAttribute("class", `ring-fill ${RING_TONE[status] || "status-tone-idle"}`);
    $("ringValue").textContent = `${clicked} / ${target}`;
    // 同步无障碍进度：aria-valuenow/min/max + 可读的 aria-valuetext，
    // 配合 progressbar 角色 + aria-live，屏幕阅读器能像 progress 一样播报。
    const ring = $("ring");
    ring.setAttribute("aria-valuemin", "0");
    ring.setAttribute("aria-valuemax", String(target));
    ring.setAttribute("aria-valuenow", String(clicked));
    ring.setAttribute("aria-valuetext", `${clicked} / ${target}`);
    $("startBtn").textContent = running ? "执行中…" : "开始执行";

    // 首屏只回答「现在能不能跑」；跑到哪了 / 为什么停了交给状态环与告警条
    if (running) {
      $("heroTitle").textContent = text;
      $("heroDesc").textContent = state.target
        ? `已执行 ${state.clicked || 0} / ${state.target} 轮十连${state.misses ? `，连续未匹配 ${state.misses} 次` : ""}。`
        : "正在初始化设备与模板…";
    } else {
      updateHeroCopy();
    }

    $("metricMisses").textContent = String(state.misses ?? 0);
    $("metricMissesNote").textContent = state.misses
      ? `已连续 ${state.misses} 次未匹配`
      : `${Number($("maxMisses").value) || 8} 次未匹配即暂停`;

    renderRunAlert(state);
    renderLastMatch(state.last_match);
    renderLiveShot(state);
    renderLogs(state.logs);
    setLocked(running);
  } catch (error) {
    renderLogs([friendlyError(error.message)]);
  }
}

function trimSentence(text) {
  return String(text).trim().replace(/[，,。.;；]+$/, "");
}

/* 任务结果与失败原因：这些字段一直由后端提供，此前前端完全没有消费 */
function renderRunAlert(state) {
  const box = $("runAlert");
  const status = state.status;
  const clicked = state.clicked || 0;
  const target = state.target || 0;
  const progress = `（进度 ${clicked} / ${target}）`;
  const reason = trimSentence(state.last_error || "");

  let tone = null;
  let title = "";
  let text = "";
  let action = "继续执行";

  if (status === "paused") {
    tone = "alert-warn";
    title = "匹配连续失败，任务已暂停";
    text = `${reason || "连续未匹配到模板，为避免乱点已自动暂停"}${progress}建议核对模板或调低置信度后继续。`;
  } else if (status === "error") {
    tone = "alert-danger";
    title = "任务异常中止";
    text = `${reason || "运行过程中出现异常"}${progress}请到「运行日志」查看完整记录。`;
  } else if (observedRun && status === "done") {
    tone = "alert-info";
    title = "本轮执行完成";
    text = `共完成 ${clicked} / ${target} 轮十连，设备已停止操作。`;
    action = "再跑一次";
  } else if (observedRun && status === "stopped") {
    tone = "alert-info";
    title = "已手动停止";
    text = `停在第 ${clicked} / ${target} 轮，可继续执行接着跑。`;
  }

  if (!tone) {
    box.hidden = true;
    return;
  }
  alertSignature = `${status}|${state.last_error || ""}|${clicked}|${target}`;
  if (dismissedAlert === alertSignature) {
    box.hidden = true;
    return;
  }
  box.className = `alert ${tone}`;
  box.hidden = false;
  $("runAlertTitle").textContent = title;
  $("runAlertText").textContent = text;
  $("runAlertAction").textContent = action;
  $("runAlertAction").hidden = !lastPayload;
}

/* 设备实时画面：接通 state.last_screenshot 与 /api/screenshots/{name} */
function shotStamp(name) {
  const match = /^shot-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(name || "");
  if (!match) return name || "";
  const [, , month, day, hour, minute, second] = match;
  return `${month}-${day} ${hour}:${minute}:${second}`;
}

function shotUrl(name, bust = false) {
  const base = `/api/screenshots/${encodeURIComponent(name)}`;
  return bust ? `${base}?t=${Date.now()}` : base;
}

/* last_screenshot 契约：string = 文件名（直接当 src 用）。
   旧实现把整张 PNG 字节塞进 state，前端收到 ArrayBuffer / TypedArray / 对象都按 fallback 处理，
   不再走 shotUrl，避免被某种"看似字符串但其实是 ArrayBuffer"的返回把 <img src> 当成 URL 渲染。 */
function pickScreenshotName(value) {
  if (typeof value !== "string" || !value) return null;
  // 文件名只允许 basename + png 扩展名，挡住一切目录穿越和奇怪字符
  if (value.includes("/") || value.includes("\\") || value.includes("..")) return null;
  if (!/\.png$/i.test(value)) return null;
  return value;
}

function renderLiveShot(state) {
  const name = pickScreenshotName(state.last_screenshot);
  const running = Boolean(state.is_running);
  const note = $("liveShotNote");

  $("liveBadge").hidden = !(running && name);
  if (!name) {
    note.textContent = "等待任务开始";
    return;
  }
  note.textContent = `${running ? "实时刷新" : "最后更新"} · ${shotStamp(name)}`;
  if (name === shownShot) return;
  shownShot = name;

  const img = $("liveShotImg");
  img.hidden = false;
  $("liveShotEmpty").hidden = true;
  img.src = shotUrl(name);

  if ($("followRun").checked) showRunFrame(name);
}

function showRunFrame(name) {
  const img = $("shot");
  img.hidden = false;
  $("shotEmpty").hidden = true;
  $("shotStage").classList.add("has-shot");
  img.src = shotUrl(name, true);
  $("shotSource").textContent = `跟随任务截图 · ${shotStamp(name)}`;
  selection = null;
  dragStart = null;
  $("selection").style.display = "none";
  $("coordReadout").textContent = "未框选";
}

function renderLastMatch(match) {
  if (!match) {
    $("lastMatchText").textContent = "暂无记录";
    $("lastMatchMeta").textContent = "—";
    return;
  }
  const center = match.center ? `${match.center[0]}, ${match.center[1]}` : "—";
  // 尺度直接反映"这一轮按钮渲染成了多大"，是排查识别问题最有用的一列
  const scale = Number(match.scale);
  const scaleText = Number.isFinite(scale) && scale > 0 ? ` · 尺度 ${scale.toFixed(2)}` : "";
  $("lastMatchText").textContent = match.template_name || "—";
  $("lastMatchMeta").textContent = `置信度 ${Number(match.confidence).toFixed(3)}${scaleText} · 坐标 ${center} · 连点 ${match.tap_count || 1}`;
}

/* --------------------------------------------------------------------------
   事件绑定
   -------------------------------------------------------------------------- */

/* 控制台的次数控件是 #clickCount 的镜像：档位与步进统一写回唯一真相 */
function bindCountControls() {
  const groups = [
    ["clickCountPicks", "clickCount", "clickCountMinus", "clickCountPlus"],
    ["consoleCountPicks", "consoleCount", "consoleCountMinus", "consoleCountPlus"],
  ];
  for (const [groupId, inputId, minusId, plusId] of groups) {
    $(groupId).addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-value]");
      if (btn) setClickCount(Number(btn.dataset.value));
    });
    $(minusId).addEventListener("click", () => nudgeCount(-1));
    $(plusId).addEventListener("click", () => nudgeCount(1));
    if (inputId === "clickCount") continue;
    $(inputId).addEventListener("input", () => {
      const canonical = $("clickCount");
      canonical.value = $(inputId).value;
      canonical.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
}

/* 程序化改动也要走 input 事件，保证依赖 renderSummaries 的下游全部刷新 */
function setClickCount(value) {
  const input = $("clickCount");
  input.value = String(clampCount(value));
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

/* 次数到 100 以后每步跨 10，避免按到手酸 */
function nudgeCount(direction) {
  const current = Number($("clickCount").value);
  const base = Number.isInteger(current) ? current : COUNT_MIN;
  setClickCount(base + direction * (base >= 100 ? 10 : 1));
}

function bindNav() {
  document.querySelectorAll("[data-nav]").forEach((btn) => {
    btn.addEventListener("click", () => {
      location.hash = `#${btn.dataset.nav}`;
      setView(btn.dataset.nav);
    });
  });
  $("themeToggle").addEventListener("click", toggleTheme);
  $("collapseNav").addEventListener("click", toggleNav);
}

function bindActions() {
  // Promise.resolve 包一层：同步 handler 返回 undefined，直接 .catch 会抛 TypeError
  const on = (id, fn) => $(id).addEventListener("click", () => {
    try {
      Promise.resolve(fn()).catch((error) => toast(error.message, "error"));
    } catch (error) {
      toast(error.message, "error");
    }
  });

  on("startBtn", startTask);
  on("stopBtn", stopTask);
  on("clearStop", clearStop);
  on("resumeBtn", resumeTask);
  on("runAlertAction", resumeTask);
  $("runAlertClose").addEventListener("click", () => {
    dismissedAlert = alertSignature;
    $("runAlert").hidden = true;
  });
  on("loadDevices", () => loadDevices());
  on("rescanDevice", () => loadDevices());
  on("shotBtn", takeShot);
  on("saveTemplate", saveTemplate);
  on("refreshTemplates", () => loadTemplates());
  on("applyRecommended", applyRecommended);
  on("applyRecommended2", applyRecommended);
  on("savePlan", savePlan);
  on("savePlanFromConsole", () => {
    setView("plans");
    $("planName").focus();
  });
  on("updatePlan", () => savePlan({ overwrite: true }));

  // 方案卡：整卡载入，卡内按钮走事件委托（卡片是动态生成的）
  $("planGrid").addEventListener("click", (event) => {
    const card = event.target.closest(".plan-card");
    if (!card) return;
    const act = event.target.closest("[data-act]")?.dataset.act || "load";
    const filename = card.dataset.filename;
    const fail = (error) => toast(error.message, "error");
    if (act === "delete") armPlanDelete(card);
    else if (act === "cancel-delete") disarmPlanDelete(card);
    else if (act === "confirm-delete") deletePlan(filename).catch(fail);
    else if (act === "default") setDefaultPlan(filename).catch(fail);
    else {
      // 点卡片其他位置 = 放弃删除并载入该方案
      disarmAllPlanDeletes();
      selectPlan(filename).catch(fail);
    }
  });
  // Esc 放弃删除确认
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") disarmAllPlanDeletes();
  });

  // 控制台方案芯片：与方案卡同语义，整块即载入（芯片动态生成，走事件委托）
  $("consolePlanChips").addEventListener("click", (event) => {
    const chip = event.target.closest(".plan-chip");
    if (!chip) return;
    selectPlan(chip.dataset.filename).catch((error) => toast(error.message, "error"));
  });
  on("gotoPlans", () => setView("plans"));
  on("gotoCapture", () => setView("capture"));
  on("gotoLogs", () => setView("logs"));
  on("copyLogs", async () => {
    try {
      await navigator.clipboard.writeText(lastLogs.join("\n"));
      toast("日志已复制", "success");
    } catch {
      toast("浏览器拒绝了剪贴板访问，请手动选择复制", "warn");
    }
  });
  on("clearLogView", () => {
    renderLogs([]);
    toast("视图已清空，服务端日志仍在累积", "warn");
  });

  ["clickCount", "postClickWait", "repeatTapCount", "repeatTapGap", "interval", "jitter", "maxMisses", "deviceId", "scaleMin", "scaleMax", "scaleStep"].forEach((id) => {
    $(id).addEventListener("input", renderSummaries);
  });
  $("freezeGuard").addEventListener("change", renderSummaries);
  $("threshold").addEventListener("input", renderSummaries);
  $("deviceSelect").addEventListener("change", renderSummaries);
  $("captureDevice").addEventListener("change", () => { $("deviceId").value = $("captureDevice").value; renderSummaries(); });
  // 采样页：选已有模板名 → 回填名称；手改名称 → 重新判定是否覆盖
  $("templatePick").addEventListener("change", () => {
    $("templateName").value = $("templatePick").value;
    renderTemplateTarget();
  });
  $("templateName").addEventListener("input", renderTemplateTarget);
  $("planDefault").addEventListener("change", renderSummaries);
  bindCountControls();
  $("followRun").addEventListener("change", () => {
    if ($("followRun").checked) {
      if (shownShot) showRunFrame(shownShot);
    } else {
      $("shotSource").textContent = "拖拽框选按钮区域";
    }
  });
}

/* --------------------------------------------------------------------------
   启动
   -------------------------------------------------------------------------- */

async function init() {
  initAppearance();
  bindNav();
  bindNavKeyboard();
  bindActions();
  bindSelection();
  setView(currentView());

  await loadTemplates({ keepPayload: false });
  await loadPlans();
  const defaultPlan = plansCache.find((plan) => plan.default) || plansCache[0];
  if (defaultPlan) await selectPlan(defaultPlan.filename, { quiet: true });
  else renderActivePlan();
  await loadDevices({ quiet: true }).catch(() => {});

  renderSummaries();
  applyLock();
  startStatePolling();
  pollState();
}

init().catch((error) => toast(error.message, "error"));

/* --------------------------------------------------------------------------
   状态轮询：requestIdleCallback 退避 + 页面 hidden 时停轮询
   --------------------------------------------------------------------------
   setInterval(pollState, 1000) 在后台标签里也会每秒敲一次 /api/tasks/state，
   既无谓耗 CPU 又无谓占网络。改用「任务在浏览器空闲时再安排下一次」：
   - 跑完一轮 pollState 后用 requestIdleCallback（兜底 setTimeout）预约下一轮；
   - 最小间隔 1000ms（正常运行仍是一秒一刷），间隔由 backoff 决定；
   - 任务进行中用 MIN_POLL_MS；空闲/backoff 时拉长，最长 POLL_MAX_GAP_MS；
   - 页面 hidden（visibilitychange）立刻停轮询，visible 后再起。 */

const POLL_MIN_MS = 1000;
const POLL_MAX_GAP_MS = 5000;

let pollTimer = null;
let pollIdleHandle = null;
let lastPollAt = 0;
let pollGap = POLL_MIN_MS;
let pollingActive = false;

function requestIdle(callback) {
  if (typeof window.requestIdleCallback === "function") {
    return window.requestIdleCallback(callback, { timeout: 500 });
  }
  return window.setTimeout(callback, 50);
}

function cancelIdleHandle(handle) {
  if (handle == null) return;
  if (typeof window.cancelIdleCallback === "function" && pollIdleHandle === handle) {
    window.cancelIdleCallback(handle);
  } else {
    clearTimeout(handle);
  }
  pollIdleHandle = null;
}

function scheduleNextPoll() {
  if (!pollingActive) return;
  cancelIdleHandle(pollIdleHandle);
  pollIdleHandle = requestIdle(async () => {
    pollIdleHandle = null;
    if (!pollingActive) return;
    lastPollAt = Date.now();
    await pollState();
    if (!pollingActive) return;
    if (stateStatus === "running" || stateStatus === "starting") {
      pollGap = POLL_MIN_MS;
    } else if (document.hidden) {
      pollGap = POLL_MAX_GAP_MS;
    } else {
      // 空闲态逐渐拉长间隔，1s → 5s 指数退避
      pollGap = Math.min(POLL_MAX_GAP_MS, Math.round(pollGap * 1.5));
    }
    scheduleNextPoll();
  });
}

function startStatePolling() {
  if (pollingActive) return;
  pollingActive = true;
  lastPollAt = 0;
  pollGap = POLL_MIN_MS;
  scheduleNextPoll();
}

function stopStatePolling() {
  pollingActive = false;
  cancelIdleHandle(pollIdleHandle);
  if (pollTimer != null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopStatePolling();
  } else {
    startStatePolling();
  }
});
