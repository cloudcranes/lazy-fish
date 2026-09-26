# lazy-fish 优化路线图

> **生成日期**: 2026-09-25
> **性质**: 只读整合。基于 backend-audit.md（28 条）与 t2-frontend-audit.md（13 条）。
> **评分维度**: 性价比（effort / impact）× 风险（泄露 / 数据丢失 / UX 坏掉）。
> **三阶段**: 阶段 1（≤ 1 周，必做）/ 阶段 2（≤ 1 月，值做）/ 阶段 3（长期，备选）。
> **owner 缩写**: BE = 后端 / FE = 前端 / FS = 跨端契约 / OPS = 部署 / TEST = 测试。

---

## 0. 合并去重

合并要点：

| # | 主题 | 重复来源 | 结论 |
|---|---|---|---|
| M-1 | **契约漂移**：`state.last_screenshot` 后端发 bytes (base64)，前端当文件名 | BE §1 / FE §1.1 | **跨端 P0**，合并入阶段 1 PR-1 |
| M-2 | **可观测性缺位**：无结构化 logging、无 healthcheck 业务字段、无 metrics | BE §3 (3 条) | BE 单端，阶段 1 PR-3 |
| M-3 | **截图 / 模板 / 方案 路径遍历** 三个 GET 端点同病 | BE §4.1, §4.2 | BE 单端，阶段 1 PR-1 |
| M-4 | **`device_id` 注入** + **CropRequest 越界** + **`adb tap` 整数注入** | BE §4.3, §4.5 | BE 单端，阶段 1 PR-1 |
| M-5 | **轮询节流**：1Hz 不分状态 / 后端发 PNG 放大 30× | BE §1-中 / FE §2.2 | **跨端 P1**，阶段 2（依赖 M-1 修复） |
| M-6 | **`planFingerprint` 字段缺漏 → 假 dirty** | FE §1.2, §4.6 | FE 单端，阶段 1 PR-2 |
| M-7 | **a11y**：tablist/tab 缺失、`<dl>` 滥用、焦点环风格不统一 | FE §3 全节 | FE 单端，阶段 1 PR-2 |
| M-8 | **资源 / 体积**：toast 上限、`shot-layout` 重复、ROI 缓存、opencv-headless | BE §1-高 / FE §2 / OPS §5 | 跨端+部署，阶段 2 |
| M-9 | **CI / 构建**：apt 缓存、HEALTHCHECK 单行、release 死代码 | OPS §5 | OPS 单端，阶段 1 PR-4 |
| M-10 | **可维护性**：Makefile lint、requirements-dev、`load_plan` schema 校验 | BE §4-中, §5, §6 | BE+OPS，阶段 2 |

剩余单端条目按归属贴入对应阶段。

---

## 1. 阶段 1（≤ 1 周，必做）

> 入选标准：影响真实运行或安全面，diff ≤ 50 行，回归风险低。

### PR-1：安全 + 契约（跨端，必须第一个合并）

| 项 | 来源 | 改动文件 | 工作量 | 验收 |
|---|---|---|---|---|
| **M-1a** 后端改回 `last_screenshot = name`（写盘取名） | BE §1-高 P1（runner.py）/ FE §1.1 A 方案 | `runner.py:26,41,100,186-197` | 0.5d | `GET /api/tasks/state` 返回 `last_screenshot` 为合法 `shot-*.png` 文件名；`last_screenshot_name` 字段存在；`tests/ui/smoke.mjs` 增加断言 `typeof === "string"` |
| **M-1b** 前端容忍历史数据：非文件名则 `name=null` | FE §1.1 B 方案 | `app.js:1161` | 0.5h | 真实后端跑时控制台"设备实时画面"显示当前帧；不再发 `404 /api/screenshots/<base64>` |
| **M-3** `/api/screenshots/{name}` `/api/templates/{name}` `/api/plans/{filename}` 白名单正则 + `resolve().parents` | BE §4.1, §4.2 | `app.py:76-95,122-135` | 1h | `pytest` 加用例：`..%2Fetc%2Fpasswd` → 400；含 `.json` 合法名 → 200 |
| **M-4a** `_safe_device_id` 正则白名单 `[A-Za-z0-9._:-]{1,64}` | BE §4.3 | `app.py:62,91,100,102` + 新工具函数 | 0.5h | `device_id="foo;rm -rf /"` → 400 |
| **M-4b** `CropRequest.x/y` `Field(le=10000)` | BE §4.5 | `app.py:33-34` | 5min | Pydantic 自动校验；测试覆盖 `x=100000` → 422 |

