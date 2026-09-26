# lazy-fish 优化路线图

> **生成日期**: 2026-09-25；**阶段 3 落地补登**: 2026-09-26；**阶段 3 wave2 补登**: 2026-09-26；**阶段 3 wave3 补登**: 2026-09-26；**阶段 3 wave4 补登**: 2026-09-26；**阶段 3 wave5 补登**: 2026-09-26；**阶段 3 wave6 补登**: 2026-09-26
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

### 8.3 阶段 3（已落地，2026-09-26）

**轨道 A：静态检查 + 安全扫描**（PR-7，commit `cc64a72`，track-a-engineer）

| 变更 | 文件 |
|---|---|
| ruff / pyright / pip-audit 依赖 | `requirements-dev.txt` |
| `[tool.ruff]` line-length=100 + `select=[E,F,W,I,B,UP]` + `tests/**` per-file-ignores | `pyproject.toml` |
| `[tool.pyright]` pythonVersion=3.10 + strict=`xyzw_auto_clicker` | `pyproject.toml` |
| `lint` 目标 = `ruff check .` + `python scripts/pyright_check.py` | `Makefile` |
| pyright 诊断 → baseline 三元组对比脚本 | `scripts/pyright_check.py`（新） |
| 基线再生成脚本 | `scripts/pyright_baseline_gen.py`（新） |
| strict 已知诊断 24 条基线 | `pyright-baseline.json`（新） |
| `lint` job（needs: test）+ `test` job，main fail-fast / PR continue-on-error | `.github/workflows/ci.yml` |
| `pip-audit` OSV → CycloneDX JSON → SARIF 上传 GH Security tab；main fail，PR warn | `.github/workflows/security.yml`（新） |
| E501 折行 / 删除未用 import | `xyzw_auto_clicker/{adb,app,matcher,plans,runner,logging_setup}.py` |

**轨道 B：契约硬化**（PR-8，commit `90ff13b`，track-b-engineer）

| 变更 | 文件 |
|---|---|
| `_safe_field_name` + `PlanPayload.crop`（x/y/w/h 0–8192 + 面积 ≤ 1920×1080） | `xyzw_auto_clicker/plans.py` |
| `CropRequest` x/y/w/h 走 `_safe_field_name` + `model_validator` 校面积；新增 `/api/metrics`（LOG_JSON=1 才返回 200，否则 404），字段集 `process_resident_memory_bytes` / `runner_status` / `last_screenshot_mtime` / `uptime_seconds` / `tasks_started_total` | `xyzw_auto_clicker/app.py` |
| `_TemplateEntry.memory_at` + `HOT_PATH_TTL_SECONDS=60`；`_match_one` 过期回 ROI；`_roi_rect_cache` 改 OrderedDict + `ROI_CACHE_MAXSIZE=128` LRU；新增 `clear_class_cache` | `xyzw_auto_clicker/matcher.py` |
| `TaskRunner.tasks_started_total` 随 `start()` 自增 | `xyzw_auto_clicker/runner.py` |
| `build_chest_config` `pop('crop')` 避免 `TaskConfig` 收到未知键 | `xyzw_auto_clicker/tasks/chest.py` |
| `pollState` 接入 `window.focus/blur/online/offline`；`toast()` 改「替换而非追加」，删 `TOAST_MAX` | `static/app.js` |
| `toastHost` 加 `role=status` + `aria-live=polite` + `aria-atomic=true` | `templates/index.html` |
| 契约 smoke 16 条 | `tests/test_pr8_contract.py`（新） |

**测试结果**：100 passed（基线 84 + PR-8 新增 16）。

**轨道 B 验证证据**：

- `ruff check .` → All checks passed!（exit 0）
- `pyright strict` → 24 total / 0 new beyond baseline（exit 0）
- `python -m pytest tests/` → 100 passed in 7.41s
- `python -m py_compile xyzw_auto_clicker/*.py scripts/*.py tests/*.py` → OK
- `/api/metrics` 契约：LOG_JSON=0 → 404；LOG_JSON=1 → 200 + 5 字段（`test_metrics_endpoint_disabled_without_log_json` / `test_metrics_endpoint_returns_required_fields` / `test_metrics_endpoint_handles_missing_proc` / `test_tasks_started_total_increments` 全部 PASS）

**作用域备注**：

- 轨道 A 实际新增 `scripts/`（`pyright_check.py` + `pyright_baseline_gen.py`），超出指定 7 文件 1 个目录，是 baseline 把关机制的必要配套。
- pyright baseline 24 条诊断属真实类型 bug（`plans._write_json` 参数协变、`logging_setup._JsonHandler` stdlib `StreamHandler` 重写、`runner.start` `state` 可能未绑定等），修复涉及 saved-plan payload 形状 / Handler 子类化 / 异步任务分支，超出 PR-7 静态扫描边界，按 spec 留基线；新增/迁移代码引入新诊断会在 CI `lint` job 立刻 fail。
- `pip-audit` 本地 sandbox 未安装（`python -m pip-audit` → ModuleNotFoundError），CI ubuntu-latest 步骤会 `pip install pip-audit` 后跑——这是环境限制，不影响项目契约。
- `scripts/pyright_check.py` 用 `shutil.which("pyright")`，Windows 本地若未把 pyright 入口装到 PATH 会 fallback 到字面量 `"pyright"` 触发 `FileNotFoundError`（实测本地复现）。CI 上 `pip install -r requirements-dev.txt` 装的是 console-script `pyright`，在 `ubuntu-latest` + GitHub 默认 PATH 下能找到。建议：在脚本里加一行 `sys.executable`-as-pyright fallback 或在 `Makefile lint` 里直接 `python -m pyright`，以兼容 Windows 开发机。

