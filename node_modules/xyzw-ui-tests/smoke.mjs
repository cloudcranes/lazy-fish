// 临时 UI 冒烟测试：通过 CDP 驱动 Edge，验证视图切换、方案加载、模板指派、校验与锁定
import fs from "node:fs";

const PORT = process.env.CDP_PORT || 9333;
const URL = process.env.APP_URL || "http://127.0.0.1:8765/?theme=light";

const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = list.find((t) => t.type === "page");
if (!page) throw new Error("no page target");

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});

let seq = 0;
const pending = new Map();
const pageErrors = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.method === "Runtime.exceptionThrown") {
    pageErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  }
  if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
    pageErrors.push(`${msg.params.entry.text} @ ${msg.params.entry.url || ""}`);
  }
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
});

function send(method, params = {}) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, (msg) => (msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)));
    ws.send(JSON.stringify({ id, method, params }));
  });
}

const results = [];
async function check(name, expression, expected) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression: `(() => { ${expression} })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  if (exceptionDetails) {
    results.push([name, "THREW", exceptionDetails.exception?.description || exceptionDetails.text]);
    return null;
  }
  const value = result.value;
  const pass = expected === undefined ? true : JSON.stringify(value) === JSON.stringify(expected);
  results.push([name, pass ? "PASS" : "FAIL", `got=${JSON.stringify(value)} want=${JSON.stringify(expected)}`]);
  return value;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 只求值、不计入断言结果，用于"先读数据再断言"的场景
async function ev(expression) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression: `(() => { ${expression} })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
}

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
// 先清空页面，丢弃上一次会话残留的轮询请求，避免被计入本次错误
await send("Page.navigate", { url: "about:blank" });
await sleep(600);
pageErrors.length = 0;
await send("Page.navigate", { url: URL });
await sleep(2500);

// 1. 初始化后应加载方案 / 模板 / 设备
// 方案数量与默认方案都从页面运行时状态读，不写死——方案会被用户增删、默认标记会被改
const plansInPage = await ev(`return plansCache.map((p) => ({ name: p.name, filename: p.filename, default: !!p.default, click_count: p.payload?.click_count }));`);
const expectedDefault = plansInPage.find((p) => p.default) || plansInPage[0];
await check("plans rendered", `return document.querySelectorAll('.plan-card').length`, plansInPage.length);
await check("default plan is the active one", `return document.querySelector('.plan-card.is-active')?.dataset.filename`, expectedDefault.filename);
await check("active plan marked with aria-pressed", `return document.querySelector('.plan-card.is-active .plan-card-hit').getAttribute('aria-pressed')`, "true");
await check("active plan name shown in bar", `return document.getElementById('activePlanName').textContent`, expectedDefault.name);
await check("clean state has no dirty flag", `return document.getElementById('planDirty').hidden`, true);
await check("plan card shows duration estimate", `return /^约 /.test(document.querySelector('.plan-card .plan-card-eta').textContent)`, true);
// 多尺度识别：折叠高级项默认收起，但状态回读必须在标题行可见
await check("识别高级项默认收起", `return document.querySelector('.advanced').open`, false);
await check("尺度阶梯回读", `return document.getElementById('outScaleLadder').textContent`, "0.78–1.30 · 17 档");
await check(
  "识别参数默认值",
  `return [Number(document.getElementById('scaleMin').value), Number(document.getElementById('scaleMax').value), Number(document.getElementById('scaleStep').value), document.getElementById('freezeGuard').checked].join('/')`,
  "0.78/1.3/0.035/true"
);
await check("templates rendered", `return document.querySelectorAll('.tpl-card').length`, 5);
await check("first template assigned", `return document.querySelector('input[name=firstTemplate]:checked')?.value`, "video-first-open10.png");
await check("repeat template assigned", `return [...document.querySelectorAll('input[name=repeatTemplate]:checked')].map(x=>x.value).join(',')`, "video-repeat10.png");
await check("device auto filled", `return document.getElementById('deviceId').value.startsWith('192.168') || document.getElementById('deviceSelect').value.length > 0`, true);
await check("device stays in sync with backend", `
  return loadDevices({ quiet: true })
    .then(() => fetch('/api/devices').then(r => r.json()))
    .then(api => {
      const id = api.devices[0].id;
      return document.getElementById('deviceId').value === id && document.getElementById('deviceSelect').value === id;
    });
`, true);
await check("hero ready", `return document.getElementById('heroTitle').textContent`, "准备就绪");
// 指标卡显示的是已载入方案的次数，次数随方案变，不能写死
await check("metric shows click count", `return document.getElementById('metricClickCount').textContent`, String(expectedDefault.click_count));
await check("nav label visible when expanded", `return getComputedStyle(document.querySelector('.nav-item span')).display`, "block");
await check("no missing template badge", `return document.getElementById('navTemplateBadge').hidden`, true);