**owner**：FS（跨端契约）。**diff 总量**：~40 行。**回归口径**：`make test` 全绿 + `tests/ui/capture-flow.mjs` 真机链路跑通 + 新增 4 条 pytest。

### PR-2：前端契约 + a11y 零风险快修

| 项 | 来源 | 改动文件 | 工作量 | 验收 |
|---|---|---|---|---|
| **M-6a** `planFingerprint` 补 `freeze_threshold / freeze_max_waits / roi_band` | FE §1.2 | `app.js:157-174` | 0.5h | 老方案文件读回后无"参数已改动"误报 |
| **M-6b** `applyRecommended` 无匹配时同步 `activePlanSnapshot` | FE §4.6 | `app.js:746-758` | 5min | 点"应用推荐"后不再触发 dirty 提示 |
| **M-7a** nav 加 `role="tablist"` + 子按钮 `role="tab" aria-controls`；section 加 `aria-labelledby` | FE §3.1 | `templates/index.html:26,95,273,465,496,568` + `app.js` `setView` | 0.5h | axe-core 0 violations on tablist；键盘 ←/→ 切换视图 |
| **M-7b** `<dl class="card quick-list">` → `<section>` | FE §3.3 | `templates/index.html:211-227` | 0.5h | HTML5 校验通过；VoiceOver 正确朗读 |
| **M-7c** `.alert-close:focus-visible` 改用 `box-shadow: var(--focus-ring)` | FE §3.7 | `app.css:1043-1045` | 5min | 键盘 Tab 到关闭按钮焦点样式与全站一致 |
| **2.3** toast 上限 5 条 | FE §2.3 | `app.js:81-97` | 5min | 连续 20 条错误时 `toast-host` 不撑爆 |
| **2.1** 删除 `.shot-layout` 重复声明 | FE §2.1 | `app.css:2133-2140` | 5min | 1080px 断点布局不变；CSSBytes ↓ 0.2KB |

**owner**：FE。**diff 总量**：~30 行。**回归口径**：`tests/ui/smoke.mjs` + 新增 `tests/ui/a11y-tablist.mjs` 验证 ARIA 配对。

### PR-3：可观测性（BE 一次性补齐）

| 项 | 来源 | 改动文件 | 工作量 | 验收 |
|---|---|---|---|---|
| **3.1** `logging.basicConfig` + `runner.state.logs` 同步写 stderr | BE §3-高 #1 | `app.py:18` 后 | 5min | 容器化部署时 `docker logs` 可见结构化日志 |
| **3.2** `/api/health` 返回 `{status, templates_count, plans_count}` | BE §3-中 #3 | `app.py:166` 后 | 0.5h | `curl /api/health` 200；HEALTHCHECK 改打此路由 |
| **2.5** `Dockerfile HEALTHCHECK` 单行 + 改打 `/api/health` | OPS §5-低 | `Dockerfile:35-37` | 5min | 容器 `docker inspect` 健康检查间隔生效 |
| **3.3** `state.clicked` 命中/未命中/帧时延写入 `runner.state.logs` JSON 行 | BE §3-高 #2 | `runner.py:138` 后 | 0.5h | UI 日志面板 + 容器 stderr 都可看到 `{event:"click",ms:32}` |

