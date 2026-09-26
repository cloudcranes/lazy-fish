# t2 · 前端深度审计报告

**范围**：`templates/index.html`、`static/app.{css,js}`、`static/tokens.css`、`tests/ui/*`
**性质**：只读分析。报告里所有 diff 均为"示意最小改动"，实际落代码需走正常 PR 评审。
**评级约定**：P0（运行时坏掉）/ P1（功能正确但 UX/契约错位）/ P2（资源/可维护性优化）。

---

## 0. 体积与构建盘点

| 文件 | 体积 |
|---|---|
| `templates/index.html` | 33.4 KB |
| `static/app.css` | 45.7 KB |
| `static/app.js` | 54.1 KB |
| `static/tokens.css` | 5.2 KB |
| 合计（首屏） | **~138 KB / 约 35 KB gz（粗估）** |

- 没有 webfont：仅引用系统字体栈（`static/tokens.css:73-74`）→ 字体加载策略已最优，不存在 FOIT/FOUT。
- CSS 体积偏大的主因：颜色/排版工具类 + 大量 grid 断点重复（详见 §2）。
- JS 单文件无拆分，`app.js` 直接走 `?v={{ asset_version }}` 缓存爆破（`templates/index.html:595`）。OK 但任何改动都让浏览器整体重新解析。

---

## 1. 与后端契约（P0）

### 1.1 `state.last_screenshot` 类型错位 · 运行时 P0

**位置**：
- 后端：`xyzw_auto_clicker/runner.py:26`（声明 `bytes | None`）、`runner.py:41`（直接放进 snapshot 字典）、`runner.py:100`（赋值原始 PNG 字节）。
- 前端消费：`static/app.js:1160-1180`（`renderLiveShot`）。

**现状**：
后端 `/api/tasks/state` 返回的 `last_screenshot` 是原始 PNG `bytes`。FastAPI 默认 `jsonable_encoder` 会把 `bytes` 编码成 **base64 字符串** 写进 JSON（实测 size 几十 KB 到上百 KB）。

前端把它当成"截图文件名"用：
```js
// static/app.js:1161
const name = state.last_screenshot || null;
// ...
img.src = shotUrl(name);  // → "/api/screenshots/<huge-base64>"
```
`shotStamp(name)` 的正则 `^shot-(\d{4})(\d{2})(\d{2})-...`（`app.js:1149`）也不会匹配 base64，于是 `liveShotNote` 永远停在"等待任务开始"附近的 fallback。

`tests/ui/smoke.mjs:332-336` 之所以能 PASS，是因为测试自己注入的是 string 文件名（`shot-20260619-214152-332181.png`），没走真实后端路径——这正是"测试全绿但生产炸了"的经典契约漂移。

**影响**：
- 控制台"设备实时画面"卡在真实运行中**永远显示空**，且 `<img>` 的 src 是个无效 URL，浏览器会刷一次 404。
- `liveBadge`/`liveShotNote` 文案错乱。
- 状态轮询每秒把整张 PNG base64 走一次线缆，1 KB/s × N 用户都跑一条连接 → 网络/CPU 浪费。

**最小修复方向（任选其一，给一个最小 diff 示意）**：
- A. 后端改为写盘 + 返回文件名（与 `_save_debug_screenshot` 已有逻辑合并，`runner.py:186-197`）：
  ```diff
  --- a/xyzw_auto_clicker/runner.py
  +++ b/xyzw_auto_clicker/runner.py
  @@ RunnerState.snapshot @@
  -    "last_screenshot": self.last_screenshot,
  +    # 真实截图仅落盘给调试页用，前端只看名字
  +    "last_screenshot": self.last_screenshot_name,
  @@ _run loop @@
  -                state.last_screenshot = screenshot
  +                shot_path = self._save_debug_screenshot(screenshot, tag="live")
  +                state.last_screenshot_name = shot_path.name if shot_path else None
  ```
