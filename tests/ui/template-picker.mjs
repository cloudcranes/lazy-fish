// 截图采样页：从下拉里选已有模板名（避免重复输入），以及「将覆盖」提示。
// 非破坏性：只操作表单与内存态，不拉设备截图、不写任何模板文件。
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
function record(name, actual, expected) {
  const pass = expected === undefined || JSON.stringify(actual) === JSON.stringify(expected);
  results.push([name, pass ? "PASS" : "FAIL", `got=${JSON.stringify(actual)} want=${JSON.stringify(expected)}`]);
  console.log(`${pass ? "PASS " : "FAIL "} ${name}${pass ? "" : `\n      got=${JSON.stringify(actual)}\n      want=${JSON.stringify(expected)}`}`);
}
async function check(name, expression, expected) {
  record(name, await ev(expression), expected);
}
async function ev(expression) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression: `(() => { ${expression} })()`,
    returnByValue: true,
    awaitPromise: true,
  });
  if (exceptionDetails) {
    console.log(`THREW ${exceptionDetails.exception?.description || exceptionDetails.text}`);
    return null;
  }
  return result.value;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const setPick = (value) => ev(`
  const s = document.getElementById('templatePick');
  s.value = ${JSON.stringify(value)};
  s.dispatchEvent(new Event('change', { bubbles: true }));
  return s.value;
`);
const typeName = (value) => ev(`
  const el = document.getElementById('templateName');
  el.value = ${JSON.stringify(value)};
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
await send("Page.navigate", { url: `${BASE}/?theme=light&t=${Date.now()}#capture` });
await sleep(3000);

const names = await ev(`return templatesCache.slice();`);
const existing = names[0];
console.log(`模板清单(${names.length}): ${names.join(", ")}`);

console.log("\n--- 1. 下拉已列出全部已有模板 ---");
await check("控件就位于采样页", `return Boolean(document.querySelector('#view-capture .side-list #templatePick'))`, true);
await check("选项数 = 模板数 + 新建项", `return document.querySelectorAll('#templatePick option').length`, names.length + 1);
await check("首项是新建项", `return document.getElementById('templatePick').options[0].textContent`, "＋ 新建模板");
await check("新建项 value 为空", `return document.getElementById('templatePick').options[0].value`, "");
await check("选项值即模板文件名", `return [...document.querySelectorAll('#templatePick option')].slice(1).map(o => o.value)`, names);
await check("初始处于新建态", `return { hidden: document.getElementById('templateTarget').hidden, btn: document.getElementById('saveTemplate').textContent }`, { hidden: true, btn: "保存框选区域" });

console.log("\n--- 2. 选已有模板 → 回填名称并提示将覆盖 ---");
await setPick(existing);
await sleep(200);
await check("名称被回填", `return document.getElementById('templateName').value`, existing);
await check("出现覆盖提示", `return document.getElementById('templateTarget').hidden`, false);
await check("按钮改为覆盖语义", `return document.getElementById('saveTemplate').textContent`, "覆盖该模板");
await check("提示文案点名该文件", `return document.getElementById('templateTargetNote').textContent.includes(${JSON.stringify(existing)})`, true);
await check("缩略图指向该模板", `return document.getElementById('templateTargetImg').src.includes('/api/templates/' + encodeURIComponent(${JSON.stringify(existing)}))`, true);
await check("提示区确实可见（[hidden] 没被 display 盖掉）", `return getComputedStyle(document.getElementById('templateTarget')).display !== 'none'`, true);

console.log("\n--- 3. 选回「新建」→ 清空并撤销覆盖态 ---");
await setPick("");
await sleep(200);
await check("名称被清空", `return document.getElementById('templateName').value`, "");
await check("覆盖提示隐藏", `return document.getElementById('templateTarget').hidden`, true);
await check("提示区计算样式为 none", `return getComputedStyle(document.getElementById('templateTarget')).display`, "none");
await check("按钮回到保存语义", `return document.getElementById('saveTemplate').textContent`, "保存框选区域");

console.log("\n--- 4. 手动敲已有名称也要识别 ---");
await typeName(existing);
await sleep(200);
await check("手敲命中 → 覆盖态", `return { hidden: document.getElementById('templateTarget').hidden, pick: document.getElementById('templatePick').value }`, { hidden: false, pick: existing });
await check("手敲命中 → 下拉同步选中", `return document.getElementById('templatePick').value`, existing);

console.log("\n--- 5. 手敲新名称不误报覆盖 ---");
await typeName("brand-new-button");
await sleep(200);
await check("新名字 → 无覆盖提示", `return document.getElementById('templateTarget').hidden`, true);
await check("新名字 → 下拉回到新建", `return document.getElementById('templatePick').value`, "");
await check("新名字 → 按钮为保存", `return document.getElementById('saveTemplate').textContent`, "保存框选区域");

console.log("\n--- 6. 前后空格按 trim 判定，空串不算命中 ---");
await typeName(`  ${existing}  `);
await sleep(200);
await check("带空格仍判为已有", `return document.getElementById('templateTarget').hidden`, false);
await typeName("   ");
await sleep(200);
await check("纯空格不算命中", `return document.getElementById('templateTarget').hidden`, true);

console.log("\n--- 7. 模板列表变化时选项同步 ---");
const sync = await ev(`
  const backup = templatesCache;
  templatesCache = [];
  renderTemplateOptions();
  const empty = {
    options: document.querySelectorAll('#templatePick option').length,
    hidden: document.getElementById('templateTarget').hidden,
  };
  templatesCache = backup;
  renderTemplateOptions();
  return { empty, restored: document.querySelectorAll('#templatePick option').length };
`);
record("无模板时只剩新建项", sync.empty.options, 1);
record("无模板时无覆盖提示", sync.empty.hidden, true);
record("恢复后选项还原", sync.restored, names.length + 1);

console.log("\n--- 8. 运行时锁定 ---");
const lock = await ev(`
  setLocked(true);
  const locked = document.getElementById('saveTemplate').disabled;
  setLocked(false);
  return { locked, after: document.getElementById('saveTemplate').disabled };
`);
record("运行中保存按钮锁定", lock.locked, true);
record("解锁后保存按钮可用", lock.after, false);

console.log("\n--- 9. 还原现场 ---");
await typeName("");
await setPick("");
await sleep(200);
await check("名称已清空", `return document.getElementById('templateName').value`, "");
await check("回到新建态", `return document.getElementById('templateTarget').hidden`, true);

console.log("\n--- 10. 无未捕获异常 ---");
record("无未捕获错误", pageErrors, []);

const failed = results.filter(([, status]) => status !== "PASS");
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) console.log(failed.map(([n, s, d]) => `${s} ${n} ${d}`).join("\n"));
ws.close();
process.exit(failed.length ? 1 : 0);