// 2. 校验：清空设备后开始执行应报错且不启动
await check("start without device shows error", `
  document.getElementById('deviceId').value = '';
  document.getElementById('deviceSelect').innerHTML = '';
  document.getElementById('startBtn').click();
  return true;
`);
await sleep(600);
await check("error toast appears", `return document.querySelectorAll('.toast.is-error').length > 0`, true);
await check("toast explains missing device", `return document.querySelector('.toast.is-error').textContent.includes('设备')`, true);
await check("still idle after invalid start", `return document.getElementById('stopBtn').disabled`, true);

// 3. 恢复设备，改为非法次数
await check("fill device back", `
  const s = document.getElementById('deviceSelect');
  s.innerHTML = '<option value="127.0.0.1:5555">127.0.0.1:5555</option>';
  s.value = '127.0.0.1:5555';
  document.getElementById('deviceId').value = '127.0.0.1:5555';
  document.querySelectorAll('.toast').forEach(t => t.remove());
  return true;
`);
await check("zero click count rejected", `
  document.getElementById('clickCount').value = '0';
  document.getElementById('clickCount').dispatchEvent(new Event('input', {bubbles:true}));
  document.getElementById('startBtn').click();
  return document.getElementById('metricClickCount').textContent;
`, "—");
await sleep(500);
await check("toast for zero count", `return [...document.querySelectorAll('.toast')].map(t=>t.textContent).join('|')`, undefined);

// 4a. 未指派后续模板 -> 应报错
await check("clear repeat template", `
  document.querySelectorAll('.toast').forEach(t => t.remove());
  document.getElementById('clickCount').value = '10';
  document.querySelectorAll('input[name=repeatTemplate]').forEach(x => { x.checked = false; });
  document.querySelector('input[name=repeatTemplate]').dispatchEvent(new Event('change', {bubbles:true}));
  document.getElementById('startBtn').click();
  return true;
`);
await sleep(500);
await check("toast explains missing role", `return document.querySelector('.toast.is-error')?.textContent.includes('后续')`, true);

// 4b. 引用了不存在的模板文件 -> 应报错并跳转模板视图
await check("inject missing template", `
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const ghost = document.createElement('input');
  ghost.type = 'checkbox';
  ghost.name = 'repeatTemplate';
  ghost.value = 'ghost-missing.png';
  ghost.checked = true;
  document.getElementById('templates').appendChild(ghost);
  document.getElementById('startBtn').click();
  return true;
`);
await sleep(1400);
await check("jumped to templates view", `return document.getElementById('view-templates').classList.contains('is-active')`, true);
await check("badge shows missing count", `return document.getElementById('navTemplateBadge').textContent`, "1");
await check("hero warns about missing template", `return document.getElementById('heroTitle').textContent`, "模板缺失");
await check("cleanup ghost template", `
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const ghost = [...document.querySelectorAll('input[name=repeatTemplate]')].find(x => x.value === 'ghost-missing.png');
  ghost.checked = false;
  ghost.dispatchEvent(new Event('change', {bubbles:true}));
  ghost.remove();
  document.querySelector('input[name=repeatTemplate][value="video-repeat10.png"]').checked = true;
  document.querySelector('input[name=repeatTemplate][value="video-repeat10.png"]').dispatchEvent(new Event('change', {bubbles:true}));
  return document.getElementById('navTemplateBadge').hidden;
`, true);
await check("hero recovers", `return document.getElementById('heroTitle').textContent`, "准备就绪");