**owner**：BE + OPS。**diff 总量**：~25 行。**回归口径**：本地 `curl /api/health` 返 200；`pytest -k health`；CI smoke `for i in $(seq 20)` 替代 `sleep 8`。

### PR-4：部署 + 杂项（一并收口）

| 项 | 来源 | 改动文件 | 工作量 | 验收 |
|---|---|---|---|---|
| **5.1** `opencv-python-headless` 替代 `opencv-python` | OPS §5-高 | `requirements.txt` + `Dockerfile:23` | 0.5d | 镜像体积 270MB → 150MB；`docker images` 验证 |
| **5.2** CI `actions/cache` 缓存 `/var/lib/apt/lists` | OPS §5-中 #3 | `.github/workflows/ci.yml:21` 前 | 0.5h | CI 总时长 ↓ 4min；`runs.workflow.duration` 监控 |
| **5.3** `docker-compose.yml` 加 `deploy.resources.limits` + `:cached` 注释 | OPS §5-中 #5,#6 | `docker-compose.yml:10,17-18` | 5min | `docker-compose up` 启动后单容器内存 ≤ 512MB |
| **5.4** `Makefile lint` 排除 `app` 模块 | OPS §5-低 #1 | `Makefile:30-31` | 5min | `make lint` 通过；不触发 `repair_template_names()` |
| **5.5** `release.yml` 删 `Generate changelog excerpt` 死代码 | OPS §5-低 #3 | `release.yml:63-84` | 5min | push tag 后 release workflow 不再 echo 重复内容 |
| **6.1** 新增 `requirements-dev.txt`，CI 改装 | BE §6-中 | `requirements-dev.txt` (新) + `ci.yml:34` + `Makefile:19` | 5min | `pip install -r requirements-dev.txt` 含 pytest |
| **4.3** `release.yml changelog` 死代码（已合并入 5.5） | — | — | — | — |

**owner**：OPS + BE。**diff 总量**：~50 行（多为新建小文件）。**回归口径**：`docker build` 时间 ↓、`make lint` 通过、`make test` 仍用 `pytest`。

---

## 2. 阶段 2（≤ 1 月，值做）

> 入选标准：影响性能或可维护性，diff 50~150 行，需配合回归测试。

### PR-5：性能（matcher + adb + plans IO）

| 项 | 来源 | 改动文件 | 工作量 | 验收 |
|---|---|---|---|---|
| **1.1** matcher ROI numpy view 缓存 `(left,top,w,h)` | BE §1-高 #1 | `matcher.py:299,364` | 0.5d | `tests/perf/test_match_throughput.py`（新）：稳态帧时延 ↓ 5ms；CI 加 perf gate |
| **1.2** `_templates` LRU 上限 32 | BE §2-中 #4 | `matcher.py:144-147` | 0.5h | 模板批量导入 100 张后 `_templates.__len__() ≤ 32` |
| **1.3** `plans.py` IO 用 `asyncio.to_thread` 包 | BE §2-中 #1 | `plans.py:64,87,174` | 0.5h | 方案 100 个时 `GET /api/plans` p95 < 50ms |
| **1.4** `adb._run` 改 `subprocess.Popen + timeout.kill()` | BE §2-高 #1 | `adb.py:26-33` | 0.5d | adb hang 时 10s 内抛 `AdbError("ADB 命令超时")` 不卡事件循环 |
| **1.5** `freeze_max_waits` 默认 4 → 6 | BE §1-低 #3 | `models.py:38` | 5min | 长动画（>4 帧）不再强行放行；UI 文案同步更新 |
| **2.1** 轮询节流：running=1s / idle=4s | FE §2.2 + BE §1-中 | `app.js:1381` | 1h | 网络面板：空闲期 XHR/s ↓ 4× |
| **2.2** `AdbError` 加 `code: int \| None` 字段 | BE §3-中 #4 | `adb.py:14` | 5min | 错误分类预埋；前端本期不消费 |

