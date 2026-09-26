// 方案卡破坏性操作：设为默认 / 删除。
// 会临时创建一个 zz-自检方案，结束时删除并还原原本的默认方案标记。
//
// 前置：Edge 以 --remote-debugging-port=9333 启动，后端跑在 8765（见 tests/ui/README.md）
import fs from "node:fs";

const PORT = process.env.CDP_PORT || 9333;
const API = process.env.APP_BASE || "http://127.0.0.1:8765";
const TEMP_NAME = "zz-自检方案";
const TEMP_FILE = `${TEMP_NAME}.json`;

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
async function check(name, expression, expected) {
  record(name, await ev(expression), expected);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fetchPlans = async () => (await (await fetch(`${API}/api/plans`)).json()).plans;
const postPlan = (body) =>
  fetch(`${API}/api/plans`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// --- 数据准备 ---
const before = await fetchPlans();
const originalDefault = before.find((p) => p.default)?.filename || null;
const restored = before.find((p) => p.filename === originalDefault) || null;
console.log(`原始默认方案: ${originalDefault}（共 ${before.length} 个方案）`);

await postPlan({
  name: TEMP_NAME,
  default: false,
  payload: {
    device_id: null,
    first_template_names: ["video-first-open10.png"],
    template_names: ["video-repeat10.png"],
    click_count: 7,
    interval_seconds: 0.8,
    jitter_seconds: 0.2,
    post_click_wait_seconds: 3,
    repeat_tap_count: 2,
    repeat_tap_gap_seconds: 0.12,
    threshold: 0.9,
    max_misses: 5,
  },
});
console.log(`已创建临时方案: ${TEMP_NAME}`);

let exitCode = 1;
try {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });
  // 先离开再进入：同 URL（含 hash）的 navigate 不会真正重新加载，会残留上一次的内存状态
  await send("Page.navigate", { url: "about:blank" });
  await sleep(600);
  await send("Page.navigate", { url: `${API}/?theme=light&t=${Date.now()}#plans` });
  await sleep(3000);

  console.log("\n--- 1. 临时方案出现，默认仍在原方案 ---");
  await check("卡片数 +1", `return document.querySelectorAll('.plan-card').length`, before.length + 1);
  await check("原始默认标记未变", `return document.querySelector('.plan-card .pill-brand')?.closest('.plan-card').dataset.filename`, originalDefault);
  await check("临时方案显示设为默认", `
    const c = document.querySelector('.plan-card[data-filename="${TEMP_FILE}"]');
    return c.querySelector('[data-act="default"]').textContent.trim();
  `, "设为默认");

  console.log("\n--- 2. 设为默认 ---");
  await ev(`document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] [data-act="default"]').click(); return true;`);
  await sleep(1600);
  await check("默认已转移", `return document.querySelector('.plan-card .pill-brand')?.closest('.plan-card').dataset.filename`, TEMP_FILE);
  await check("按钮变为已是默认并禁用", `
    const b = document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] [data-act="default"]');
    return [b.textContent.trim(), b.disabled];
  `, ["已是默认", true]);
  await check("原默认降级为普通", `
    const c = [...document.querySelectorAll('.plan-card')].find(x => x.dataset.filename === ${JSON.stringify(originalDefault)});
    if (!c) return 'card-not-found';
    return c.querySelector('.pill-brand') === null;
  `, true);
  const afterDefault = await fetchPlans();
  record("后端只有一个默认", afterDefault.filter((p) => p.default).length, 1);
  record("后端默认 = 临时方案", afterDefault.find((p) => p.default)?.filename, TEMP_FILE);

  console.log("\n--- 3. 删除：二次确认内联在卡上，不再调用原生 confirm ---");
  // 先载入临时方案，以便验证"删除使用中方案"的状态清理
  await ev(`document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] .plan-card-hit').click(); return true;`);
  await sleep(1500);
  await check("临时方案已载入", `return activePlanFilename`, TEMP_FILE);
  await check("参数随载入变化", `return document.getElementById('clickCount').value`, "7");
  // 原生 confirm 一旦被调用就置标记：内联确认方案必须完全不碰它（沙箱 iframe 里它会静默返回 false）
  await ev(`window.__confirmCalled = false; window.confirm = () => { window.__confirmCalled = true; return false; }; return true;`);
  await ev(`document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] [data-act="delete"]').click(); return true;`);
  await sleep(600);
  await check("点删除进入待确认态", `
    const c = document.querySelector('.plan-card[data-filename="${TEMP_FILE}"]');
    return {
      cardArmed: c.classList.contains('is-armed'),
      rowArmed: c.querySelector('.plan-card-actions').classList.contains('is-armed'),
      confirmBtn: c.querySelector('[data-act="confirm-delete"]')?.textContent.trim(),
      cancelBtn: Boolean(c.querySelector('[data-act="cancel-delete"]')),
      hitHidden: c.querySelector('[data-act="delete"]') === null,
    };
  `, { cardArmed: true, rowArmed: true, confirmBtn: "确认删除", cancelBtn: true, hitHidden: true });
  await check("全程未调用原生 confirm", `return window.__confirmCalled`, false);
  await check("待确认时方案还在", `return document.querySelectorAll('.plan-card').length`, before.length + 1);
  await ev(`document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] [data-act="cancel-delete"]').click(); return true;`);
  await sleep(400);
  await check("取消后回到常规操作行", `
    const c = document.querySelector('.plan-card[data-filename="${TEMP_FILE}"]');
    return { armed: c.classList.contains('is-armed'), del: c.querySelector('[data-act="delete"]')?.textContent.trim() };
  `, { armed: false, del: "删除" });
  await check("取消后方案仍在", `return document.querySelectorAll('.plan-card').length`, before.length + 1);

  console.log("\n--- 3b. Esc 也应放弃删除确认 ---");
  await ev(`document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] [data-act="delete"]').click(); return true;`);
  await sleep(400);
  await check("再次进入待确认", `return document.querySelector('.plan-card[data-filename="${TEMP_FILE}"]').classList.contains('is-armed')`, true);
  await ev(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true;`);
  await sleep(400);
  await check("Esc 后解除待确认", `return document.querySelector('.plan-card[data-filename="${TEMP_FILE}"]').classList.contains('is-armed')`, false);
  await check("Esc 后方案仍在", `return document.querySelectorAll('.plan-card').length`, before.length + 1);

  console.log("\n--- 3c. 运行时锁定覆盖删除相关按钮（同步读，避免与轮询赛跑） ---");
  const delLock = await ev(`
    const card = document.querySelector('.plan-card[data-filename="${TEMP_FILE}"]');
    // 1) 未进入确认态：「删除」按钮也要跟锁定
    setLocked(true);
    const idle = { del: card.querySelector('[data-act="delete"]').disabled };
    setLocked(false);
    // 2) 进入确认态后锁定：「确认删除」「取消」都要跟上
    card.querySelector('[data-act="delete"]').click();
    setLocked(true);
    const row = card.querySelector('.plan-card-actions');
    const armed = {
      confirm: row.querySelector('[data-act="confirm-delete"]').disabled,
      cancel: row.querySelector('[data-act="cancel-delete"]').disabled,
    };
    setLocked(false);
    const after = { confirm: row.querySelector('[data-act="confirm-delete"]').disabled };
    // 取消并复原操作行，避免影响后续段落
    row.querySelector('[data-act="cancel-delete"]').click();
    return { idle, armed, after };
  `);
  console.log("  锁定:", JSON.stringify(delLock));
  record("运行中「删除」入口锁定", delLock.idle.del, true);
  record("运行中「确认删除」锁定", delLock.armed.confirm, true);
  record("运行中「取消」锁定", delLock.armed.cancel, true);
  record("解锁后「确认删除」可用", delLock.after.confirm, false);
  await check("段落结束后已复原为常规操作行", `return document.querySelector('.plan-card[data-filename="${TEMP_FILE}"]').classList.contains('is-armed')`, false);

  console.log("\n--- 4. 删除：确认后删除并清理使用中状态 ---");
  await ev(`document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] [data-act="delete"]').click(); return true;`);
  await sleep(400);
  await ev(`document.querySelector('.plan-card[data-filename="${TEMP_FILE}"] [data-act="confirm-delete"]').click(); return true;`);
  await sleep(1800);
  await check("卡片数还原", `return document.querySelectorAll('.plan-card').length`, before.length);
  const final = await fetchPlans();
  record("后端已删除", final.some((p) => p.filename === TEMP_FILE), false);
  await check("使用中状态未悬空", `return { active: activePlanFilename, cards: document.querySelectorAll('.plan-card.is-active').length }`, { active: null, cards: 0 });
  await check("顶部条回到未载入", `return document.getElementById('activePlanBadge').textContent`, "未载入");

  console.log("\n--- 5. 还原原始默认方案 ---");
  if (restored) {
    await postPlan({ name: restored.name, payload: restored.payload, default: true });
  }
  const settled = await fetchPlans();
  record("默认已还原", settled.find((p) => p.default)?.filename || null, originalDefault);
  record("方案数量还原", settled.length, before.length);
  record("无残留临时方案", settled.some((p) => p.filename === TEMP_FILE), false);
  record("无未捕获异常", pageErrors, []);

  const failed = results.filter(([, status]) => status !== "PASS");
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  if (failed.length) console.log(failed.map(([n, s, d]) => `${s} ${n} ${d}`).join("\n"));
  exitCode = failed.length ? 1 : 0;
} finally {
  // 无论如何都清掉临时方案，避免污染用户数据。
  // 注意：删除是软删除（文件被 os.replace 到 data/plans-trash/），所以回收目录也要一起清，
  // 否则连跑几次会堆出一串 zz-自检方案.json。
  const leftover = (await fetchPlans()).some((p) => p.filename === TEMP_FILE);
  if (leftover) {
    await fetch(`${API}/api/plans/${encodeURIComponent(TEMP_FILE)}`, { method: "DELETE" });
    console.log(`已清理残留临时方案: ${TEMP_FILE}`);
  }
  const trashFile = new URL(`../../data/plans-trash/${TEMP_FILE}`, import.meta.url);
  if (fs.existsSync(trashFile)) {
    fs.unlinkSync(trashFile);
    console.log(`已清理回收目录残留: ${TEMP_FILE}`);
  }
  ws.close();
}
process.exit(exitCode);