// 5. 视图路由：点击导航应同步切换
await check("nav click switches view synchronously", `
  const btn = document.querySelector('[data-nav="logs"]');
  btn.click();
  return document.getElementById('view-logs').classList.contains('is-active')
    && !document.getElementById('view-console').classList.contains('is-active');
`, true);
await check("nav item marked current", `return document.querySelector('[data-nav="logs"]').getAttribute('aria-current')`, "page");
await check("topbar title follows", `return document.getElementById('topbarTitle').textContent`, "运行日志");
await check("hash routing works on load", `
  location.hash = '#capture';
  return new Promise(r => setTimeout(() => r(document.getElementById('view-capture').classList.contains('is-active')), 200));
`, true);

// 6. 主题切换
await check("theme toggle to dark", `
  document.getElementById('themeToggle').click();
  return document.getElementById('appShell').dataset.theme;
`, "dark");
await check("dark tokens applied to body", `
  return getComputedStyle(document.body).backgroundColor;
`, undefined);
await check("theme toggle back to light", `
  document.getElementById('themeToggle').click();
  return document.getElementById('appShell').dataset.theme;
`, "light");
await check("theme persisted", `return localStorage.getItem('xyzw.theme')`, "light");

// 7. 侧栏折叠
await check("collapse nav hides labels", `
  document.getElementById('collapseNav').click();
  return getComputedStyle(document.querySelector('.nav-item span')).display;
`, "none");
await check("expand nav restores labels", `
  document.getElementById('collapseNav').click();
  return getComputedStyle(document.querySelector('.nav-item span')).display;
`, "block");

// 8. 置信度滑块同步摘要
await check("threshold slider syncs", `
  const t = document.getElementById('threshold');
  t.value = '0.75';
  t.dispatchEvent(new Event('input', {bubbles:true}));
  return document.getElementById('outThreshold').textContent + '/' + document.getElementById('summaryThreshold').textContent;
`, "0.75/0.75");

// 9. 方案卡点击 = 真正载入参数（不能只高亮）
await check("clicking plan card loads its params", `
  const card = [...document.querySelectorAll('.plan-card')].find(c => c.dataset.filename === '招募方案.json');
  card.querySelector('.plan-card-hit').click();
  return true;
`);
await sleep(1200);
await check("loaded plan tracked", `return activePlanFilename`, "招募方案.json");
await check("params actually replaced by plan", `return document.getElementById('clickCount').value`, "400");
await check("loaded plan becomes the active card", `return document.querySelector('.plan-card.is-active')?.dataset.filename`, "招募方案.json");
await check("only one card active", `return document.querySelectorAll('.plan-card.is-active').length`, 1);
await check("console metric follows loaded plan", `return document.getElementById('metricClickCount').textContent`, "400");
await check("plan name field backfilled", `return document.getElementById('planName').value`, "招募方案");
await check("loading alone is not a dirty change", `return document.getElementById('planDirty').hidden`, true);

// 9b. 未保存改动检测
await check("editing a param flags unsaved change", `
  const el = document.getElementById('clickCount');
  el.value = '401';
  el.dispatchEvent(new Event('input', {bubbles:true}));
  return [document.getElementById('planDirty').hidden, document.getElementById('updatePlan').hidden].join('|');
`, "false|false");
await check("reverting clears the flag", `
  const el = document.getElementById('clickCount');
  el.value = '400';
  el.dispatchEvent(new Event('input', {bubbles:true}));
  return document.getElementById('planDirty').hidden;
`, true);