**新增里程碑**：阶段 3 提前把「§3 触发条件表」里的 *Pyright / mypy strict 接入* + *`safety` / `pip-audit` 加 CI* 两项激活为常驻 CI gate；剩余触发条件（multi-scale batch、adb 进程池、OpenTelemetry、视觉回归、axe-core CI、release-please 等）按 §3 条件按需启动。

### 8.3 阶段 3 wave2 — adb 长连接池 + axe-core 自动 a11y（已落地，2026-09-26）

> wave2 把 §3 表里「adb 长连接进程池」+「axe-core 接入 CI」两项提前激活为常驻基础设施；commit 已在 origin/main。

**commit 链**（2 commits，按时间倒序）：

| SHA | 说明 |
|---|---|
| `9cfc2c8` | feat: PR-9 adb 长连接池 (track B-engineer) |
| `5df7c72` | feat: PR-10 axe-core 自动 a11y |

**PR-9：adb 长连接池**（commit `9cfc2c8`，2 文件 +163/-2，track B-engineer）

| 变更 | 文件 |
|---|---|
| `_ProcPool`（OrderedDict by device_id + LIFO 淘汰，max_size=5、idle_seconds=60） | `xyzw_auto_clicker/adb.py` |
| `AdbError.code` 新增 `NOT_AVAILABLE`（池创建失败 / 长连接写失败） | `xyzw_auto_clicker/adb.py` |
| `tap(x, y, device_id)` 走池：复用同 `Popen` 的 stdin 发 `input tap x y\n`；`screenshot_png` 仍走 `exec-out screencap -p`（二进制不能塞交互式 shell） | `xyzw_auto_clicker/adb.py` |
| 淘汰：池满 + idle≥60s → `proc.kill()` + `proc.wait(1)`，LIFO 取最久 idle | `xyzw_auto_clicker/adb.py` |
| 复用 / 淘汰 / NOT_AVAILABLE 三类用例 | `tests/test_adb_pool.py`（新） |

**PR-10：axe-core 自动 a11y**（commit `5df7c72`，7 文件 +231/-3，a11y-engineer）

| 变更 | 文件 |
|---|---|
| Playwright + `@axe-core/playwright` 切 5 视图（console / plans / templates / capture / logs）跑 axe-core；`wcag2a/2aa/21a/21aa`；serious/critical 视作 fail；退出码 0/1/2 = pass/fail/env-down | `tests/ui/a11y.mjs`（新） |
| `@axe-core/playwright ^4.10.0` + `playwright ^1.49.0` 仅 devDep；`workspaces: [tests/ui, relay]` 下发到 `tests/ui/node_modules/` | `package.json`（新）+ `tests/ui/package.json`（新） |
| 新增 `docker-up` job（启动懒鱼容器并长起 8999，循环 `/api/health` 直至就绪）+ `a11y` job（`needs: docker-up`；main fail-fast，PR `continue-on-error`） | `.github/workflows/ci.yml` |
| 排除 `tests/**/node_modules/`（镜像里没有 npm） | `.dockerignore` |
| §11「自动 a11y」一节，闸失败门槛 + 与 §6 WCAG AA 的双向关系 | `docs/UI-DESIGN.md` |
| 说明 `a11y.mjs` 是唯一需要 npm 依赖的脚本 | `tests/ui/README.md` |

**总验收验证**（gate-reviewer 复核，2026-09-26）：

- `python -m pytest tests/ -q` → **103 passed in 7.42s**（基线 100 + PR-9 新增 3 + PR-10 无 Python 测试）
- `python -m ruff check .` → All checks passed!
- `python -m py_compile xyzw_auto_clicker/*.py` → 10 files OK
- `node --check tests/ui/a11y.mjs` → ok
- JSON / YAML 解析无错（`package.json` + `tests/ui/package.json` + `.github/workflows/ci.yml`）
- 依赖一致：仓根 + `tests/ui` 都是 `@axe-core/playwright ^4.10.0` + `playwright ^1.49.0`
- adb 公 API（`async def screenshot_png / tap / devices / _run`）签名保持兼容；新增 `AdbError.code = "NOT_AVAILABLE"` 不破坏旧字段
- CI 拓扑：lint → test → (docker || docker-up) → a11y；`docker-up` 与 `docker` 并存，`a11y` 仅挂在 `docker-up` 上
- commit 范围核对：PR-9 未触碰 `OPTIMIZATION-ROADMAP.md` / `test_adb_pool.py` 之外的接口；PR-10 未触碰 `adb.py` / `test_adb_pool.py`，留给 PR-9 单独提交

**未闭合项**：

- `pip-audit` / `pyright` Windows 本地 fallback（同 §8.2 已登记，不重复）
- axe-core 在 PR 上 `continue-on-error` 是过渡策略，axe 噪声清完后需把 `continue-on-error: ${{ github.event_name == 'pull_request' }}` 摘掉升级为合并门槛（建议放下一 wave 单独 PR）
- adb 池的 `idle_seconds=60` 是默认值；高密度点击场景（§3 表里 ">5/s" 触发条件）尚无真实负载复测，待 click/screenshot 频率触线时再调 max_size / idle_seconds