**owner**：BE + FE。**回归口径**：`tests/test_core.py` 全绿；前端 smoke `network.waitForResponse` 验证空闲 4s 间隔。

### PR-6：契约收紧 + 可维护性

| 项 | 来源 | 改动文件 | 工作量 | 验收 |
|---|---|---|---|---|
| **6.2** `load_plan` 走 `PlanPayload.model_validate` | BE §4-中 #5 | `plans.py:140` | 1h | 损坏 JSON → 422 而非 500；前端收到明确错误 |
| **3.4** `applyLock` 节点缓存 | FE §5.4 | `app.js:994-1015` + init 一次取齐 | 1h | 模板 100+ 时 1Hz 轮询主线程 < 16ms |
| **3.5** 进度环 `aria-label` 动态更新 + `aria-live="polite"` | FE §3.2, §3.4 | `app.js` pollState | 0.5h | NVDA 朗读 "已完成 5 / 目标 10，50%" |
| **3.6** 720-900px 中间断点 | FE §4.4 | `app.css:2146` | 0.5h | 800px 视口 5 个图标 + 文字不撞 wrap |
| **3.7** 数字 input 接 `checkValidity()` | FE §4.2 | `app.js` renderSummaries | 1h | 越界值输入时浏览器原生错误提示 |
| **3.8** `<dl>` 内的 card-head 改 `<section>` 结构（PR-2 已处理，重复 skip） | — | — | — | — |
| **6.3** `Dockerfile` multi-stage（build + runtime） | OPS §5-中 #2 | `Dockerfile` 全文 | 1d | 镜像体积再 ↓ 30MB；构建依赖不残留 |
| **6.4** `release.yml` 多平台 gha cache scope 隔离 | OPS §5-中 #4 | `release.yml:60-61` | 0.5h | amd64/arm64 构建互不破坏缓存 |

**owner**：BE + FE + OPS。**回归口径**：完整 `make test` + `tests/ui/*` 全绿；Docker 本地 `docker run` 健康检查通过。

---

## 3. 阶段 3（长期，备选）

> 入选标准：当前规模无 ROI 或新功能触发后再做。

| 项 | 来源 | 工作量 | 触发条件 |
|---|---|---|---|
| matcher 多尺度 batch `cv2.batchDistance` | BE §1-中 #2 | 2d | scale 数 > 20 时；当前 16 档差异 < 5% |
| adb 长连接进程池 `adb shell` | BE §1-中 #3 / §2-高 #1 | 1w | click/screenshot > 5/s 时；当前 30ms 热路径已够 |
| Pyright / mypy strict 接入 | — | 3d | models.py 字段超 30 个时 |
| `safety` / `pip-audit` 加 CI | — | 0.5d | 上 PyPI / 引入第三方依赖时 |
| OpenTelemetry auto-instrumentation | BE §3-低 | 1w | 上 k8s / 多实例时 |
| 多 Runner 实例并发（in-process） | BE §2-高 #1 后半 | 2w | 单容器多设备需求出现时 |
| 视觉回归（Playwright pixel diff） | FE §6 缺口 #2 | 1d | UI 改版频率 > 1/月时 |
| axe-core 接入 CI | FE §6 缺口 #1 | 0.5d | 发版前 |
| `STOP_FILE` 改 env 注入 | BE §4-低 #1 | 0.5h | 上 k8s 时 |
| release changelog 由 `release-please` 接管 | OPS §5-低 #3 | 1d | 引入 conventional commits 时 |
| Pydantic `TaskConfig` 增字段校验（crop_x/y/w/h） | BE §4-中 #5 后半 | 0.5d | 后续 plan 直接配置 crop 时 |
| OTel / Prometheus `/metrics` 导出 | BE §3-高 #2 | 1d | metrics 需求出现 |

**owner**：按归属逐项。**口径**：无验收标准，等触发条件。

---

## 4. 跨端契约总览（PR-1 后应更新的契约）

