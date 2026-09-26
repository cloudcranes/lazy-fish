// 临时：验证截图采样链路（获取截图 -> 拖拽框选 -> 保存裁剪模板）
import fs from "node:fs";

const PORT = 9333;
const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = list.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));

let seq = 0;
const pending = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}) =>
  new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
    ws.send(JSON.stringify({ id, method, params }));
  });
const evaluate = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: `(() => { ${expr} })()`, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = [];
const ok = (n, v, w) => out.push(`${JSON.stringify(v) === JSON.stringify(w) ? "PASS " : "FAIL "} ${n}${JSON.stringify(v) === JSON.stringify(w) ? "" : `  ->  got=${JSON.stringify(v)} want=${JSON.stringify(w)}`}`);

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1600, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: "http://127.0.0.1:8765/?theme=light#capture" });
await sleep(2500);

await evaluate(`document.getElementById('shotBtn').click(); return true;`);
await sleep(3000);

ok("screenshot visible", await evaluate(`const i=document.getElementById('shot'); return !i.hidden && i.naturalWidth > 0;`), true);
ok("empty placeholder hidden", await evaluate(`return document.getElementById('shotEmpty').hidden;`), true);
ok("stage marked has-shot", await evaluate(`return document.getElementById('shotStage').classList.contains('has-shot');`), true);
ok("no error toast", await evaluate(`return document.querySelectorAll('.toast.is-error').length;`), 0);

const box = await evaluate(`
  const r = document.getElementById('shot').getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height };
`);
console.log("image rect:", box);

// 在图片中央区域拖拽一个框
const startX = box.x + box.w * 0.25;
const startY = box.y + box.h * 0.35;
const endX = box.x + box.w * 0.55;
const endY = box.y + box.h * 0.55;

await send("Input.dispatchMouseEvent", { type: "mousePressed", x: startX, y: startY, button: "left", buttons: 1, clickCount: 1 });
await sleep(80);
await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: endX, y: endY, button: "left", buttons: 1 });
await sleep(80);
await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: endX, y: endY, button: "left", buttons: 0, clickCount: 1 });
await sleep(300);

const marquee = await evaluate(`
  const m = document.getElementById('selection');
  const s = getComputedStyle(m);
  return { display: s.display, left: m.style.left, top: m.style.top, width: m.style.width, height: m.style.height };
`);
console.log("marquee:", marquee);

const expectedW = ((endX - startX) / box.w) * 100;
const expectedH = ((endY - startY) / box.h) * 100;
const pctW = parseFloat(marquee.width) / box.w * 100;
const pctH = parseFloat(marquee.height) / box.h * 100;
ok("marquee visible", marquee.display, "block");
ok("marquee width tracks drag (±2%)", Math.abs(pctW - expectedW) < 2, true);
ok("marquee height tracks drag (±2%)", Math.abs(pctH - expectedH) < 2, true);
ok("marquee left offset anchored to image", Math.abs(parseFloat(marquee.left) - box.w * 0.25) < 6, true);

const readout = await evaluate(`return document.getElementById('coordReadout').textContent;`);
console.log("readout:", readout);
ok("coord readout shows native pixels", /起点 \d+, \d+ · 尺寸 \d+ × \d+/.test(readout), true);

// 保存模板
ok("template name required", await evaluate(`
  document.querySelectorAll('.toast').forEach(t=>t.remove());
  document.getElementById('templateName').value = '';
  document.getElementById('saveTemplate').click();
  return true;
`), true);
await sleep(400);
ok("error toast when name empty", await evaluate(`return document.querySelector('.toast.is-error')?.textContent.includes('名称');`), true);

const NAME = "zz-smoke-test-crop";
await evaluate(`
  document.querySelectorAll('.toast').forEach(t=>t.remove());
  document.getElementById('templateName').value = '${NAME}';
  document.getElementById('saveTemplate').click();
  return true;
`);
await sleep(2500);
// 名称命中已有模板时按钮语义会变成「覆盖该模板」，提示文案也随之变化，这里两种都接受
ok("success toast after save", await evaluate(`return /模板.*已/.test(document.querySelector('.toast.is-success')?.textContent || '');`), true);
ok("template appears in grid", await evaluate(`return [...document.querySelectorAll('.tpl-card')].some(c => c.dataset.name === '${NAME}.png');`), true);
ok("no error toast", await evaluate(`return document.querySelectorAll('.toast.is-error').length;`), 0);

console.log(out.join("\n"));
ws.close();
process.exit(out.some((l) => l.startsWith("FAIL")) ? 1 : 0);