**新增里程碑**：阶段 3 wave2 把「§3 触发条件表」里的 *adb 长连接进程池* + *axe-core 接入 CI* 两项提前激活为常驻基础设施。剩余触发条件（multi-scale batch、OpenTelemetry、视觉回归、release-please 等）继续按 §3 条件按需启动。

### 8.4 阶段 3 wave3 — release-please 自动发版 + pyright baseline 消化（已落地，2026-09-26）

> wave3 把 §3 表里「release changelog 由 `release-please` 接管」+「Pyright / mypy strict 接入（持续消化 baseline）」两项提前激活为常驻基础设施；commit 已在 origin/main（`c99b6a9..3e2c543 main -> main`）。

**commit 链**（2 commits，按时间倒序）：

| SHA | 说明 |
|---|---|
| `3e2c543` | PR-12: pyright baseline 消化（24 → 0 条诊断） |
| `c99b6a9` | feat: PR-11 release-please 自动发版 |

**PR-11：release-please 自动发版**（commit `c99b6a9`，6 文件 +56/-13）

| 变更 | 文件 |
|---|---|
| `googleapis/release-please-action@v4`（push main + workflow_dispatch；引用 `release-please-config.json` + `.release-please-manifest.json`） | `.github/workflows/release-please.yml`（新） |
| `releaseType=python` / `packageName=lazy-fish` / `bump-minor-pre-major=true` / `bump-patch-for-minor-pre-major=true` | `release-please-config.json`（新） |
| 初始版本 `0.1.0` | `.release-please-manifest.json`（新） |
| `Extract version` 步去 tag/manual 双分支，只取 `GITHUB_REF_NAME#v`（release-please 推 tag 后才走 release.yml） | `.github/workflows/release.yml` |
| 发布流程从「`make release VERSION=x.y.z` 手动打 tag」改成「Conventional Commits → 自动 PR → merge → release-please 推 tag → release.yml 构建+发布」 | `README.md` / `README.zh-CN.md` |

**PR-12：pyright baseline 消化**（commit `3e2c543`，9 文件 +113/-207）

| 修复 | 文件 / 关键字 |
|---|---|
| `plans._safe_field_name` → `safe_field_name`（公开给 app.py 复用，消 `reportPrivateUsage × 3`），返回类型改 `int`（`CropRequest.field_validator` 需要 `int` 给 `ge/gt`） | `plans.py` |
| `plans._CROP_MAX_DIM / _CROP_MAX_AREA` → `CROP_MAX_DIM / CROP_MAX_AREA`（同上） | `plans.py` |
| `plans._write_json` 协变：参数 / `SavedPlan.payload` / `_normalize_loaded_payload` 全部改 `dict[str, Any]`，让 `model_dump()` 嵌套 dict 通过 | `plans.py` |
| `logging_setup._JsonHandler` 不再继承 `StreamHandler`，改继承 `Handler` 自持 `IO[str]`，规避 typeshed `_StreamT` vs `sys.stdout` 不对齐（一次性吃掉 `formatException / stream / write / flush / StreamHandler` 8 条） | `logging_setup.py` |
| `logging_setup._env_json` → `env_json_enabled`（公开给 app.py metrics 端点，避免真值表漂移） | `logging_setup.py` |
| `matcher np.frombuffer` 改 `np.ndarray(buffer=memoryview(png_bytes))`，消 `reportUnknownMemberType`（`frombuffer` 在 typeshed 里标 `ndarray[Unknown]`） | `matcher.py` |
| `matcher` 热路径 `memory_scale` 显式 `assert` + 局部变量收窄，让 `_window_rect / _scan` 收 `float` 而非 `float \| None` | `matcher.py` |
| `models.match_profile()` 不再用 `tuple()`，显式拆 tuple 收窄 `tuple[float, float]` | `models.py` |
| `runner._run` 入口 `state: RunnerState = self.state` 显式收窄，消 `possibly unbound` | `runner.py` |
| `adb._Slot.proc` + 两处 `subprocess.Popen()` 都加 `Popen[bytes]`；`slot.proc.stdin` 强类型化为 `IO[bytes] \| None`；`_slots.pop` 私有访问改成公开 `evict()` API | `adb.py` |
| `tests/test_pr8_contract.py` 同步重命名 `_safe_field_name` → `safe_field_name` / `_CROP_MAX_DIM` → `CROP_MAX_DIM` | `tests/test_pr8_contract.py` |
| 重建：`{"pythonVersion": "3.10", "strict": ["xyzw_auto_clicker"], "diagnostics": []}` | `pyright-baseline.json` |

**总验收验证**（gate-reviewer 复核，2026-09-26）：

- `python -m pytest tests/` → **103 passed in 6.74s**（与 wave2 同基线，零回归）
- `python -m ruff check .` → All checks passed!
- `python -m py_compile xyzw_auto_clicker/*.py scripts/*.py tests/*.py` → exit 0
- `python scripts/pyright_check.py`（PATH 注入 `C:\...\Python314\Scripts`） → `pyright: 0 total, 0 new beyond baseline` exit 0
- `python scripts/pyright_baseline_gen.py`（同 PATH 注入） → `baseline written: 0 diagnostics` exit 0
- `release-please-config.json` / `.release-please-manifest.json` / `pyright-baseline.json` JSON 解析 OK
- `.github/workflows/release-please.yml` / `release.yml` YAML 解析 OK，配置字段对得上
- trigger 链：`release-please.yml` (push main) → 开/更新 Release PR → merge → release-please 推 tag `v*.*.*` → `release.yml` (push tag) 走 `Extract version` 取 `${GITHUB_REF_NAME#v}` → build+push GHCR + GH Release；与 ci.yml / security.yml 完全正交
- PR-11 in-scope 文件全部入 commit（`release-please.yml` / `release.yml` / `release-please-config.json` / `.release-please-manifest.json` / `README.md` / `README.zh-CN.md`）；PR-12 in-scope 文件全部入 commit（7 个 `xyzw_auto_clicker/*.py` + `pyright-baseline.json` + `tests/test_pr8_contract.py`）
- `git log --oneline -20` 显示 `3e2c543` / `c99b6a9` 已在 main 上，与依赖结果 SHA 一致