- B. 前端容忍两种形态（兼容老数据 + 后续迁移）：
  ```diff
  --- a/static/app.js
  +++ b/static/app.js
  @@ renderLiveShot @@
  -  const name = state.last_screenshot || null;
  +  // 后端目前回 base64 字节，新版本应回文件名；判断是不是合法文件名
  +  const raw = state.last_screenshot;
  +  const name = typeof raw === "string" && /^[\w.\-]+$/.test(raw) ? raw : null;
  ```

**建议**：先选 A（后端真因），同时给 B 的边界保护兜底；前者消除网络放大，后者保护历史快照。两步共 ~20 行 diff。

### 1.2 后端 `TaskConfig` 字段前端不消费 · P1

**位置**：
- 后端：`xyzw_auto_clicker/models.py:30-38` 定义了 `scale_min/max/step`、`roi_band`、`freeze_guard`、`freeze_threshold`、`freeze_max_waits`。
- 前端：`static/app.js:280-298` `currentPayload()` 与 `app.js:157-174` `planFingerprint()` 均未触达 `freeze_threshold / freeze_max_waits / roi_band`。

**影响**：
- 用户改不了这些参数（UI 不暴露），但后端会用 TaskConfig 默认值把它们落进 `data/plans/*.json`（`xyzw_auto_clicker/plans.py`）。
- `planFingerprint` 缺这三个键 → 老方案文件被读回后必然被判成"有未保存改动"（`activePlanSnapshot=null` 或不匹配）。

**最小 diff 示意**：
```diff
--- a/static/app.js
+++ b/static/app.js
@@ planFingerprint @@
     threshold: Number(p.threshold),
     max_misses: Number(p.max_misses),
     scale_min: Number(p.scale_min ?? DEFAULT_SCALE_MIN),
     scale_max: Number(p.scale_max ?? DEFAULT_SCALE_MAX),
     scale_step: Number(p.scale_step ?? DEFAULT_SCALE_STEP),
     freeze_guard: p.freeze_guard !== false,
+    freeze_threshold: Number(p.freeze_threshold ?? 3.0),
+    freeze_max_waits: Number(p.freeze_max_waits ?? 4),
+    roi_band: p.roi_band ?? null,
   });
```
（不一定要在 UI 暴露；只要把指纹补齐，"有未保存改动"的误报就消失。）

### 1.3 `state.last_match.tap_count` 与后端真实语义微差 · P2

**位置**：`static/app.js:1206`、`xyzw_auto_clicker/runner.py:116-132`。

后端第 0 轮的 `tap_count=1`（`runner.py:46`），其它轮用 `repeat_tap_count`；前端 UI 文案写的是"连点 N"（`app.js:1206`）。当第一轮后端发了 `tap_count: 1` 时，前端还显示"连点 1"——对用户而言看起来像"匹配成功了但只点了 1 次"，而那本就是设计如此。P2 文案/精度问题，不影响功能。

---

## 2. 资源（P2）

### 2.1 `app.css` 体积膨胀点

**位置**：`static/app.css`。

- `.btn-icon` 在 `:1` 和 `:2` 两处定义：`app.css:545-548`（基础）与 `app.css:1437-1463`（方案卡专用）。后者的尺寸（`30px × 30px`）和前者的通用尺寸（`36px`）不一致，重复定义让维护时容易踩坑。
- `.shot-layout` 在 `app.css:2133-2140` 的 1080px 断点里被声明了两遍（`grid-template-columns: minmax(0, 1fr)` 重复写）。
- 多个 `.pill` 仅靠 `--success-bg/--success-fg/--success-br` 切色，新增语义色时还得加新类。可改用 `data-tone="warn"` + 一段规则（与 `.alert` 一致即可，省 5 个类）。

**最小 diff 示意（合并重复 `.shot-layout`）**：
```diff
 @media (max-width: 1080px) {
   .console-grid,
   .shot-layout {
-    grid-template-columns: minmax(0, 1fr);
-  }
-  .shot-layout {
     grid-template-columns: minmax(0, 1fr);
   }
   .run-config-body {
     grid-template-columns: minmax(0, 1fr);
   }
 }
```

