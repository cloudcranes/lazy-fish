// 控制台「运行配置」卡：能否在首屏直接载入方案、设定本轮执行次数。
// 非破坏性：只载入方案与改表单值，不改动任何方案文件（结束时还原默认方案与次数）。
//
// 前置：Edge 以 --remote-debugging-port=9333 启动，后端跑在 8765（见 tests/ui/README.md）
const PORT = process.env.CDP_PORT || 9333;
const BASE = process.env.APP_BASE || "http://127.0.0.1:8765";

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
async function ev(expression) {
  const { result } = await send("Runtime.evaluate", {
    expression: `(() => { ${expression} })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  return result.value;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const setCount = (id, value) => ev(`
  const el = document.getElementById('${id}');
  el.value = ${JSON.stringify(String(value))};
  el.dispatchEvent(new Event('input', { bubbles: true }));
  return el.value;
`);

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });
// 先离开再进入：同 URL（含 hash）的 navigate 不会真正重新加载，会残留上一次的内存状态
await send("Page.navigate", { url: "about:blank" });
await sleep(600);
pageErrors.length = 0;
await send("Page.navigate", { url: `${BASE}/?theme=light&t=${Date.now()}#console` });
await sleep(3000);

const plans = await ev(`return plansCache.map(p => ({ filename: p.filename, name: p.name, count: p.payload?.click_count, default: Boolean(p.default) }));`);
const defaultPlan = plans.find((p) => p.default) || plans[0];
const otherPlan = plans.find((p) => p.filename !== defaultPlan.filename) || defaultPlan;
console.log(`方案清单: ${plans.map((p) => `${p.name}(${p.count})${p.default ? " [默认]" : ""}`).join(", ")}`);
console.log(`默认=${defaultPlan.filename}  对照=${otherPlan.filename}`);

console.log("\n--- 1. 控制台首屏：运行配置卡已就位 ---");
await check("控制台是当前视图", `return document.getElementById('view-console').classList.contains('is-active')`, true);
await check("运行配置卡在控制台视图内", `return Boolean(document.querySelector('#view-console .run-config'))`, true);
await check("芯片数量与已存方案一致", `return document.querySelectorAll('#consolePlanChips .plan-chip').length`, plans.length);
await check("默认方案芯片带默认标签", `return Boolean(document.querySelector('#consolePlanChips .plan-chip[data-filename="${defaultPlan.filename}"] .plan-chip-tag'))`, true);
await check("芯片显示参数摘要", `return /\\d+ 连/.test(document.querySelector('#consolePlanChips .plan-chip .plan-chip-meta').textContent)`, true);
await check("初始有且仅有一个芯片处于使用中", `return document.querySelectorAll('#consolePlanChips .plan-chip[aria-pressed="true"]').length`, 1);
await check("使用中的就是已载入参数的方案", `return document.querySelector('#consolePlanChips .plan-chip[aria-pressed="true"]').dataset.filename`, defaultPlan.filename);
await check("未载入时无未保存改动标记", `return document.getElementById('consoleDirty').hidden`, true);

console.log("\n--- 2. 点芯片 = 真的载入方案参数 ---");
if (otherPlan.filename !== defaultPlan.filename) {
  await ev(`document.querySelector('#consolePlanChips .plan-chip[data-filename="${otherPlan.filename}"]').click(); return true;`);
  await sleep(1400);
  await check("chip 切换已载入方案", `return activePlanFilename`, otherPlan.filename);
  await check("方案页参数真的被载入", `return document.getElementById('clickCount').value`, String(otherPlan.count));
  await check("控制台次数镜像同步", `return document.getElementById('consoleCount').value`, String(otherPlan.count));
  await check("控制台指标跟随", `return document.getElementById('currentPlanName').textContent`, otherPlan.name);
  await check("控制台说明跟随", `return document.getElementById('consoleConfigNote').textContent.includes(${JSON.stringify(otherPlan.name)})`, true);
  await check("芯片选中态互斥", `return document.querySelectorAll('#consolePlanChips .plan-chip[aria-pressed="true"]').length`, 1);
  await check("方案页方案卡同步指向", `return document.querySelectorAll('.plan-card.is-active').length === 1 && document.querySelector('.plan-card.is-active').dataset.filename === ${JSON.stringify(otherPlan.filename)}`, true);
  await check("载入后不算未保存改动", `return document.getElementById('consoleDirty').hidden`, true);
} else {
  record("只有 1 个方案时跳过切换断言", true, true);
}

console.log("\n--- 3. 控制台快捷档位 ---");
await ev(`document.querySelector('#consoleCountPicks button[data-value="50"]').click(); return true;`);
await sleep(250);
await check("控制台档位写入唯一真相", `return document.getElementById('clickCount').value`, "50");
await check("控制台输入框同步", `return document.getElementById('consoleCount').value`, "50");
await check("控制台档位高亮", `return document.querySelector('#consoleCountPicks button[data-value="50"]').getAttribute('aria-pressed')`, "true");
await check("方案页档位同步高亮", `return document.querySelector('#clickCountPicks button[data-value="50"]').getAttribute('aria-pressed')`, "true");
await check("两处档位互斥一致", `return document.getElementById('clickCountPicks').querySelectorAll('button[aria-pressed="true"]').length === 1 && document.getElementById('consoleCountPicks').querySelectorAll('button[aria-pressed="true"]').length === 1`, true);
await check("控制台耗时估算与方案页一致", `return document.getElementById('consoleEstimate').querySelector('b').textContent === document.getElementById('plansEstimate').querySelector('b').textContent`, true);

console.log("\n--- 4. 控制台步进器 ---");
await ev(`document.getElementById('consoleCountPlus').click(); return true;`);
await sleep(200);
await check("50 → 51", `return document.getElementById('clickCount').value`, "51");
await ev(`document.querySelector('#consoleCountPicks button[data-value="100"]').click(); document.getElementById('consoleCountPlus').click(); return true;`);
await sleep(200);
await check("100 → 110（步长升为 10）", `return document.getElementById('clickCount').value`, "110");
await check("镜像同步 110", `return document.getElementById('consoleCount').value`, "110");
await ev(`document.getElementById('consoleCountMinus').click(); return true;`);
await sleep(200);
await check("110 → 100", `return document.getElementById('clickCount').value`, "100");

console.log("\n--- 5. 双向镜像：任一侧改动都要贯通 ---");
await setCount("clickCount", 7);
await sleep(200);
await check("方案页改 → 控制台跟随", `return document.getElementById('consoleCount').value`, "7");
await setCount("consoleCount", 9);
await sleep(200);
await check("控制台改 → 方案页跟随", `return document.getElementById('clickCount').value`, "9");
await check("控制台改 → 概要跟随", `return document.getElementById('summaryClickCount').textContent`, "9 次");

console.log("\n--- 6. 未保存改动标记 ---");
await check("改次数后控制台出现改动标记", `return document.getElementById('consoleDirty').hidden`, false);
await check("方案页同步出现修改提示", `return document.getElementById('planDirty').hidden`, false);
await check("说明文案不再声称与方案一致", `return document.getElementById('consoleConfigNote').textContent.includes('参数已改动') || document.getElementById('consoleDirty').hidden === false`, true);
if (otherPlan.filename !== defaultPlan.filename) {
  await setCount("consoleCount", otherPlan.count);
  await sleep(250);
  await check("改回原值后标记消失", `return document.getElementById('consoleDirty').hidden`, true);
  await check("方案页标记同步消失", `return document.getElementById('planDirty').hidden`, true);
}

console.log("\n--- 7. 空方案态的兜底（仅内存态，不动数据） ---");
const emptyState = await ev(`
  const backup = plansCache;
  plansCache = [];
  renderPlanChips();
  const chipBox = document.getElementById('consolePlanChips');
  const states = {
    chips: chipBox.querySelectorAll('.plan-chip').length,
    boxHidden: chipBox.hidden,
    emptyShown: !document.getElementById('consolePlanEmpty').hidden,
    lockSafe: applyLock(),
  };
  plansCache = backup;
  renderPlanChips();
  return { ...states, restored: document.querySelectorAll('#consolePlanChips .plan-chip').length };
`);
record("无方案时不渲染芯片", emptyState.chips, 0);
record("无方案时隐藏芯片容器", emptyState.boxHidden, true);
record("无方案时给出空态引导", emptyState.emptyShown, true);
record("空态渲染不抛错且可恢复", emptyState.restored, plans.length);

console.log("\n--- 8. 运行时锁定覆盖控制台新增控件 ---");
// 同步读取：1s 轮询会调 setLocked(false) 覆盖，异步断言会与轮询赛跑
const lock = await ev(`
  setLocked(true);
  const locked = {
    chips: [...document.querySelectorAll('#consolePlanChips .plan-chip')].every(c => c.disabled),
    picks: [...document.querySelectorAll('#consoleCountPicks button')].every(b => b.disabled),
    plus: document.getElementById('consoleCountPlus').disabled,
    input: document.getElementById('consoleCount').disabled,
    startBtn: document.getElementById('startBtn').disabled,
  };
  setLocked(false);
  return { locked, after: {
    chips: [...document.querySelectorAll('#consolePlanChips .plan-chip')].every(c => !c.disabled),
    input: document.getElementById('consoleCount').disabled,
    startBtn: document.getElementById('startBtn').disabled,
  }};
`);
console.log("  锁定:", JSON.stringify(lock));
record("方案芯片锁定", lock.locked.chips, true);
record("档位锁定", lock.locked.picks, true);
record("步进器锁定", lock.locked.plus, true);
record("输入框锁定", lock.locked.input, true);
record("开始按钮仍锁定", lock.locked.startBtn, true);
record("解锁后芯片可用", lock.after.chips, true);
record("解锁后输入框可用", lock.after.input, false);

console.log("\n--- 9. 控制台步进器的边界禁用 + 校验态 ---");
const bounds = await ev(`
  const set = (v) => {
    const el = document.getElementById('consoleCount');
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };
  set('1');
  const atMin = { minus: document.getElementById('consoleCountMinus').disabled, planMinus: document.getElementById('clickCountMinus').disabled };
  set('10000');
  const atMax = { plus: document.getElementById('consoleCountPlus').disabled, planPlus: document.getElementById('clickCountPlus').disabled };
  set('');
  const invalid = {
    consoleInput: document.getElementById('consoleCount').classList.contains('is-invalid'),
    planInput: document.getElementById('clickCount').classList.contains('is-invalid'),
    consoleHint: document.getElementById('consoleCountHint').className,
    planHint: document.getElementById('countHint').className,
    estimate: document.getElementById('consoleEstimate').querySelector('b').textContent,
  };
  set('20');
  const restored = {
    valid: document.getElementById('consoleCount').classList.contains('is-invalid'),
    hint: document.getElementById('consoleCountHint').className,
    note: document.getElementById('metricClickCountNote').textContent,
  };
  return { atMin, atMax, invalid, restored };
`);
console.log("  边界:", JSON.stringify(bounds));
record("下界禁用 −", bounds.atMin.minus, true);
record("下界方案页同步禁用", bounds.atMin.planMinus, true);
record("上界禁用 +", bounds.atMax.plus, true);
record("上界方案页同步禁用", bounds.atMax.planPlus, true);
record("非法值：控制台输入标红", bounds.invalid.consoleInput, true);
record("非法值：方案页输入同步标红", bounds.invalid.planInput, true);
record("非法值：控制台提示转危险", bounds.invalid.consoleHint, "field-hint is-danger");
record("非法值：方案页提示同步", bounds.invalid.planHint, "field-hint is-danger");
record("非法值：估算归零", bounds.invalid.estimate, "—");
record("恢复合法值后解除标红", bounds.restored.valid, false);
record("恢复合法值后提示复位", bounds.restored.hint, "field-hint");
record("指标卡备注恢复估算", /^预计 /.test(bounds.restored.note), true);

console.log("\n--- 10. 还原现场 ---");
// 注意：包装器是同步 IIFE，不能在表达式里写 await，只能把 Promise 交回给 CDP 等
await ev(`return selectPlan(${JSON.stringify(defaultPlan.filename)}, { quiet: true });`);
await sleep(1200);
await check("已还原默认方案", `return activePlanFilename`, defaultPlan.filename);
await check("已还原默认方案的次数", `return document.getElementById('clickCount').value`, String(defaultPlan.count));
await check("还原后芯片选中态正确", `return document.querySelector('#consolePlanChips .plan-chip[aria-pressed="true"]').dataset.filename`, defaultPlan.filename);

console.log("\n--- 11. 无未捕获异常 ---");
record("无未捕获错误", pageErrors, []);

const failed = results.filter(([, status]) => status !== "PASS");
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) console.log(failed.map(([n, s, d]) => `${s} ${n} ${d}`).join("\n"));
ws.close();
process.exit(failed.length ? 1 : 0);