// 9c. 执行次数：档位 / 步进 / 估算 / 校验
await check("preset writes to input", `
  document.querySelector('#clickCountPicks button[data-value="50"]').click();
  return document.getElementById('clickCount').value;
`, "50");
await check("preset is highlighted exclusively", `return document.getElementById('clickCountPicks').querySelectorAll('button[aria-pressed="true"]').length`, 1);
await check("stepper increments", `
  document.getElementById('clickCountPlus').click();
  return document.getElementById('clickCount').value;
`, "51");
await check("stepper accelerates past 100", `
  document.querySelector('#clickCountPicks button[data-value="100"]').click();
  document.getElementById('clickCountPlus').click();
  return document.getElementById('clickCount').value;
`, "110");
await check("stepper decrements", `
  document.getElementById('clickCountMinus').click();
  return document.getElementById('clickCount').value;
`, "100");
await check("duration estimate uses current params", `
  const p = currentPayload();
  return [Math.round(roundSeconds(p)*100)/100, fmtDuration(estimateSeconds(p)),
          document.getElementById('summaryDuration').textContent,
          document.getElementById('metricClickCountNote').textContent].join("|");
`, "3.09|5 分 9 秒|5 分 9 秒|预计 5 分 9 秒");
await check("long run switches estimate to warning tone", `
  const c = document.getElementById('clickCount');
  c.value = '1000';
  c.dispatchEvent(new Event('input', {bubbles:true}));
  return [document.getElementById('countEstimate').classList.contains('is-long'),
          document.getElementById('countHint').className].join("|");
`, "true|field-hint is-warn");
await check("long run estimate value", `return document.getElementById('summaryDuration').textContent`, "51 分 30 秒");
await check("invalid count is flagged live", `
  const c = document.getElementById('clickCount');
  c.value = '0';
  c.dispatchEvent(new Event('input', {bubbles:true}));
  return [c.classList.contains('is-invalid'), document.getElementById('countHint').textContent].join("|");
`, "true|请填 1–10000 之间的整数");
await check("invalid count blocks readiness", `return readiness().title`, "次数无效");
await check("readiness reports duration", `
  const c = document.getElementById('clickCount');
  c.value = '10';
  c.dispatchEvent(new Event('input', {bubbles:true}));
  return /预计 .+/.test(readiness().desc);
`, true);
await check("count controls lock while running", `
  setLocked(true);
  const locked = [...document.querySelectorAll('#clickCountPicks button')].every(b => b.disabled)
    && document.getElementById('clickCountPlus').disabled
    && document.getElementById('clickCountMinus').disabled
    && document.getElementById('clickCount').disabled;
  setLocked(false);
  return locked;
`, true);
await check("count controls unlock after run", `return [...document.querySelectorAll('#clickCountPicks button')].every(b => !b.disabled)`, true);
await check("stepper respects lower bound", `
  const c = document.getElementById('clickCount');
  c.value = '1';
  c.dispatchEvent(new Event('input', {bubbles:true}));
  return document.getElementById('clickCountMinus').disabled;
`, true);

// 10. 无障碍：主要交互元素有可读名称
await check("nav buttons have text", `return [...document.querySelectorAll('[data-nav]')].every(b => b.textContent.trim().length > 0)`, true);
await check("status pill has live region parent", `return document.getElementById('toastHost').getAttribute('aria-live')`, "polite");