**未用 JS 排查**：纯 vanilla，无打包；唯一的"潜在未用"是 `escapeHtml` 用于 `planRoles` / `renderPlanChips`（`app.js:579,598`），别处都靠 `textContent`。这是合理的，没冗余。

### 2.2 状态轮询频率固定 1s · P2

**位置**：`static/app.js:1381` `setInterval(pollState, 1000);`。

任务未运行时也按 1 Hz 拉 `/api/tasks/state`（回包里还可能含 base64 PNG，叠加 §1.1 后放大约 30×）。建议：

**最小 diff 示意**：
```diff
--- a/static/app.js
+++ b/static/app.js
@@ init @@
-  setInterval(pollState, 1000);
-  pollState();
+  let timer = null;
+  const schedule = (ms) => { clearInterval(timer); timer = setInterval(pollState, ms); };
+  schedule(isLocked ? 1000 : 4000);
+  pollState();
+  // pollState 内部根据 state.is_running 调 schedule(1000) 或 schedule(4000)
```
跳到的阈值（`>running → 1s / <idle → 4s`）只是经验值；跑 100ms 也会让 toast/告警显得呆滞，按 4s 已是日常节流底线。

### 2.3 toast 无上限 · P2

**位置**：`static/app.js:81-97`。

任务出错刷出十几条后，toast 队列会无限增长，body 末端的 `toast-host` 高度被撑爆，遮挡控制台。最小 diff：

```diff
   host.appendChild(item);
+  while (host.children.length > 5) host.firstElementChild.remove();
   const remove = () => {
```

---

## 3. 可访问性 a11y（P1）

### 3.1 视图用 `role="tabpanel"` 但没有 `role="tablist"` · P1

**位置**：`templates/index.html:95, 273, 465, 496, 568`；侧栏 `<button data-nav="console">`（`templates/index.html:26` 等）。

WAI-ARIA 把 `tabpanel` 设计成"必须和同级的 `tab` 配对，且被 `tablist` 包裹"。当前结构是：
```html
<nav class="nav">
  <button data-nav="console" aria-current="page">控制台</button>
  ...
</nav>
<main>
  <section class="view is-active" role="tabpanel" aria-label="控制台">...</section>
  ...
</main>
```
没有 `role="tablist"`，没有 `role="tab"`，没有 `aria-controls`。屏幕阅读器（VoiceOver、NVDA）会按"无主控键的面板"读，键盘用户切换视图也没有标准 `Tab/Shift+Tab` 锚点。

**最小 diff 示意**：
```diff
--- a/templates/index.html
+++ b/templates/index.html
@@ nav buttons @@
- <button class="nav-item" type="button" data-nav="console" aria-current="page">
+ <button class="nav-item" type="button" role="tab" id="tab-console"
+         data-nav="console" aria-current="page" aria-controls="view-console">
@@ view sections @@
- <section class="view is-active" id="view-console" role="tabpanel" aria-label="控制台">
+ <section class="view is-active" id="view-console" role="tabpanel"
+         aria-labelledby="tab-console">
@@ nav wrapper @@
- <nav class="nav" aria-label="主导航">
+ <nav class="nav" role="tablist" aria-label="主导航">
```
配合 `app.js` 里 `setView` 多设 `aria-selected="true|false"` 即可。

### 3.2 进度环缺失数值可读性 · P2

**位置**：`templates/index.html:115-124`、`static/app.css:1092-1136`。

`<div class="ring" id="ring" role="img" aria-label="执行进度">` 是个纯装饰 SVG。屏幕阅读器读到 "执行进度" 但读不到 `0 / 10`、`进度 30%` 等关键值。`aria-label` 是静态字符串。

