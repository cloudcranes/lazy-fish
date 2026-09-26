// 方案选择 + 执行次数：方案卡载入是否真的生效、未保存改动检测、
// 次数档位/步进/耗时估算/实时校验、运行时锁定。非破坏性，不改动任何方案数据。
//
// 前置：Edge 以 --remote-debugging-port=9333 启动，后端跑在 8765（见 tests/ui/README.md）
const PORT = process.env.CDP_PORT || 9333;
const BASE = process.env.APP_BASE || "http://127.0.0.1:8765";
// 不要写死方案名：默认标记会被用户操作或其它测试改动，页面加载后从 plansCache 动态取
let DEFAULT_PLAN = process.env.DEFAULT_PLAN || null;
let OTHER_PLAN = process.env.OTHER_PLAN || null;

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
// 直接对已取到的值断言，避免二次求值时页面状态已变
function record(name, actual, expected) {
  // expected === undefined 表示只取值不断言
  const pass = expected === undefined || JSON.stringify(actual) === JSON.stringify(expected);
  results.push([name, pass ? "PASS" : "FAIL", `got=${JSON.stringify(actual)} want=${JSON.stringify(expected)}`]);
  console.log(`${pass ? "PASS " : "FAIL "} ${name}${pass ? "" : `\n      got=${JSON.stringify(actual)}\n      want=${JSON.stringify(expected)}`}`);
}
async function check(name, expression, expected) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression: `(() => { ${expression} })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  if (exceptionDetails) {
    results.push([name, "THREW", exceptionDetails.exception?.description || exceptionDetails.text]);
    console.log(`THREW ${name}\n      ${exceptionDetails.exception?.description || exceptionDetails.text}`);
    return null;
  }
  record(name, result.value, expected);
  return result.value;
}
/* 只求值、不入断言列表 */
async function ev(expression) {
  const { result } = await send("Runtime.evaluate", {
    expression: `(() => { ${expression} })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  return result.value;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });
// 先离开再进入：同 URL（含 hash）的 navigate 不会真正重新加载，会残留上一次的内存状态
await send("Page.navigate", { url: "about:blank" });
await sleep(600);
pageErrors.length = 0;
await send("Page.navigate", { url: `${BASE}/?theme=light&t=${Date.now()}#plans` });
await sleep(3000);

console.log("--- 1. 初始态：默认方案应已真正载入 ---");
const planIndex = await ev(`return plansCache.map((p) => ({ name: p.name, filename: p.filename, default: !!p.default }));`);
const fallback = planIndex.find((p) => p.default) || planIndex[0];
DEFAULT_PLAN = DEFAULT_PLAN || fallback.filename;
OTHER_PLAN = OTHER_PLAN || (planIndex.find((p) => p.filename !== DEFAULT_PLAN) || fallback).filename;
console.log(`  方案 ${planIndex.length} 个：默认 ${DEFAULT_PLAN}，对照 ${OTHER_PLAN}`);

const initial = await ev(`return {
  active: activePlanFilename,
  badge: document.getElementById('activePlanBadge').textContent,
  clickCount: document.getElementById('clickCount').value,
  dirtyHidden: document.getElementById('planDirty').hidden,
  activeCards: document.querySelectorAll('.plan-card.is-active').length,
  cards: document.querySelectorAll('.plan-card').length,
};`);
console.log("  初始:", JSON.stringify(initial));
await check("默认方案已标记为使用中", `return activePlanFilename`, DEFAULT_PLAN);
await check("有且仅有一张卡处于使用中", `return document.querySelectorAll('.plan-card.is-active').length`, 1);
await check("初始无未保存改动", `return document.getElementById('planDirty').hidden`, true);
await check("方案卡显示耗时预估", `return /^约 /.test(document.querySelector('.plan-card .plan-card-eta').textContent)`, true);