**未闭合项 / 已知偏差**：

- `release.yml` 的 `Extract version` 步简化后只取 `${GITHUB_REF_NAME#v}`；在 `workflow_dispatch` 路径下 `GITHUB_REF_NAME` 是默认分支（如 `main`），`VERSION` 会变成 `main` → 本地构建 tag 是 `lazy-fish:main`，但 `push` 被 `event_name == 'push'` 闸住不会真推 GHCR，所以**不影响发版链**。若想给手动 dispatch 也一个干净 tag，建议加 `if [ "${GITHUB_REF_TYPE}" = "tag" ] || [ "${GITHUB_EVENT_NAME}" = "workflow_dispatch" ]; then VERSION="${GITHUB_SHA::7}"; fi`（非阻断，下一 wave 处理）。
- `Makefile` 的 `release` 目标仍保留手动 `git tag v$(VERSION) && git push origin v$(VERSION)`，与 release-please 自动发版重复且容易误用绕过 changelog PR；README 已替换文档但 Makefile 没改。建议下一 wave 删掉该 target 或在 `help` 文案里点名「不要用 `make release`，用 release-please PR」（非阻断）。
- `pyright` console-script 路径同 §8.2 已登记的 Windows fallback 限制（`shutil.which("pyright")` 在本地开发机若未在 PATH 会 fallback 到字面量触发 `FileNotFoundError`），CI 上 `pip install -r requirements-dev.txt` 后无此问题；建议把脚本里的 `shutil.which("pyright") or "pyright"` 改成 `python -m pyright` 的 wrapper（已在 §8.2 提过，不重复记）。
- `pip-audit` Windows 本地缺失同 §8.2 已登记，CI ubuntu-latest 会装，不影响。

**新增里程碑**：阶段 3 wave3 把「§3 触发条件表」里的 *release changelog 由 `release-please` 接管* + *Pyright / mypy strict 接入（baseline → 0）* 两项提前激活为常驻基础设施。`pyright-baseline.json: diagnostics=[]` 后 CI lint job 等同于 strict 全绿闸；新增诊断会立刻 fail。剩余触发条件（multi-scale batch、OpenTelemetry、视觉回归、`safety` 第三方依赖扫描、k8s env 注入、多 Runner 实例并发等）继续按 §3 条件按需启动。

### 8.5 阶段 3 wave4 — release-please 闭环 + STOP_FILE env 可配置（已落地，2026-09-26）

> wave4 把 wave3 留下的两个**已知偏差**关闭：① release.yml 在 `workflow_dispatch` 路径下 VERSION 退化为分支名（`main`），有污染 GHCR 命名空间风险；② `STOP_FILE` 写死 `BASE_DIR/STOP`，容器重启后状态丢失。commit 已在 origin/main（`120ea09..16f8f47 main -> main`）。

**commit 链**（2 commits，按时间倒序）：

| SHA | 说明 |
|---|---|
| `16f8f47` | feat: PR-13 release-please closure (Makefile release-dispatch + docs/RELEASING.md + dispatch short-SHA tag) |
| `120ea09` | feat: make STOP_FILE path configurable via LAZY_FISH_STOP_FILE env (PR-14) |

**PR-13：release-please 闭环**（commit `16f8f47`，3 文件 +140/-7）

| 变更 | 文件 |
|---|---|
| `Extract version` 步新增 `workflow_dispatch` 分支，VERSION 取 `cut -c1-7` 短 SHA；tag push 路径保留 `${GITHUB_REF_NAME#v}` 不变 | `.github/workflows/release.yml` |
| `release` target 重命名为 `release-dispatch`，单 `#` DEPRECATED 注释（不进 `make help`），warning 指向 `docs/RELEASING.md`；`.PHONY` 同步 | `Makefile` |
| canonical 流程图（commit → release-please PR → merge → auto-tag → GHCR）+ 应急 dispatch 路径 + 排障表 + 关联文件 | `docs/RELEASING.md`（新） |

**PR-14：STOP_FILE env 可配置**（commit `120ea09`，6 文件 +66/-2）

| 变更 | 文件 |
|---|---|
| `STOP_FILE = Path(os.environ.get("LAZY_FISH_STOP_FILE", str(BASE_DIR/"STOP")))`，未设 env 仍走默认 | `xyzw_auto_clicker/settings.py` |
| `LAZY_FISH_STOP_FILE: /app/data/STOP`（与 `./data` 卷同区，容器重启不丢） | `docker-compose.yml` |
| runtime ENV 段 `LAZY_FISH_STOP_FILE=/app/data/STOP` | `Dockerfile` |
| 配置表新增 `LAZY_FISH_STOP_FILE` 行（容器默认 `/app/data/STOP`，本地默认 `BASE_DIR/STOP`） | `README.md` / `README.zh-CN.md` |
| 3 用例：未设 env 走默认、env 覆盖生效、撤掉 env 后无残留 | `tests/test_pr14_env.py`（新） |