**最小 diff 示意**：
```diff
--- a/static/app.js
+++ b/static/app.js
@@ pollState @@
   $("ringValue").textContent = `${state.clicked || 0} / ${state.target || 0}`;
+  $("ring").setAttribute("aria-label",
+    `执行进度：已完成 ${state.clicked || 0}，目标 ${state.target || 0}，${state.progress_percent || 0}%`);
```
或更轻：`aria-label` 初始写"执行进度环"，每次轮询同步 `$("ring").setAttribute("aria-label", ...)`。

### 3.3 `<dl>` 滥用非 `<dt>/<dd>` 子节点 · P1

**位置**：`templates/index.html:211-227`。

```html
<dl class="card quick-list">
  <div class="card-head">...             <!-- 不合法 -->
  <div class="quick-row"><dt>...</dt><dd>...</dd></div>
  ...
</dl>
```
HTML5 允许 `<dl>` 直接放 `<div>`（每 `<div>` 内一对 `<dt>/<dd>`），但 `<dl>` 内裸放 `<div class="card-head">` 里再放 `<span>` 之后再 `<dt>` 是不规范的，VoiceOver/Safari 会跳过。

**最小 diff 示意**：
```diff
--- a/templates/index.html
+++ b/templates/index.html
- <dl class="card quick-list" style="margin: 0">
-   <div class="card-head">
-     <span class="card-title">执行概要</span>
-     <span class="card-title-note">随参数实时更新</span>
-   </div>
+ <section class="card quick-list" style="margin: 0">
+   <div class="card-head">
+     <span class="card-title">执行概要</span>
+     <span class="card-title-note">随参数实时更新</span>
+   </div>
   <div class="quick-row"><dt>...</dt><dd>...</dd></div>
   ...
- </dl>
+ </section>
```
（语义上"执行概要"不是定义列表，更接近"参数摘要面板"，改 `<section>` 更准。）

### 3.4 状态环无键盘可达替代 · P2

环只是个 `<div role="img">`，盲用户无法获取"已点击 N / 目标 M"。`#ringValue` 在 SVG 外有可见的 `<div>`，但 `#ringLabel` 是状态文本。屏幕阅读器能读到这两个，但 `role="img"` 会让 SR 把整个子树当图像处理——能读到的就是 `aria-label` 那个静态串。

**修法同 §3.2**：把 `role="img"` 换成 `<div role="status" aria-live="polite">`，轮询时更新 `aria-label` 或干脆让 SR 直接读 `ringValue/ringLabel`。

### 3.5 减少动画已正确实现 · ✅

`static/tokens.css:205-211` 在 `prefers-reduced-motion: reduce` 下把所有 `--dur-*` 归 0；`pill.live .pill-dot` 的呼吸、`live-pulse`、`view-in` 都依赖时长变量，自动停。无需改。

### 3.6 对比度复核（基于 tokens.css）

已抽查：
- `--success-fg #157a3e` on `--success-bg #e8f6ee`：约 4.8:1 ✓
- `--warn-fg #9a5a06` on `--warn-bg #fdf2e1`：约 4.9:1 ✓
- `--danger-fg #bb2d1e` on `--danger-bg #fcecea`：约 4.6:1 ✓
- `--text-tertiary --ink-500 #857c6d` on `--surface-canvas --ink-050 #f8f6f2`：约 4.3:1（11px/12px 字号下 AA 通过，13px 字号下 AAA 通过）
- `--brand-700 #835a18` on `--brand-050 #fdf8ee`：约 4.6:1 ✓

**深色主题**：
- `--success-fg #6cd49a` on `--success-bg #17301f`：约 5.7:1 ✓
- `--warn-fg #e0ad52` on `--warn-bg #32230d`：约 6.1:1 ✓
- `--danger-fg #ef9f92` on `--danger-bg #34191a`：约 5.4:1 ✓
- `--text-secondary #bdb7c4` on `--surface-canvas #17161a`：约 9.1:1 ✓

唯一一处可能踩坑：**`#marquee-size` 字号 11px + 前景 `#fff` on `--danger-fg` 背景**（`static/app.css:1939-1944`）。白/红对比约 4.5:1，仅 11px 字号下擦边 AA（3:1）。文字是尺寸标签可读性优先，建议维持。