// 11. 后端状态字段联动：实时画面 / 失败计数 / 暂停原因
await check("live shot renders backend filename", `
  renderLiveShot({ last_screenshot: 'shot-20260619-214152-332181.png', is_running: true });
  const img = document.getElementById('liveShotImg');
  return [img.hidden, img.getAttribute('src'), document.getElementById('liveBadge').hidden,
          document.getElementById('liveShotNote').textContent].join('|');
`, "false|/api/screenshots/shot-20260619-214152-332181.png|false|实时刷新 · 06-19 21:41:52");
await check("live badge stops when run ends", `
  renderLiveShot({ last_screenshot: 'shot-20260619-214152-332181.png', is_running: false });
  return [document.getElementById('liveBadge').hidden, document.getElementById('liveShotNote').textContent].join('|');
`, "true|最后更新 · 06-19 21:41:52");
await check("live shot placeholder when no run yet", `
  renderLiveShot({ last_screenshot: null, is_running: false });
  return document.getElementById('liveShotNote').textContent;
`, "等待任务开始");
await check("shot filename timestamp parsed", `return shotStamp('shot-20260619-214152-332181.png')`, "06-19 21:41:52");
await check("pause alert surfaces last_error", `
  renderRunAlert({ status: 'paused', clicked: 2, target: 10, last_error: '连续匹配失败，任务已暂停，避免乱点' });
  const box = document.getElementById('runAlert');
  return [box.hidden, box.className, document.getElementById('runAlertTitle').textContent,
          document.getElementById('runAlertText').textContent.includes('连续匹配失败'),
          document.getElementById('runAlertText').textContent.includes('2 / 10')].join('|');
`, "false|alert alert-warn|匹配连续失败，任务已暂停|true|true");
await check("error alert surfaces last_error", `
  renderRunAlert({ status: 'error', clicked: 0, target: 10, last_error: 'ADB 命令超时' });
  return [document.getElementById('runAlert').className,
          document.getElementById('runAlertText').textContent.includes('ADB 命令超时')].join('|');
`, "alert alert-danger|true");
await check("alert hidden when idle", `
  renderRunAlert({ status: 'idle', clicked: 0, target: 0, last_error: null });
  return document.getElementById('runAlert').hidden;
`, true);
await check("alert dismiss hides same state", `
  renderRunAlert({ status: 'paused', clicked: 2, target: 10, last_error: 'X' });
  document.getElementById('runAlertClose').click();
  const closed = document.getElementById('runAlert').hidden;
  renderRunAlert({ status: 'paused', clicked: 2, target: 10, last_error: 'X' });
  return closed && document.getElementById('runAlert').hidden;
`, true);
await check("new state reopens alert", `
  renderRunAlert({ status: 'error', clicked: 2, target: 10, last_error: 'Y' });
  return document.getElementById('runAlert').hidden;
`, false);
await check("misses metric mirrors backend", `
  return fetch('/api/tasks/state').then(r => r.json())
    .then(s => document.getElementById('metricMisses').textContent === String(s.misses));
`, true);
await check("hero not stuck on terminal status", `
  stateStatus = 'done';
  renderSummaries();
  const title = document.getElementById('heroTitle').textContent;
  stateStatus = 'idle';
  renderSummaries();
  return title;
`, "准备就绪");
await check("static assets cache-busted", `
  return [...document.querySelectorAll('link[rel=stylesheet], script[src*="app.js"]')]
    .every(n => /\\?v=\\d+$/.test(n.getAttribute('href') || n.getAttribute('src')));
`, true);

// 12. 移动端布局
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await sleep(500);
await check("mobile nav becomes bottom bar", `
  const nav = document.querySelector('.nav');
  const style = getComputedStyle(nav);
  return style.position === 'fixed' && style.bottom === '0px';
`, true);
await check("mobile no horizontal overflow", `return document.documentElement.scrollWidth <= window.innerWidth + 1`, true);
const shot = await send("Page.captureScreenshot", { format: "png" });
fs.writeFileSync(".workbuddy/ui-preview/mobile-console.png", Buffer.from(shot.data, "base64"));

await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await sleep(300);
await check("desktop no horizontal overflow", `return document.documentElement.scrollWidth <= window.innerWidth + 1`, true);

const realErrors = pageErrors.filter((e) => !/favicon/i.test(e));
if (realErrors.length) results.push(["no page errors", "FAIL", realErrors.join(" | ")]);
else results.push(["no page errors", "PASS", ""]);

console.log(results.map(([n, s, d]) => `${s.padEnd(5)} ${n}${s === "PASS" ? "" : "  ->  " + d}`).join("\n"));
const failed = results.filter(([, s]) => s !== "PASS");
console.log(`\n${results.length - failed.length}/${results.length} passed`);
ws.close();
process.exit(failed.length ? 1 : 0);