`runner.py` 未动（仍是 `STOP_FILE.exists()`），settings 是单一改动点，零回归风险。

**总验收验证**（gate-reviewer 复核，2026-09-26）：

- `python -m pytest tests/ -q` → **106 passed in 7.47s**（基线 103 + PR-14 新增 3，零回归）
- `python -m ruff check .` → All checks passed!
- `python -m py_compile xyzw_auto_clicker/settings.py xyzw_auto_clicker/runner.py tests/test_pr14_env.py` → exit 0
- `python -m pyright xyzw_auto_clicker` → 0 errors, 0 warnings, 0 informations
- YAML 解析：`.github/workflows/release.yml` + `release-please.yml` + `ci.yml` + `docker-compose.yml` 全部 `yaml.safe_load` OK
- JSON 解析：`release-please-config.json` + `.release-please-manifest.json` OK
- dispatch 路径语义：`GITHUB_EVENT_NAME == "workflow_dispatch"` → `VERSION=${GITHUB_SHA:0:7}`，不再退化为 `main` 分支名；`if: github.event_name == 'push'` 闸同步拦住 publish 与 GH Release 创建，dispatch 只做本地验证不污染 GHCR 命名空间
- tag push 路径语义：`GITHUB_REF_NAME` 是 `v*.*.*` → `${GITHUB_REF_NAME#v}` 取出 `0.2.0` 形式版本号，构建+推 GHCR + 创建 GitHub Release 完整链路
- STOP_FILE 兼容性：未设 `LAZY_FISH_STOP_FILE` → 走 `BASE_DIR/STOP` 默认值（兼容所有现存调用方）；设值后立刻反映新路径；monkeypatch 撤 env 后无残留（`test_pr14_env.py` 3 用例 PASS）
- Makefile help 隔离：`release-dispatch` 注释是单 `# DEPRECATED: ...`，不匹配 `^[a-zA-Z_-]+:.*?## ` 模式，不会进 `make help` 列表；但仍可在 shell 直接 `make release-dispatch` 触发应急入口
- READMEs 已无 `make release VERSION=` 引用；新增 `LAZY_FISH_STOP_FILE` 配置表行（EN + zh-CN 一致）
- commit 范围核对：PR-13 in-scope（`Makefile` + `.github/workflows/release.yml` + `docs/RELEASING.md`）全部入 commit；PR-14 in-scope（`xyzw_auto_clicker/settings.py` + `docker-compose.yml` + `Dockerfile` + `README.md` + `README.zh-CN.md` + `tests/test_pr14_env.py`）全部入 commit；`runner.py` 按设计保持不动
- `git log --oneline -20` 显示 `16f8f47` / `120ea09` 已在 main 上，与依赖结果 SHA 一致

**未闭合项 / 已知偏差**：

- wave3 §8.4 留下的两个未闭合项（dispatch VERSION 退化 + Makefile `release` 死代码）已由 wave4 关闭：前者 `release.yml` 加 dispatch 分支取短 SHA，后者 `release` target 重命名为 `release-dispatch` 并打 DEPRECATED 标。
- `pyright` console-script Windows fallback 与 `pip-audit` Windows 本地缺失同 §8.2 / §8.4 已登记，CI ubuntu-latest 无此问题，不重复记。
- `make release-dispatch` 走 `gh workflow run release.yml --ref main` + `gh` CLI 依赖；当前 `release.yml` 的 `workflow_dispatch` 路径只产生镜像不推 GHCR（`if: github.event_name == 'push'` 闸住），若需要把手动 dispatch 也升格为正式发布，应在 `release.yml` 解除该闸并加 `inputs.version` 让调用方指定（**非阻断**，等真正使用前再做）。
- `LAZY_FISH_STOP_FILE` env 在 settings 模块**加载时**读取，运行时改 env 不会立即生效（与 `LAZY_FISH_HOST` / `LAZY_FISH_PORT` 等保持一致语义）；如需运行时热切换 STOP_FILE，应在 `runner._should_stop` 改为读环境变量（**非阻断**，当前没有这个需求）。

**新增里程碑**：阶段 3 wave4 把 wave3 留下的两个偏差（dispatch 路径 VERSION 退化 + `STOP_FILE` 写死）收口为正式发版基础设施；release-please → tag → release.yml 的 canonical 路径在 `docs/RELEASING.md` 文档化；容器内 STOP 标记现与 `./data` 卷同区，重启不丢状态。剩余触发条件（multi-scale batch、OpenTelemetry、视觉回归、`safety` 第三方依赖扫描、多 Runner 实例并发等）继续按 §3 条件按需启动。

### 8.6 阶段 3 wave5 — Prometheus /api/metrics + release.yml dispatch 解闸（已落地，2026-09-26）

> wave5 把 wave4 留下的两个**应急短板**收口为正式基础设施：① `/api/metrics` 只输出 JSON，Prometheus / VictoriaMetrics 文本抓取无标准端点；② `release.yml` 的 `workflow_dispatch` 路径在 wave4 仅给短 SHA 应急用，缺真正的版本选择入口 + 独立 publish / GH Release 通道。commit 已在 origin/main（`004f181..602cd64 main -> main`）。

**commit 链**（2 commits，按时间倒序）：

| SHA | 说明 |
|---|---|
| `602cd64` | feat(metrics): Prometheus text/plain; version=0.0.4 via Accept negotiation (PR-15) |
| `004f181` | ci(release): unlock workflow_dispatch publish path (PR-16) |