console.log("\n--- 2. 点击方案卡：参数必须真的被载入 ---");
await ev(`
  document.querySelector('.plan-card[data-filename="${OTHER_PLAN}"] .plan-card-hit').click();
  return true;
`);
await sleep(1400);
await check("已载入方案切换", `return activePlanFilename`, OTHER_PLAN);
await check("参数真的被载入", `return document.getElementById('clickCount').value`, "400");
await check("控制台指标同步", `return document.getElementById('metricClickCount').textContent`, "400");
await check("执行概要同步", `return document.getElementById('summaryClickCount').textContent`, "400 次");
await check("方案名回填", `return document.getElementById('planName').value`, "招募方案");
await check("载入后不算未保存改动", `return document.getElementById('planDirty').hidden`, true);
await check("无对应档位时不误高亮", `return [...document.querySelectorAll('#clickCountPicks button')].filter(b => b.getAttribute('aria-pressed') === 'true').length`, 0);

console.log("\n--- 3. 未保存改动检测 ---");
await ev(`const el = document.getElementById('clickCount'); el.value = '401'; el.dispatchEvent(new Event('input', {bubbles:true})); return true;`);
await sleep(300);
await check("改动后出现提示", `return document.getElementById('planDirty').hidden`, false);
await check("出现覆盖保存按钮", `return document.getElementById('updatePlan').hidden`, false);
await check("控制台同步标注", `return document.getElementById('planNote').textContent.includes('未保存改动')`, true);
await ev(`const el = document.getElementById('clickCount'); el.value = '400'; el.dispatchEvent(new Event('input', {bubbles:true})); return true;`);
await sleep(300);
await check("改回原值后提示消失", `return document.getElementById('planDirty').hidden`, true);

console.log("\n--- 4. 执行次数：快捷档位 ---");
await ev(`document.querySelector('#clickCountPicks button[data-value="50"]').click(); return true;`);
await sleep(250);
await check("档位写入输入框", `return document.getElementById('clickCount').value`, "50");
await check("档位高亮", `return document.querySelector('#clickCountPicks button[data-value="50"]').getAttribute('aria-pressed')`, "true");
await check("档位互斥高亮", `return document.getElementById('clickCountPicks').querySelectorAll('button[aria-pressed="true"]').length`, 1);

console.log("\n--- 5. 执行次数：步进器（含大数值加速） ---");
await ev(`document.getElementById('clickCountPlus').click(); return true;`);
await sleep(200);
await check("50 → 51", `return document.getElementById('clickCount').value`, "51");
await ev(`
  document.querySelector('#clickCountPicks button[data-value="100"]').click();
  document.getElementById('clickCountPlus').click();
  return true;
`);
await sleep(200);
await check("100 → 110（步长升为 10）", `return document.getElementById('clickCount').value`, "110");
await ev(`document.getElementById('clickCountMinus').click(); return true;`);
await sleep(200);
await check("110 → 100", `return document.getElementById('clickCount').value`, "100");

console.log("\n--- 6. 耗时估算：随参数联动 ---");
const estimate = await ev(`const p = currentPayload(); return {
  round: Math.round(roundSeconds(p) * 100) / 100,
  seconds: Math.round(estimateSeconds(p)),
  text: document.getElementById('countEstimate').querySelector('b').textContent,
  summary: document.getElementById('summaryDuration').textContent,
  metricNote: document.getElementById('metricClickCountNote').textContent,
  plans: document.getElementById('plansEstimate').querySelector('b').textContent,
};`);
console.log("  估算:", JSON.stringify(estimate));
await check("单轮秒数 = 0.35 + 补点 + 等待 + jitter/2", `return Math.round(roundSeconds(currentPayload()) * 100) / 100`, 3.09);
await check("总耗时 ≈ 轮数 × 单轮", `return Math.round(estimateSeconds(currentPayload()))`, 309);
await check("格式化输出", `return document.getElementById('countEstimate').querySelector('b').textContent`, "5 分 9 秒");
await check("概要同步", `return document.getElementById('summaryDuration').textContent`, "5 分 9 秒");
await check("指标卡备注同步", `return document.getElementById('metricClickCountNote').textContent`, "预计 5 分 9 秒");
await check("方案页同步", `return document.getElementById('plansEstimate').querySelector('b').textContent`, "5 分 9 秒");

