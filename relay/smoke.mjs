// 三角色烟雾测试: 1 controller + 2 device (组A/B) + 1 device (组A)
// 验证: hello/state/广播/单播/组播/ack/event
import WebSocket from 'ws';

const URL = process.env.URL || 'ws://127.0.0.1:8787';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const waitOpen = (ws, tag) => new Promise((resolve, reject) => {
  ws.once('open', () => { console.log(`[${tag}] open`); resolve(); });
  ws.once('error', reject);
  const t = setTimeout(() => reject(new Error(`${tag} timeout`)), 5000);
  ws.once('open', () => clearTimeout(t));
});

const collect = (ws, tag) => {
  const got = [];
  ws.on('message', (raw) => {
    const m = JSON.parse(raw.toString());
    got.push(m);
    console.log(`[${tag}] <-`, JSON.stringify(m).slice(0, 200));
  });
  return got;
};

const ctrl = new WebSocket(URL);
await waitOpen(ctrl, 'ctrl');
const ctrlIn = collect(ctrl, 'ctrl');

const dA1 = new WebSocket(URL); await waitOpen(dA1, 'dA1'); const dA1In = collect(dA1, 'dA1');
const dA2 = new WebSocket(URL); await waitOpen(dA2, 'dA2'); const dA2In = collect(dA2, 'dA2');
const dB  = new WebSocket(URL); await waitOpen(dB,  'dB');  const dBIn  = collect(dB,  'dB');

ctrl.send(JSON.stringify({ type:'hello', role:'controller', id:'web-1' }));
dA1.send(JSON.stringify({ type:'hello', role:'device', id:'dev-A1', groups:['A'] }));
dA2.send(JSON.stringify({ type:'hello', role:'device', id:'dev-A2', groups:['A'] }));
dB .send(JSON.stringify({ type:'hello', role:'device', id:'dev-B',  groups:['B'] }));

await sleep(300);

console.log('\n=== broadcast ===');
ctrl.send(JSON.stringify({ type:'cmd', action:'click', payload:{ x:1, y:2 }, reqId:'r1' }));
await sleep(200);
console.log('A1 cmd:', dA1In.some(m=>m.type==='cmd'));
console.log('A2 cmd:', dA2In.some(m=>m.type==='cmd'));
console.log('B  cmd:', dB .some(m=>m.type==='cmd'));

console.log('\n=== group A ===');
ctrl.send(JSON.stringify({ type:'cmd', to:{ group:'A' }, action:'open', payload:{ url:'x' }, reqId:'r2' }));
await sleep(200);
console.log('A1 cmd:', dA1In.some(m=>m.type==='cmd' && m.action==='open'));
console.log('A2 cmd:', dA2In.some(m=>m.type==='cmd' && m.action==='open'));
console.log('B  cmd (should be false):', dB .some(m=>m.type==='cmd' && m.action==='open'));

console.log('\n=== unicast ===');
ctrl.send(JSON.stringify({ type:'cmd', to:'dev-B', action:'ping', reqId:'r3' }));
await sleep(200);
console.log('B cmd:', dB .some(m=>m.type==='cmd' && m.action==='ping'));

console.log('\n=== ack ===');
dA1.send(JSON.stringify({ type:'ack', id:'dev-A1', action:'click', ok:true, result:{ hit:true }, reqId:'r1' }));
await sleep(150);
console.log('ctrl got ack:', ctrlIn.some(m=>m.type==='ack' && m.id==='dev-A1'));

console.log('\n=== state ===');
console.log('ctrl saw devices:', ctrlIn.filter(m=>m.type==='state').at(-1)?.devices?.length);

ctrl.close(); dA1.close(); dA2.close(); dB.close();
await sleep(100);
console.log('\nOK');
process.exit(0);