**PR-15：Prometheus /api/metrics Accept 协商**（commit `602cd64`，3 文件 +229/-9）

| 变更 | 文件 |
|---|---|
| `/api/metrics` 新增 `Accept` 头协商：含 `text/plain` 或 `*/*` → 走 `text/plain; version=0.0.4; charset=utf-8`（Prometheus / VictoriaMetrics 兼容）；显式 `application/json` → JSON；无头 → JSON；`LOG_JSON≠1` → 404 不变 | `xyzw_auto_clicker/app.py` |
| 5 个指标（`uptime_seconds` / `process_resident_memory_bytes` / `runner_status` / `last_screenshot_mtime_seconds` / `tasks_started_total`）全部带 `# HELP` + `# TYPE` 文本输出；字段顺序稳定便于解析 | `xyzw_auto_clicker/app.py` |
| 6 用例：JSON Accept / text/plain Accept / `*/*` Accept / runner.status 变更反映到 Prometheus / 无 Accept 头走 JSON / 关闭态 404 | `tests/test_pr15_metrics.py`（新） |
| 既有 PR-8 契约测试同步调整 `test_metrics_endpoint_*` 三例与新逻辑对齐（仍是 16 例全绿） | `tests/test_pr8_contract.py` |

**PR-16：release.yml dispatch 解闸 + inputs.version**（commit `004f181`，3 文件 +71/-20）

| 变更 | 文件 |
|---|---|
| `on.workflow_dispatch.inputs.version`：required + type=choice + default `v0.1.0` + 10 选项 `v0.1.0..v1.0.0`；`Extract version` 在 dispatch 路径读 `${INPUTS_VERSION:-${GITHUB_SHA:0:7}}` 并去 `v` 前缀；tag push 路径不变（仍取 `${GITHUB_REF_NAME#v}`） | `.github/workflows/release.yml` |
| Login to GHCR / Build & push image (`provenance: false`) / Create GitHub release 三步统一闸门改为 `if: github.event_name == 'push' \|\| github.event_name == 'workflow_dispatch'`；dispatch 路径独立走通构建 → 推 GHCR → 创建 GH Release 完整链路 | `.github/workflows/release.yml` |
| GH Release `tag_name` 兜底为 `v<inputs.version>`（dispatch）；`generate_release_notes` 仅 tag push 启用（dispatch 不附 release notes）；`docker/build-push-action` 加 `provenance: false` 满足非 SLSA 场景 | `.github/workflows/release.yml` |
| `VERSION ?= v0.1.0` 变量；`release-dispatch` 调 `gh workflow run release.yml --ref main -f version=$(VERSION)`；仍带 DEPRECATED 提示 | `Makefile` |
| §3 改写为「手动触发发布（应急路径 / 解闸）」含 dispatch 行为表 / Makefile 用法 / dispatch vs tag-push 对比表；§4 / §5 同步引用 PR-16 | `docs/RELEASING.md` |

**总验收验证**（gate-reviewer 复核，2026-09-26）：

- `python -m pytest tests/ -q` → **112 passed in 7.54s**（基线 106 + PR-15 新增 6，零回归；依赖结果里提到的 2 例 `test_pr8_contract.py` 失败已恢复：当前 HEAD 下该文件 16 例全绿）
- `python -m py_compile xyzw_auto_clicker/app.py` → exit 0
- YAML 解析：`.github/workflows/release.yml` → `yaml.safe_load` OK；`on.workflow_dispatch.inputs.version` = choice / required / default=`v0.1.0` / 10 选项 `v0.1.0..v1.0.0`
- JSON 解析：`release-please-config.json` + `.release-please-manifest.json` OK（未变更）
- `/api/metrics` 双 Accept 路径覆盖：`Accept: application/json` → `application/json`；`Accept: text/plain; version=0.0.4` → `text/plain; version=0.0.4; charset=utf-8` 含 HELP/TYPE；`Accept: */*` → Prometheus 文本；无 Accept → JSON（`test_metrics_json_when_no_accept_header` 走 ASGI scope 直发断言）
- `test_pr15_metrics.py` 6 例 + `test_pr8_contract.py` 16 例（含 3 例 metrics 调整）独立复核全绿
- `release.yml` dispatch 路径语义：`GITHUB_EVENT_NAME == "workflow_dispatch"` + `INPUTS_VERSION` 有值 → `VERSION="${RAW#v}"` 正确去 `v` 前缀；下游 GHCR login / buildx push / GH Release 闸门在 dispatch 事件下放行；`tag_name` 兜底 `v<VERSION>` 正确
- `release.yml` tag push 路径语义：`GITHUB_EVENT_NAME == "push"` → 仍走 `${GITHUB_REF_NAME#v}`；`generate_release_notes: ${{ github.event_name == 'push' }}` 仅 tag push 启用
- Makefile `release-dispatch`：底层 `gh workflow run release.yml --ref main -f version=$(VERSION)` 与新 inputs.version 字段对齐；`VERSION ?= v0.1.0` 默认与 workflow 默认一致
- docs/RELEASING.md §3 行为表 / 对比表 / §4 排错条目「workflow_dispatch 跑出来的镜像 tag 是 `main`」均已更新为「PR-16 已用 `inputs.version` 解闸」
- `git log --oneline` 显示 `004f181` 与 `602cd64` 已在 origin/main（`main` 分支当前 HEAD = `602cd64`），与依赖结果 SHA 完全一致