console.log("\n--- 7. 长时运行提示 ---");
await ev(`
  const c = document.getElementById('clickCount');
  c.value = '1000';
  c.dispatchEvent(new Event('input', {bubbles:true}));
  return true;
`);
await sleep(250);
await check("估算 >30min 切换告警样式", `return document.getElementById('countEstimate').classList.contains('is-long')`, true);
await check("提示文案转警示", `return document.getElementById('countHint').className`, "field-hint is-warn");

console.log("\n--- 8. 次数校验：非法值实时反馈 ---");
await ev(`const c = document.getElementById('clickCount'); c.value = '0'; c.dispatchEvent(new Event('input', {bubbles:true})); return true;`);
await sleep(250);
await check("0 标红", `return document.getElementById('clickCount').classList.contains('is-invalid')`, true);
await check("0 提示文案", `return document.getElementById('countHint').textContent`, "请填 1–10000 之间的整数");
await check("0 时估算归零", `return document.getElementById('countEstimate').querySelector('b').textContent`, "—");
await check("0 时 readiness 拦截", `return readiness().title`, "次数无效");
await ev(`const c = document.getElementById('clickCount'); c.value = 'abc'; c.dispatchEvent(new Event('input', {bubbles:true})); return true;`);
await sleep(200);
await check("非数字也拦截", `return document.getElementById('clickCount').classList.contains('is-invalid')`, true);
await ev(`const c = document.getElementById('clickCount'); c.value = '1.5'; c.dispatchEvent(new Event('input', {bubbles:true})); return true;`);
await sleep(200);
await check("小数也拦截", `return document.getElementById('clickCount').classList.contains('is-invalid')`, true);
await ev(`const c = document.getElementById('clickCount'); c.value = '20'; c.dispatchEvent(new Event('input', {bubbles:true})); return true;`);
await sleep(200);
await check("恢复合法值后解除标红", `return document.getElementById('clickCount').classList.contains('is-invalid')`, false);

console.log("\n--- 9. 运行时锁定覆盖新增控件 ---");
// 同步读取：1s 轮询会调 setLocked(false) 覆盖，异步断言会与轮询赛跑
const lock = await ev(`
  setLocked(true);
  const locked = {
    picks: [...document.querySelectorAll('#clickCountPicks button')].every(b => b.disabled),
    plus: document.getElementById('clickCountPlus').disabled,
    minus: document.getElementById('clickCountMinus').disabled,
    input: document.getElementById('clickCount').disabled,
    savePlan: document.getElementById('savePlan').disabled,
    stopBtn: document.getElementById('stopBtn').disabled,
  };
  setLocked(false);
  return { locked, after: {
    picks: [...document.querySelectorAll('#clickCountPicks button')].every(b => !b.disabled),
    input: document.getElementById('clickCount').disabled,
  }};
`);
console.log("  锁定:", JSON.stringify(lock));
record("档位按钮锁定", lock.locked.picks, true);
record("步进器锁定", lock.locked.plus && lock.locked.minus, true);
record("输入框锁定", lock.locked.input, true);
record("既有控件仍锁定", lock.locked.savePlan, true);
record("停止按钮反向解锁", lock.locked.stopBtn, false);
record("解锁后档位可用", lock.after.picks, true);
record("解锁后输入框可用", lock.after.input, false);

console.log("\n--- 10. 无未捕获异常 ---");
record("无未捕获错误", pageErrors, []);

const failed = results.filter(([, status]) => status !== "PASS");
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) console.log(failed.map(([n, s, d]) => `${s} ${n} ${d}`).join("\n"));
ws.close();
process.exit(failed.length ? 1 : 0);
