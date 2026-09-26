// 最小 WS relay: 多端同控, 按组/标签路由
// 协议 (JSON):
//   C->S  { type:"hello", role:"controller"|"device", id, groups:[string] }
//   C->S  { type:"cmd",   to:"*"|"<id>"|{group:"<g>"}, action, payload }
//   D->S  { type:"ack",   id, action, ok, result }
//   D->S  { type:"event", id, name, payload }
//   S->*  { type:"state", devices:[{id,groups,online,lastSeen}], controllers:n }

import { WebSocketServer } from 'ws';

const PORT = Number(process.env.PORT) || 8787;
const wss = new WebSocketServer({ port: PORT, host: '0.0.0.0' });

// role -> Map<id, { ws, groups:Set, meta }>
const devices = new Map();
const controllers = new Map();

const send = (ws, obj) => {
  try { ws.send(JSON.stringify(obj)); } catch {}
};

const broadcastState = () => {
  const snap = {
    type: 'state',
    devices: [...devices.entries()].map(([id, d]) => ({
      id, groups: [...d.groups], online: d.ws.readyState === 1, lastSeen: d.lastSeen,
    })),
    controllers: controllers.size,
    ts: Date.now(),
  };
  for (const c of controllers.values()) send(c.ws, snap);
};

const routeCmd = (cmd) => {
  const matched = [];
  for (const [id, d] of devices) {
    if (d.ws.readyState !== 1) continue;
    if (cmd.to === '*') { matched.push(d); continue; }
    if (typeof cmd.to === 'string' && cmd.to === id) { matched.push(d); continue; }
    if (cmd.to && typeof cmd.to === 'object' && cmd.to.group && d.groups.has(cmd.to.group)) {
      matched.push(d);
    }
  }
  for (const d of matched) {
    d.lastSeen = Date.now();
    send(d.ws, { type: 'cmd', id: d.meta.id, action: cmd.action, payload: cmd.payload, reqId: cmd.reqId });
  }
  return matched.length;
};

wss.on('connection', (ws, req) => {
  ws.meta = { id: null, role: null, ip: req.socket.remoteAddress };
  ws.isAlive = true;
  ws.on('pong', () => (ws.isAlive = true));

  ws.on('message', (raw) => {
    let msg; try { msg = JSON.parse(raw.toString()); } catch { return; }
    if (msg.type === 'hello') {
      ws.meta.role = msg.role;
      ws.meta.id = String(msg.id || ws.meta.ip);
      ws.meta.groups = new Set(msg.groups || []);
      if (msg.role === 'device') devices.set(ws.meta.id, { ws, groups: ws.meta.groups, meta: ws.meta, lastSeen: Date.now() });
      else if (msg.role === 'controller') controllers.set(ws.meta.id, { ws, meta: ws.meta });
      send(ws, { type: 'welcome', id: ws.meta.id, role: ws.meta.role });
      broadcastState();
      return;
    }
    if (msg.type === 'cmd' && ws.meta.role === 'controller') {
      const n = routeCmd({ ...msg, to: msg.to ?? '*' });
      send(ws, { type: 'cmd-sent', reqId: msg.reqId, matched: n });
      return;
    }
    if ((msg.type === 'ack' || msg.type === 'event') && ws.meta.role === 'device') {
      ws.meta.lastSeen = Date.now();
      const d = devices.get(ws.meta.id); if (d) d.lastSeen = Date.now();
      for (const c of controllers.values()) send(c.ws, msg);
      return;
    }
  });

  ws.on('close', () => {
    if (ws.meta.role === 'device') devices.delete(ws.meta.id);
    else if (ws.meta.role === 'controller') controllers.delete(ws.meta.id);
    broadcastState();
  });
});

// 心跳
setInterval(() => {
  for (const ws of wss.clients) {
    if (!ws.isAlive) return ws.terminate();
    ws.isAlive = false; try { ws.ping(); } catch {}
  }
}, 15000);

console.log(`[relay] ws://0.0.0.0:${PORT}  devices=${devices.size} controllers=${controllers.size}`);