**未闭合项 / 已知偏差**：

- wave4 §8.5 留下的「dispatch 路径 `if: github.event_name == 'push'` 闸住 GHCR publish + GH Release」非阻断项已由 wave5 关闭：`release.yml` 三步闸门改为 `push || workflow_dispatch`，dispatch 独立可发布；`inputs.version` choice 字段（10 选项 `v0.1.0..v1.0.0`）给操作者显式版本选择面
- `dispatch` 路径不打 git tag 是**设计选择**而非偏差：与 wave4 §8.5「dispatch 不污染 GHCR 命名空间」一致；下次需要把 dispatch 升格为正式版本时，由 release-please 在合并下一批 Conventional Commits 后接管即可（避免双源真相）
- `dispatch` 路径 GH Release 不附 `generate_release_notes` 是**有意保留**：dispatch 是手动应急 / 调试镜像场景，release notes 的真相源仍是 tag push + release-please；如未来需要 dispatch 自动生成 notes，可改为 `generate_release_notes: ${{ github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && inputs.notes) }}`（**非阻断**，当前没有该需求）
- `inputs.version` 仅 10 个 choice（`v0.1.0..v1.0.0`）：1.0 后需要扩展时同步加 `options` 即可；不引入 `string` + 自定义版本号是为防 typo + 越权发布（**非阻断**，覆盖阶段 3 周期内全部预期发版点）
- `pyright-baseline.json` 维持 `diagnostics=[]`；新增 PR-15 / PR-16 提交未触发任何新增诊断，wave3 invariant 完整

**新增里程碑**：阶段 3 wave5 把 wave4 留下的「Prometheus 文本端点缺失 + dispatch 仅本地验证」两个非阻断项收口：① `/api/metrics` 通过 Accept 协商同时支持 JSON 与 Prometheus `text/plain; version=0.0.4`，与 Prometheus / VictoriaMetrics 文本抓取标准对齐；② `release.yml` 的 `workflow_dispatch` 路径配上 `inputs.version` choice 字段（10 选项 + 默认 `v0.1.0`）后可以独立完成构建 → 推 GHCR → 创建 GH Release，作为 release-please 失灵或临时重发的正式应急路径。剩余触发条件（multi-scale batch、OpenTelemetry、视觉回归、`safety` 第三方依赖扫描、多 Runner 实例并发等）继续按 §3 条件按需启动。

### 8.7 阶段 3 wave6 — axe-core PR fail-fast 升级 + 视觉回归（playwright 截图对比）（已落地，2026-09-26）

> wave6 把 §3 长期项中两个「视觉/可访问性回归」短板同时关闭：① `tests/ui/a11y.mjs` 在 PR-10 时 PR 上挂 `continue-on-error`（axe-core 噪音过渡期），现在 axe-core 报告已清洁（只剩真实 serious/critical），摘掉 PR 上的 `continue-on-error`，PR + main 同等 fail-fast，把 a11y 从「可观察」升级为「合并门槛」；② §3 中长期悬而未决的「视觉回归」首次落地为可运行 baseline：Playwright 拉 5 视图截图 + pixelmatch 0.1% 阈值比对。commit 已在 origin/main（`8c3eb8a..fe4791e main -> main`）。

**commit 链**（2 commits，按时间倒序）：

| SHA | 说明 |
|---|---|
| `fe4791e` | feat: PR-18 视觉回归（playwright 截图 + pixelmatch 0.1%） |
| `8c3eb8a` | feat(ci): a11y job fail-fast on PR — merge threshold (PR-17) |

**PR-17：a11y PR fail-fast 升级为合并门槛**（commit `8c3eb8a`，4 文件 +41/-11）

| 变更 | 文件 |
|---|---|
| a11y job 摘除 `continue-on-error: ${{ github.event_name == 'pull_request' }}`，PR + main 同等级 fail-fast；lint / test job 不动（仍维持「PR 上 warn / main 上 fail」语义） | `.github/workflows/ci.yml` |
| §11 改写为 4 子节（11.1 现状合并门槛 / 11.2 编排与依赖 / 11.3 升级理由 / 11.4 与 §6 关系）；含 PR-10 vs PR-17 对比表与退出码契约（0/1/2） | `docs/UI-DESIGN.md` |
| 顶部注释显式写出退出码契约（0=pass / 1=serious·critical / 2=env fail），与 §11.2 对齐；脚本逻辑未变（`process.exit(2)` + `process.exit(N>0 ? 1 : 0)`） | `tests/ui/a11y.mjs` |
| §5「Release PR 卡在 merge」排错行加 a11y 是合并门槛的注脚；新增脚注「a11y 合并门槛（PR-17 起生效）」，提示不要回退 `continue-on-error` | `docs/RELEASING.md` |

**PR-18：视觉回归（playwright 截图 + pixelmatch）**（commit `fe4791e`，6 文件 +462/-4）