### 3.7 焦点环系统存在但 `.alert-close` 例外 · P2

**位置**：`static/app.css:1043-1045`。

`.alert-close:focus-visible` 用 `outline: 2px solid currentColor; outline-offset: 1px;`，与全站 `:focus-visible` 用 `box-shadow` 不同。键盘用户切到关闭按钮时焦点形状与全站不一致。

**最小 diff 示意**：
```diff
- .alert-close:focus-visible {
-   outline: 2px solid currentColor;
-   outline-offset: 1px;
- }
+ .alert-close:focus-visible {
+   outline: none;
+   box-shadow: var(--focus-ring);
+ }
```

---

## 4. 交互 / UX（P1）

### 4.1 轮询节流同 §2.2 · 已在 §2.2 给出 diff

### 4.2 表单校验：上限跨字段不互锁 · P2

**位置**：`templates/index.html:339` `<input ... max="10000" ...>` 与 `<input ... min="0.1" max="30">` 等。

`<input type="number">` 的 `min/max` 仅在用 `form.checkValidity()` 时校验，代码里只用 `Number(value)`。HTML 校验没接上，例如 `interval = 999` 仍可输入，只是后端会在 `models.py:62` 抛 ValueError 再被 toast 出来。建议补一个 inline `reportValidity()`：

```diff
--- a/static/app.js
+++ b/static/app.js
@@ renderSummaries (新增一处 hook) @@
   $("metricClickCount").textContent = p.click_count > 0 ? `${p.click_count}` : "—";
+  for (const id of ["clickCount", "postClickWait", "repeatTapCount", "repeatTapGap",
+                    "interval", "jitter", "maxMisses", "scaleMin", "scaleMax", "scaleStep"]) {
+    const el = $(id);
+    if (el.value !== "" && !el.checkValidity()) el.reportValidity();
+  }
```
不影响主流程，只是给视觉/AT 一个标准提示。

### 4.3 错误展示：toast 之外缺少持久位置 · P2

`runAlert` 是好实践（`app.js:1098-1145`），但 `#runAlertClose` 把 `dismissedAlert = alertSignature` 写进全局，签名变了又弹出（OK），但 toast 自己消失后，用户关掉 alert 后就只剩 log。log 在另一视图里。**移动端** nav 折叠成底栏，"运行日志"是图标按钮，没文字标签（`static/app.css:2169-2185`），触达率最低。

**最小 diff 示意（底栏 nav-item 始终保留文字 aria-label）**：
```diff
--- a/templates/index.html
+++ b/templates/index.html
   <button class="nav-item" type="button" data-nav="logs" aria-label="运行日志">
```
（`<span>运行日志</span>` 在 900px 断点里被 `:nth-child` 显示但 `aria-label` 没设；设上后 SR/键盘用户随时能找到日志。）

### 4.4 移动端断点：560-900 之间没有中间档 · P2

**位置**：`static/app.css:2133-2231`。

560px 以下：指标卡变 2 列、按钮变大。560-900px 之间（小平板/大手机横屏）：仍是桌面布局，但侧栏已被压成底栏，挤成 5 个图标按钮挤不下。实测视口宽 800px 时 5 个图标 + 文字会撞 wrap。

**最小 diff 示意**：
```diff
+ @media (max-width: 900px) and (min-width: 720px) {
+   .nav-item { font-size: var(--fs-11); padding: var(--sp-2) 4px; }
+ }
```

### 4.5 视图切换动画与 reduced-motion 兼容 · ✅

`tokens.css:205-211` 把所有时长归零，`view-in` / `pop-in` / `live-pulse` 自动停。OK。

### 4.6 `applyRecommended()` 设 `activePlanFilename=null` 后 dirty 立刻出现 · P2

**位置**：`static/app.js:746-758`。