> PR-1 落地后立即更新 `docs/contract.md`（若无则创建），固化：

```yaml
GET /api/tasks/state:
  last_screenshot: string | null       # 之前: bytes (base64)
  last_screenshot_mtime: float | null  # 新增，便于前端 ETag
  is_running: bool

GET /api/health:
  status: "ok" | "degraded"
  templates_count: int
  plans_count: int
  uptime_seconds: float
```

**约定**：所有路径参数 GET 端点（`/api/{screenshots,templates,plans}/*`）均走 `_safe_filename(name)` 工具函数（regex + `resolve().parents` 二次校验）。

---

## 5. 工作量 / 工时合并表

| 阶段 | PR 数 | 总工作量 | 涉及文件 |
|---|---|---|---|
| 阶段 1 | 4 | ~3 人日 | ~12 文件（含新建 2） |
| 阶段 2 | 2 | ~5 人日 | ~10 文件 |
| 阶段 3 | — | 按需 | — |

阶段 1 推荐合并为 1 个 sprint（5 工作日），PR-1 单独先合以解锁契约层。

---

## 6. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| PR-1 后端改 `last_screenshot` 字段名后，老前端缓存报错 | 中 | 中 | 前端 PR-1b 加 typeof 兜底；灰度回滚方案：保留 `last_screenshot_bytes` 字段 1 个 release |
| opencv-headless 替换引入 import 不兼容 | 低 | 高 | CI 先跑 `make test` + `tests/ui/capture-flow.mjs` 真机链路 |
| axe-core 接入 CI 后 violations 数 > 50 | 中 | 低 | PR-2 已修 3 项高优 a11y，其余本期只记录不修 |
| matcher ROI view 缓存引入内存增长 | 低 | 中 | 缓存上限 32（同 `_templates` 套路），pytest 加内存断言 |
| adb Popen 改造破坏 Windows 兼容 | 低 | 高 | CI 加 windows-latest runner；先合 PR-4 再上 PR-5 |

---

## 7. 验收总览

| 阶段 | 必跑验证 |
|---|---|
| 阶段 1 | `make test`（含新增 pytest）+ `tests/ui/*` + `docker build && docker run` + `curl /api/health` |
| 阶段 2 | 阶段 1 + 新增 perf test（matcher 帧时延 / plans p95） |
| 阶段 3 | 无强制验证 |

---

## 8. 状态快照

> 本节记录各阶段在 `main` 上的实际落地证据。所有 SHA 与测试结果以归档时点为准。

### 8.1 阶段 1（已落地，2026-09-25）

**commit 链**（5 commits）：

| SHA | 说明 |
|---|---|
| `9b449ad` | feat: PR-1 安全+契约 |
| `0f14aa9` | feat: PR-3 可观测性 (logging + /api/health + 容器健康检查) |
| `ce8e33c` | feat: PR-2 前端契约+a11y |
| `e7c10ed` | feat: PR-4 部署收口 |
| `256e9cd` | stage1-closure: HEALTHCHECK to /api/health, pytest tests/, smoke curl /api/health |

**测试结果**：75 passed（`tests/test_core.py` + `tests/test_pr1_security.py` + `tests/test_pr3_observability.py` + 既有测试）。

### 8.2 阶段 2（已落地，2026-09-25）

**commit 链**（2 commits）：

| SHA | 说明 |
|---|---|
| `59dd192` | feat: PR-5 性能 |
| `1a2f75a` | feat: PR-6 契约+可维护 |

**测试结果**：84 passed（含阶段 1 的 75 条 + `tests/test_pr6_contract.py` 新增 9 条契约 smoke）。

### 8.3 阶段 3（备选，未启动）

按 §3 表中触发条件按需启动，当前无强制验收标准。

---

> 关联文档：[docs/RECOGNITION-RESEARCH.md](./RECOGNITION-RESEARCH.md)（识别根因 + 实验数据）。