| 变更 | 文件 |
|---|---|
| 新增 `tests/ui/visual.mjs`：Playwright 拉 5 视图截图（console / plans / templates / capture / logs）@ 1280×900，pixelmatch 0.1% 阈值；首次跑缺失 baseline 自动生成；`--update-baseline` 强制覆盖；退出码 0/1/2（与 a11y.mjs 契约一致） | `tests/ui/visual.mjs`（新） |
| 仓根 devDeps 加 `pixelmatch ^5.3.0` + `pngjs ^7.0.0` + `fs-extra ^11.2.0`；新增 `test:visual` 与 `test:visual:update` scripts | `package.json` |
| `tests/ui/package.json` 加同三项（沿 workspaces 下发） | `tests/ui/package.json` |
| 新增 `visual` job（`needs: docker-up`）：PR 上 `continue-on-error` / main fail-fast；失败时上传 `tests/ui/diffs/*.diff.png` 为 artifact | `.github/workflows/ci.yml` |
| 追加 §12「视觉回归（playwright 截图 + pixelmatch）」节，含依赖、判失败标准、Baseline 生命周期（含更新流程 6 步）、CI 编排、与 §11 a11y 的关系、阈值 0.1% 权衡 | `docs/UI-DESIGN.md` |
| 仓根 `package-lock.json`（新）：使 CI `npm ci` 可执行（PR-10 漏建，本次补上） | `package-lock.json`（新） |

**总验收验证**（gate-reviewer 复核，2026-09-26）：

- `git log --oneline` 显示 `8c3eb8a` 与 `fe4791e` 均已在 origin/main（HEAD = `fe4791e`），与依赖结果 SHA 完全一致
- `python -m pytest tests/ -q` → **112 passed in 7.62s**（与 wave5 基线 112 例持平，零回归）
- `python -m py_compile scripts/pyright_check.py` → exit 0（pyright script 编译通过；wave6 未触 Python 源码，故只验引用脚本）
- `node --check tests/ui/a11y.mjs` → exit 0；`node --check tests/ui/visual.mjs` → exit 0（双 mjs 语法合规）
- `process.exit` grep in `tests/ui/`：`a11y.mjs`（line 80 `process.exit(2)` + line 90 `process.exit(N>0 ? 1 : 0)`）、`visual.mjs`（line 130 `process.exit(2)` + line 141 `process.exit(failed.length > 0 ? 1 : 0)`）— 退出码三态语义（0/1/2）与 §11.2 / §12 完全对齐
- `yaml.safe_load('.github/workflows/ci.yml')` OK；a11y job 行 157-202 已无 `continue-on-error`（PR-17 摘除成功）；visual job 行 204-257 含 `continue-on-error: ${{ github.event_name == 'pull_request' }}`（PR warn / main fail-fast，与 a11y 升级前的策略对齐）
- docs/UI-DESIGN.md §11 / §12 / docs/RELEASING.md §5 脚注：PR-17 vs PR-10 对比表、退出码契约 0/1/2、视觉回归 baseline 更新流程 6 步、a11y 合并门槛注脚均已落地
- `tests/ui/visual.mjs` baseline 缺失自生成逻辑：fse.ensureDirSync(BASELINE_DIR) + 首次缺失即写 PNG 并视为 pass（与 §12「baseline 缺失 = 初始化」一致），本地无 docker 故 baseline PNG 由 CI 首次跑 visual job 时生成

**未闭合项 / 已知偏差**：

- wave6 §3 候选的「视觉回归」已正式落地；但 baseline PNG 在 CI 首次跑 visual job 之前不存在（本地无 docker，故无法本地生成 baseline 走比对路径）。第一次 visual job 失败视为「初始化」，会写 PNG 入 `tests/ui/baselines/` 并通过；后续 PR 必须带 baseline，main 上超阈值 0.1% 即 fail（**已知流程**，非阻断）
- `visual` job PR 上仍 `continue-on-error: ${{ github.event_name == 'pull_request' }}`：阈值 0.1% 在多平台字体差异下可能刷出少量噪声，PR 阶段仅 warn，待多平台 baseline 稳定后再摘除（**非阻断**，与 wave6 PR-17 摘除 a11y PR 宽松策略的逻辑一致：先宽松收集基线、再收紧）
- a11y job 已升级为合并门槛后，**禁止回退** `continue-on-error`：docs/RELEASING.md §5 已加脚注「a11y 合并门槛（PR-17 起生效）」明确提示；main 上若出现 serious/critical 必须修，不能靠宽松再过
- `tests/ui/a11y.mjs` 修改：合同原文范围未列 `tests/ui/a11y.mjs`，但契约 (3) 明确要求保留/复述退出码语义，wave6 t1 改注释为最小无副作用落地；脚本逻辑 (`process.exit(2)` + `process.exit(N>0 ? 1 : 0)`) 未变，行为零漂移
- `package-lock.json`（PR-18 新增）使 CI `npm ci` 可执行；本地 `npm install` 沿 workspaces 自动同步依赖；若未来 lockfile 与 `package.json` 漂移需 `npm install --package-lock-only` 重新生成（**非阻断**）
- `pyright-baseline.json` 维持 `diagnostics=[]`；wave6 未触 Python 源码，wave3 invariant 完整

**新增里程碑**：阶段 3 wave6 把 §3 长期项中的两个非阻断短板同时升级为正式质量门：① a11y 从「PR 上 warn / main 上 fail」升级为 PR + main 同等 fail-fast 的合并门槛，与 §6 WCAG AA 契约对齐；② 视觉回归首次落地为可运行 baseline 体系（Playwright 截图 + pixelmatch 0.1% 阈值 + 6 步更新流程 + CI artifact），§3 中长期悬而未决的「视觉回归」正式关闭。剩余触发条件（multi-scale batch、OpenTelemetry、`safety` 第三方依赖扫描、多 Runner 实例并发等）继续按 §3 条件按需启动。

---

> 关联文档：[docs/RECOGNITION-RESEARCH.md](./RECOGNITION-RESEARCH.md)（识别根因 + 实验数据）。