```js
activePlanFilename = match ? match.filename : null;
activePlanSnapshot = match ? planFingerprint(match.payload || {}) : null;
```
当不存在名为"推荐宝箱方案"的方案时，`activePlanFilename=null`，但 `lastPayload = currentPayload()`（已套了推荐参数）。`renderActivePlan()` 计算 `planFingerprint(currentPayload()) !== null` → dirty=true。视觉上出现"参数已改动"——但用户什么都没改，只是点了按钮。`applyRecommended` 已 toast 提示过，无 dirty 反馈的必要。

**最小 diff 示意**：
```diff
   lastPayload = currentPayload();
+  // 套用推荐相当于一次新载入，不需要 dirty 标记
+  if (!match) activePlanSnapshot = planFingerprint(currentPayload());
   renderActivePlan();
```

### 4.7 `consoleCount` 镜像同步：编辑光标冲突已经规避 · ✅

`app.js:432-436` 用 `document.activeElement !== mirror` 守门，光标不会被夺走。OK。

---

## 5. 代码质量（P2）

### 5.1 vanilla JS 结构良好，无明显反模式

- 模块化以 IIFE 不可行（外层是 `<script>` 直接挂 window 变量）；命名空间 `const $ = id => ...` 简单够用。
- 状态集中在 `let` 顶部（`app.js:53-67`），单文件可读。
- 事件委托处理动态卡片：`planGrid`、`consolePlanChips` 都走父节点委托（`app.js:1294, 1316`）。
- 全局 `applyLock()` 是 disabled 唯一归属（注释明确，遵守）。

### 5.2 事件绑定无泄漏风险 · ✅

- `bindSelection` 的 `window.mousemove/mouseup`（`app.js:874, 893`）只挂一次，`init` 单次执行。
- `loadTemplates` 每次 `grid.innerHTML = ""` 重建卡片，旧 `<input>` 节点随 DOM 一起 GC，连带监听器回收。无泄漏。

### 5.3 内存增长：state.logs `deque(maxlen=200)`（`runner.py:27`）后端有上限；前端 `lastLogs` 不截断但每秒钟重写 innerHTML，DOM 节点本身 GC 回收。无内存增长。

### 5.4 `applyLock` 在 1Hz 轮询 + 任意输入事件都会重跑 · P2

**位置**：`app.js:994-1015`。

每次 `pollState` 结束都会调 `applyLock()`（间接通过 `renderSummaries → applyLock`）。`renderSummaries → applyLock → querySelectorAll('[data-lock], #savePlan, ...')`（10+ 选择器）每次都重扫全文档。模板 100+ 张 + 方案卡 500+ 时会肉眼可见的抖动。优化方案：缓存节点引用在 init 时一次取齐。

**最小 diff 示意**：
```diff
+const lockables = []; // 在 init() 里填充一次
 function applyLock() {
-  document.querySelectorAll("[data-lock], #savePlan, ...").forEach((item) => {
+  for (const item of lockables) {
     if (item.id === "runAlertAction" && !isLocked) { item.disabled = false; return; }
     if (item.id === "updatePlan") item.disabled = isLocked || item.hidden;
     else item.disabled = isLocked;
   }
```
（仅在标签数极大时才考虑；当前规模属于过度优化，记一笔即可。）

### 5.5 数值/时长格式化单点 · ✅

`fmtNum`、`fmtDuration`、`friendlyError` 集中在 `app.js:99-151`。OK。

---

## 6. 测试覆盖（ui/*）评价

`tests/ui/README.md` 268 项断言，按职责拆分五个文件：
- `smoke.mjs`：视图路由、状态字段联动、响应式（包含移动端 390×844）、无障碍抽查（`smoke.mjs:327-328` 验 toast 容器有 `aria-live`）。
- `console-config.mjs`：方案芯片、次数双向镜像、未保存改动、空态、锁定。
- `plan-selection.mjs`：方案卡载入、dirty 检测、次数控件、估算、长时告警、非法值、运行时锁定。
- `plan-actions.mjs`：破坏性操作（设默认/删除）+ 自动还原；含 Esc 取消、内联二次确认不调原生 confirm 的断言。
- `template-picker.mjs`：采样页下拉与覆盖提示。
- `capture-flow.mjs`：真机截图链路。

