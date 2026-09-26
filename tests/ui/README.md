# WebUI 端到端冒烟测试

通过 Chrome DevTools Protocol 驱动 Edge，验证界面渲染、交互与关键流程。**冒烟脚本（`smoke.mjs` / `console-config.mjs` / `plan-selection.mjs` / `plan-actions.mjs` / `template-picker.mjs` / `capture-flow.mjs`）不需要安装任何 npm 依赖**（使用 Node 内置 `fetch` 与 `WebSocket`）。仅 `a11y.mjs` 需要 Playwright + axe-core，依赖由仓根 `npm ci` 拉到 `tests/ui/node_modules/`。

## 前置

1. 启动 WebUI：`.\start.ps1 -NoBrowser`（默认 `http://127.0.0.1:8765`）
2. 启动一个开启远程调试的 Edge：

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  --headless=new --disable-gpu --no-sandbox `
  --remote-debugging-port=9333 `
  --user-data-dir="$env:TEMP\edge-cdp-profile" about:blank
```

`a11y.mjs` 不需要上述 Edge；它自带 Playwright + Chromium，自动从 `npm install` 安装到 `tests/ui/node_modules/`。默认连 `http://127.0.0.1:8999/`（与 docker-compose / ci.yml 的 docker-up 一致），可用 `APP_URL` 覆盖。

## 运行

```bash
node tests/ui/smoke.mjs           # 86 项：视图路由、方案载入、模板校验、次数控件、主题、响应式、无障碍、后端状态字段联动、多尺度识别高级项
node tests/ui/console-config.mjs  # 61 项：控制台「运行配置」卡的方案芯片载入、次数镜像（双向）、未保存改动、空态、锁定（非破坏性）
node tests/ui/plan-selection.mjs  # 44 项：方案卡载入是否真的生效、未保存改动检测、次数档位/步进/耗时估算/实时校验（非破坏性）
node tests/ui/plan-actions.mjs    # 31 项：设为默认、删除（内联二次确认 / Esc 取消 / 锁定）、删除使用中方案的状态清理（会临时建方案并自动还原）
node tests/ui/template-picker.mjs # 31 项：采样页选择已有模板名、覆盖提示与按钮语义、手动输入的同步判定（非破坏性，不需要模拟器）
node tests/ui/capture-flow.mjs    # 14 项：截图拉取、拖拽框选、裁剪保存（需连接模拟器）
node tests/ui/a11y.mjs           # 自动 a11y：Playwright + axe-core 遍历 5 视图，serious/critical 视作 fail
```

合计 **267 项**断言 + 5 视图 a11y 自检。

全部以退出码 0 表示通过，非 0 表示有失败项。

## 说明

- `capture-flow.mjs` 会真实调用 `/api/templates/crop` 写入 `data/templates/zz-smoke-test-crop.png`，运行后请手动删除该测试模板。
- **删除是软删除**：`DELETE /api/plans/{file}` 会把方案文件 `os.replace` 到 `data/plans-trash/`，不是真正抹掉。`plan-actions.mjs` 因此在结束时会**同时清理 `data/plans/` 与 `data/plans-trash/`**，否则连跑几次会堆出一串 `zz-自检方案.json`。测试断言只看"列表里消失了"，不看文件是否被删除。
  > 之所以不用 `unlink()`：部分运行环境注入了 `sitecustomize.py`，把 `Path.unlink` 重定向到回收站并带**批量删除守卫**（同一会话累计超过阈值会 `SystemExit(1)`），会导致接口 500、用户侧表现为"删除按钮偶发失灵"。
- `plan-actions.mjs` 会创建 `zz-自检方案` 并临时改写默认标记，**结束时自动删除并还原**；即使中途失败也会在 `finally` 里清理。它会用 spy 断言"删除流程全程没有调用原生 `confirm()`"。若该方案名已存在于 `data/plans/`，删除会把它移走——请勿用真实方案名。
- **不要在测试里等原生对话框**。应用已不再使用 `confirm()`；而一旦有对话框弹出，`Runtime.evaluate` 会一直挂住（CDP 等渲染器），脚本既不报错也不继续，只会在超时后被杀掉。历史上正是这个现象帮我们定位到"删除按钮为什么没反应"。
- `console-config.mjs` 全程只**载入**方案与改表单值，不写任何方案文件；**结束时把默认方案还原**。它不假设"哪个方案是默认"，而是从 `plansCache` 动态取默认方案与对照方案（默认标记可能被上一次 `plan-actions.mjs` 或用户操作改变）。
- 视口尺寸由脚本通过 `Emulation.setDeviceMetricsOverride` 指定；如果拖拽相关的断言失败，先确认脚本是否成功设置了足够大的视口。
- **重新加载必须跳一次 `about:blank`**：`Page.navigate` 到与当前完全相同的 URL（含 `#hash`）不会真正重新加载，页面会保留上一轮的内存状态（`activePlanFilename`、表单值等），"初始态"断言会莫名失败。脚本里另加了 `&t=<时间戳>` 兜底。
- 涉及 `renderLiveShot` / `renderRunAlert` / `setLocked` 的断言必须在同一个 `Runtime.evaluate` 里设置并读取，否则会被 1 秒的状态轮询覆盖成真实后端状态。这是「先等待再读取」写法的固有竞态，不是被测逻辑的问题。
- 对已经取到的值做断言时用 `record(name, actual, expected)`，可以避免上面那个二次求值的竞态；`check()` 适合一次求值即定的场景。
- `check()` / `ev()` 把表达式包在**同步** IIFE 里，所以表达式内**不能写 `await`**（会变成 SyntaxError，且 `ev()` 不检查异常会静默什么都不做）。需要等 Promise 就直接 `return someAsyncFn()`，靠 CDP 的 `awaitPromise` 等待。
- 脚本结尾必须 `process.exit(0)`：Node 的 `fetch`（undici）保留 keep-alive 连接会吊住事件循环，脚本"跑完了但不退出"，输出还烂在 stdio 缓冲区里；一旦被超时杀掉就什么都不打印，看起来像挂死在第一行。

## 需要真实后端配合的联动验证

以下场景依赖后端产出真实数据（截图、失败计数、暂停原因），不是纯前端断言：

```bash
# threshold=1.0 + max_misses=1：截图会被保存、匹配必然失败、任务立即暂停，不会产生任何点击
curl -X POST http://127.0.0.1:8765/api/tasks/chest/start -H "Content-Type: application/json" -d '{
  "device_id":"<你的设备ID>","first_template_names":["video-first-open10.png"],
  "template_names":["video-repeat10.png"],"click_count":10,"interval_seconds":0.8,
  "jitter_seconds":0,"post_click_wait_seconds":1,"repeat_tap_count":2,
  "repeat_tap_gap_seconds":0.12,"threshold":1,"max_misses":1}'
```

随后 `GET /api/tasks/state` 应出现 `last_screenshot`、`misses: 1`、`last_error`，且页面首屏出现告警条并显示设备画面。