**缺口**：
1. **没有任何 a11y 自动断言**：仅 smoke.mjs 验过 1 个 nav 文案、1 个 toast `aria-live`。键盘焦点路径、ARIA roles 配对、对比度都靠肉眼。
2. **没有视觉回归**：截图只用作 `mobile-console.png` 一帧供人眼查；没有像素 diff。
3. **`renderLiveShot` 在真实后端跑的路径没被测**：`smoke.mjs:332` 用的是注入的 string，掩盖了 §1.1 的契约漂移。
4. **没有降级断言**：网络断、API 500 等错误路径只靠 `pageErrors` 兜底，没有显式断言"出错了 UI 不白屏"。

**建议（最小 diff / 加项，不动代码）**：
- 在 `smoke.mjs` 后追加 `tests/ui/a11y-tablist.mjs`，断言 §3.1 的 `role="tab"`/`aria-controls`。
- 在 `tests/ui/live-shot.mjs` 写一个依赖真实 `/api/tasks/state` 的最小冒烟：拿真实响应，断言 `typeof state.last_screenshot === "string"`，一旦是 base64 就红。
- 引入 `axe-core` 单文件（~600KB gz）跑一次 `axe.run(document)`，把 violations 数与 a11y 一并纳入 CI；不需要改任何 UI。

---

## 7. 优先级总览

| # | 项 | 级别 | 估时 | 文件:行 |
|---|---|---|---|---|
| 1.1 | `last_screenshot` base64 vs filename 契约错位 | **P0** | 0.5d | `runner.py:26,41,100` ↔ `app.js:1161` |
| 3.1 | nav 用 `tabpanel` 却无 `tablist/tab` | P1 | 0.5h | `templates/index.html:26,95,273,465,496,568` |
| 3.3 | `<dl>` 子节点不合规 | P1 | 0.5h | `templates/index.html:211-227` |
| 1.2 | TaskConfig 字段前端不消费，dirty 误报 | P1 | 1h | `app.js:157-174` |
| 4.6 | `applyRecommended` 假 dirty | P2 | 5min | `app.js:746-758` |
| 3.2 / 3.4 | 进度环 aria-live | P2 | 0.5h | `app.js:1063` 附近 |
| 3.7 | `.alert-close` 焦点环风格 | P2 | 5min | `static/app.css:1043-1045` |
| 2.2 | 1Hz 轮询节流 | P2 | 1h | `app.js:1381` |
| 2.3 | toast 上限 5 条 | P2 | 5min | `app.js:91` 之后 |
| 4.2 | number 输入接 `checkValidity` | P2 | 1h | `app.js:1378` 之前 |
| 4.4 | 720-900px 断点 | P2 | 0.5h | `static/app.css:2146` |
| 2.1 | `.shot-layout` 重复声明 | P2 | 5min | `static/app.css:2138-2140` |
| 5.4 | applyLock 节点缓存 | P2 (规模敏感) | 1h | `app.js:994-1015` |

---

## 8. 可立刻落地的"最小改动"清单（无破坏性）

按价值/工时排：
1. `app.js:1161` 加 1 行 typeof 判断，让前端先不炸（§1.1 方案 B）。
2. `app.js:157-174` `planFingerprint` 补 3 个字段，消除 dirty 误报（§1.2）。
3. `app.js:91` 后插 1 行 toast 上限（§2.3）。
4. `templates/index.html:211` `<dl>` → `<section>`（§3.3）。
5. `static/app.css:2138-2140` 删 3 行重复（§2.1）。
6. `app.js:746` 后补 2 行消除假 dirty（§4.6）。

合计 ~10 行 diff，零行为变更风险；可作为 PR-1 一并提交。