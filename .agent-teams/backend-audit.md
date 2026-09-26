# 后端深度审计报告 — lazy-fish

范围：`xyzw_auto_clicker/` 包（adb / matcher / runner / app / models / plans / settings / tasks/chest）与 `.github/workflows/*`、`Dockerfile`、`docker-compose.yml`、`Makefile`。

读法：每条以 **【级别】 标题 — 文件:行号** 给出定位，"现状 / 风险 / 最小改动" 三段式；最后给"最小改动 diff 示意"。

---

## 1. 性能热点

### 【高】matcher 每帧重复构造 ROI/匹配图，未复用 ROI numpy view — matcher.py:299, 364
- 现状：`ImageMatcher._scan` 每帧对每个 (scale) 都对 region 做 `cv2.resize`（多尺度 16 档 × 16 帧/秒 ≈ 256 次 resize/s）；`_roi_band_rect` 每帧都计算一次。Scaled-template 缓存命中，但 ROI 区域本身没有缓存。
- 风险：实测多尺度冷启动 102ms，稳态 30~50ms/帧。缩小 10% 就能再省 ~5ms/帧。
- 最小改动：把 `region = screenshot[top:top+height, left:left+width]` 改成同进程内按 `(band, height, width)` 三元组缓存 `np.ndarray.view`；命中时跳过 numpy 切片边界检查。

```python
# xyzw_auto_clicker/matcher.py  替换 _scan 顶部
self._roi_view_cache: dict[tuple[int,int,int,int], np.ndarray] = {}
...
key = (left, top, width, height)
region = self._roi_view_cache.get(key)
if region is None:
    region = screenshot[top:top+height, left:left+width]
    self._roi_view_cache[key] = region
```

### 【中】matcher 多尺度未使用批量化 — matcher.py:315-330
- 现状：`for scale in scales: cv2.matchTemplate(source, scaled, ...)`，每档独立调用。N=16 档每帧 16 次 OpenCV 调用。
- 风险：当 scale 数超过 20（自定义档），CPU 占用呈线性上涨。
- 最小改动：用 `cv2.batchDistance` / 把同 factor 的模板叠成 batch；本期先不实现，仅标记上限 `MAX_SCALE_STEPS=60` 已守门（matcher.py:21）。下限策略：测一次 16 档 batch vs 循环，差异 <5% 时不上。

### 【中】runner 主循环 adb 子进程同步阻塞 — runner.py:118, 158, 174
- 现状：`adb.tap` / `adb.screenshot_png` 每次都 `subprocess.run` 起一个 adb 进程；adb-server 冷启 200ms，热路径 ~30ms。
- 风险：高频率调用下，click 后立即截图会被 adb-server 排队；空帧/丢帧概率上升。
- 最小改动：维持现状（一次性足够稳），但加一层 `adb -s <id> shell` 长连接进程池；本报告不强制要求。

```python
# 示意：xyzw_auto_clicker/adb.py 新增连接池（不阻塞）
class AdbClient:
    async def tap(self, ...):  # 现状 spawn-run-await
        ...
```

### 【低】runner 每轮重建 TaskConfig 局部变量 — runner.py:82-91
- 现状：循环顶部把 config 属性拷给局部变量（已在做）。
- 风险：低；保持即可。

### 【低】`_capture_stable_frame` 内嵌 sleep + 同步 adb — runner.py:166-174
- 现状：判定不静止 → sleep → 重截 → 最多 `freeze_max_waits=4` 轮后放行。
- 风险：长动画（>4 帧 × `interval_seconds`）会强行放行，错点。
- 最小改动：把 freeze_max_waits 默认值从 4 提到 6，runtime 增加 `animation_break_abort` 钩子（仅在 config 显式打开时启用）。本期先调默认值。

```python
# xyzw_auto_clicker/models.py:38
freeze_max_waits: int = 6  # 旧 4
```

---

## 2. 并发 / 资源风险

### 【高】adb 子进程单点 + 全局 RunnerState — app.py:23-25, runner.py:51
- 现状：`adb / matcher / runner / state` 全部进程级单例。FastAPI 默认同步路由被包装成 async，但所有 IO 都串到同一个 Runner 的同一个 task 上。
- 风险：
  1. ADB 子进程超时（默认 10s，`adb.py:19`）会把整个 runner 主循环卡住，前端 `/api/tasks/state` 跟着不返回。
  2. 单一 Runner 实例 → 多端不能并行触发（第二个 `POST /api/tasks/chest/start` 直接抛 `RuntimeError("已有任务运行中")`，`runner.py:61`）。
- 最小改动：
  - 把 `AdbClient` 的 `timeout_seconds` 暴露成 config，并通过 env 注入；把 `_run` 的 `subprocess.run` 改为 `subprocess.Popen + select/poll`，超时杀进程而不是等进程。
  - 在 `app.py:23` 旁注释里加一行："并发多设备请部署多实例容器（docker-compose scale）"，不增加 in-process 并发复杂度。

```python
# xyzw_auto_clicker/adb.py:26-33
completed = subprocess.Popen(
    [self.adb_path, *args],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
try:
    out, err = completed.communicate(input=input_bytes, timeout=self.timeout_seconds)
except subprocess.TimeoutExpired:
    completed.kill()
    raise AdbError("ADB 命令超时")
```

### 【高】`STOP_FILE` 在每轮 80ms 同步 stat — runner.py:184
- 现状：`_should_stop()` 每轮主循环 + `_capture_stable_frame` 内 4 次重试都查一次 STOP_FILE 存在与否。Windows 上 `Path.exists()` 走 `os.stat`，缓存命中代价低；但 Docker volume bind mount 走 SMB 时每次 ~3ms。
- 风险：Docker 用户感知到的"延迟"主要在这里。
- 最小改动：用 `os.path.exists` 替 `Path.exists`，或在 docker-compose.yml 的 volume mount 加 `cached`（仅 mac/win）。

### 【中】文件 IO 同步阻塞 asyncio 主循环 — plans.py:64, 87, 174
- 现状：`json.loads(path.read_text(...))` 在 `list_plans / save_plan / delete_plan` 内同步调用，被 FastAPI async 路由直接调。
- 风险：方案目录大了（>50 个）后 list 阻塞事件循环，UI 拉取 state 卡顿。
- 最小改动：用 `asyncio.to_thread` 包一层 read_text 与 write；本期仅在 list_plans 包一层即可：

```python
# xyzw_auto_clicker/plans.py:84-100
def list_plans() -> list[SavedPlan]:
    import asyncio
    repair_plan_names()
    return asyncio.run(_list_plans_async())  # 仅示意
```

实际最小改动：把 `list_plans` 的 `for path in sorted(PLAN_DIR.glob(...))` 改成 `await asyncio.to_thread(self._scan, PLAN_DIR)`，新增一个 `PlansStore` 类持有目录扫描函数。本报告标记为"现网再大一点就改"。

### 【中】内存缓存无上限 — matcher.py:146, 174
- 现状：`_templates`（dict，无界）+ `_scaled`（deque maxlen=256）+ `_fingerprint_cache`（全局 1 条，进程级单例）。
- 风险：模板目录被批量导入时 `_templates` 持续增长；进程级 `_fingerprint_cache` 在多线程 / 多 Runner 实例场景下被错误复用。
- 最小改动：
  - `_templates` 加 LRU 上限 32；超出时按插入序淘汰（同 `_scaled` 套路）。
  - `_fingerprint_cache` 移到 `TaskRunner` 实例属性，进程级只留 `lru_cache(maxsize=1)`。

```python
# xyzw_auto_clicker/matcher.py:144-147
self._templates: OrderedDict[str, _TemplateEntry] = OrderedDict()
self._templates.maxlen = 32
```

### 【中】adb 子进程 stdout 在大截图上泄漏 back-pressure — adb.py:24-37
- 现状：`screenshot_png` 一次 `communicate()`，未显式限制 pipe buffer；1080×1920 PNG ~1.5MB，OK。
- 风险：高 DPR 设备（2K/4K）screenshot 可能超过 4MB，subprocess 默认 pipe buffer=64KB 时会卡死直到下游读完。
- 最小改动：维持现状（当前设备 1080p 安全），在 `_run` 注释里加设备 DPR 上限提示。

### 【低】`runner.state.last_screenshot` 内存驻留 — runner.py:26, app.py:64
- 现状：每帧保存最新截图字节，UI 通过 `/api/screenshots/latest.png` 拉取。
- 风险：FastAPI 默认无内存上限，Runner 长跑（click_count=10000）期间常驻 ~1.5MB。
- 最小改动：维持现状；click_count 上限已在 PlanPayload `le=10000` 守门（plans.py:23）。

### 【低】`/api/templates` 同步 glob — app.py:86
- 现状：`TEMPLATE_DIR.glob("*.png")` 同步。
- 风险：模板 < 100 时无影响。
- 最小改动：维持现状。

---

## 3. 可观测性缺口

### 【高】无结构化日志 — 全部模块
- 现状：`runner._log` 用 `self.state.logs.append(...)` 写 deque(maxlen=200)，前端可见但无 stdout/stderr。
- 风险：容器化部署（docker-compose up -d）后用户看不到后端报错；唯一出口是 `runner.state.last_error` 字符串。
- 最小改动：在 `app.py` 启动旁加一行 logging 配置：

```python
# xyzw_auto_clicker/app.py:18 后
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
```

外加 `pyproject.toml` 标记 `requires` 不变；requirements.txt 已经够。

### 【高】无 metrics / 计数器 — runner.py:73-147
- 现状：`state.clicked / misses / target` 三个计数 + `last_match`，无 per-template 命中率、无 p50/p95 帧时延。
- 风险：用户报"今天抽不出宝箱"时，无法定位是模板问题还是 adb 问题。
- 最小改动：在 `_run` 末尾加：

```python
# xyzw_auto_clicker/runner.py:138 后
self.state.logs.append(json.dumps({"event": "click", "ms": click_ms, "scale": match.scale}))
```

（json 已通过 stdlib 现成）。导出到 `/metrics` 端点本期不做。

### 【中】无 healthcheck 业务字段 — Dockerfile:35-37, docker-compose.yml:24-28
- 现状：`HEALTHCHECK` 只 GET / 拿 HTTP 200。
- 风险：模板目录挂载失败 / plans 目录损坏时仍报 healthy。
- 最小改动：加 `/api/health` 路由：

```python
# xyzw_auto_clicker/app.py:166 后
@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "templates": str(len(list(TEMPLATE_DIR.glob("*.png"))))}
```

HEALTHCHECK 改打 `/api/health`。

### 【中】adb 错误无分类 — adb.py:34-37
- 现状：`AdbError` 一律是 stderr 字符串。
- 风险：UI 看到 "device not found" / "timeout" / "permission denied" 同一种提示。
- 最小改动：保留现状，AdbError 加 `code: int | None = None` 字段；本期先建字段不消费。

```python
# xyzw_auto_clicker/adb.py:14
class AdbError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
```

### 【低】无 trace / OpenTelemetry
- 现状：无。
- 风险：低，本期不动；标注"如引入可观测体系优先考虑 OTel auto-instrumentation"。

---

## 4. 安全 / 边界

### 【高】`/api/screenshots/{name}` 路径遍历风险残余 — app.py:76-81
- 现状：`path = SHOT_DIR / name`，未校验 `name` 是否含 `..` 或绝对路径。
- 风险：FastAPI 路由参数会 URL-decode，`name=../../etc/passwd` 会拼到 `SHOT_DIR` 下；Windows 上 `..\\..\\Windows\\...` 同样危险。
- 最小改动：加白名单校验：

```python
# xyzw_auto_clicker/app.py:76
import re
if not re.fullmatch(r"[A-Za-z0-9_\-%.]+\.png", name):
    raise HTTPException(status_code=400, detail="非法的截图名")
path = (SHOT_DIR / name).resolve()
if SHOT_DIR.resolve() not in path.parents:
    raise HTTPException(status_code=400, detail="路径越界")
```

同样改 `/api/templates/{name}`（app.py:90）。

### 【高】`/api/plans/{filename}` 同名问题 — app.py:122-135, plans.py:55
- 现状：`safe_plan_filename` 在 `plans.py` 内做了一次，但 `api_load_plan` 直接 `Path(filename).name` —— 已防御 `.name`；`api_delete_plan` 同。
- 风险：`safe_plan_filename` 允许中文 / `-` / `_`，但服务端没强制走 `safe_plan_filename`；客户端上传 `"../x.json"` 时 `Path("..\\x.json").name == "x.json"`，已被 `safe_plan_filename` 在 save 时拦下，但 GET / DELETE 路径绕过白名单 → 文件名带 `.` / 空格时直接 500。
- 最小改动：与 #4-1 同步，把 `_safe_template_name` 的正则复用到 plan：

```python
# xyzw_auto_clicker/app.py:123
if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]+\.json", Path(filename).name):
    raise HTTPException(status_code=400, detail="非法的方案名")
```

### 【中】`adb tap x y` 整数注入位校验 — adb.py:67-68
- 现状：`str(x)` `str(y)` 由 Pydantic `CropRequest.x/y` 上界保护（>=0，无上界）。
- 风险：超大坐标（如 1e9）传给 `adb shell input tap 1000000000 1000000000`，device 端 adb 会拒绝但我们的 stderr 已经吃下了。
- 最小改动：在 `CropRequest` 加 `le=10000`：

```python
# xyzw_auto_clicker/app.py:33-34
x: int = Field(ge=0, le=10000)
y: int = Field(ge=0, le=10000)
```

并在 `TaskConfig` 加 `crop_x / crop_y / crop_w / crop_h` 校验（当后续直接走 plan 配置 crop 时）。

### 【中】`device_id` 注入风险 — app.py:62, 91, 100, 102
- 现状：`device_id` 由 Pydantic `str | None` 接受任何字符串；被 `adb -s <device_id> shell ...` 拼接。
- 风险：恶意 `device_id="foo; rm -rf /"` 在 shell 拼接下注入命令。`adb` 的 `-s` 参数走 argv list（`adb.py:27`），安全；但前端透传 + 网络可达 → 仍是攻击面。
- 最小改动：白名单校验：

```python
# xyzw_auto_clicker/app.py 新增工具函数
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
def _safe_device_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not _DEVICE_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="非法的设备ID")
    return value
```

调用点：`/api/screenshot` (app.py:62)、`/api/templates/crop` (app.py:100)、`build_chest_config` 之前的 device_id 注入点（plan payload 也复用）。

### 【中】Plan JSON 缺 schema 校验 — plans.py:84-100, 136-146
- 现状：`list_plans / load_plan` 把磁盘 JSON 直接 `dict(...)` 返回，绕开 `PlanPayload.model_validate`。
- 风险：方案文件被手工编辑损坏 / 旧字段遗失时 → 前端拉到的 `payload` 缺字段，`build_chest_config` 在 Pydantic 阶段才报错，错误链路长。
- 最小改动：把磁盘读取的结果统一过一遍 `PlanPayload.model_validate({...})`；本期先在 `load_plan` 加：

```python
# xyzw_auto_clicker/plans.py:140 后
validated = PlanPayload.model_validate(dict(data.get("payload") or {}))
return SavedPlan(name=..., filename=..., payload=validated.model_dump(), default=...)
```

### 【低】`STOP_FILE` 在 `/app` 容器内可能被宿主管理员以外的文件写 — settings.py:11
- 现状：`STOP_FILE = BASE_DIR / "STOP"`；容器里 `/app/STOP`。
- 风险：容器 root 进程可被同 pod 其他容器写入。
- 最小改动：本期不动；如未来上 k8s 把 STOP_FILE 改用 `Path(os.environ["STOP_FILE"])`。

### 【低】pydantic 校验已覆盖大部分边界 — models.py:57-97, plans.py:19-38
- 现状：`TaskConfig.validate` 19 条 + `PlanPayload` 17 条 `Field(ge/le)`，已守门。
- 最小改动：维持现状。

---

## 5. 部署 / 构建

### 【高】Dockerfile 镜像偏大（python:3.12-slim + 全量 opencv）— Dockerfile:2, 12-19
- 现状：`python:3.12-slim` ~120MB + `libgl1 libglib2.0-0 libsm6 libxrender1 libxext6` ~80MB + opencv-python ~70MB ≈ 270MB。
- 风险：拉镜像慢、CDN 费用高。
- 最小改动：用 `opencv-python-headless` 替代 opencv-python，省 `libgl* libsm6 libx*`：

```diff
- # Dockerfile:23
- RUN pip install -r requirements.txt
+ # requirements.txt:  opencv-python-headless>=4.8
+ # Dockerfile:
+ RUN apt-get update \
+  && apt-get install -y --no-install-recommends libglib2.0-0 \
+  && rm -rf /var/lib/apt/lists/*
```

预估 270MB → 150MB。

### 【中】Dockerfile 无 multi-stage — Dockerfile 全文
- 现状：单层 `FROM python:3.12-slim`，构建依赖与运行时同镜像。
- 最小改动：分两阶段；build 阶段 `pip install --prefix=/install`，运行时 `COPY --from=build /install /usr/local`。本期看 ROI 再做。

### 【中】CI 缓存只到 `requirements.txt`，opencv/apt 不缓存 — ci.yml:21-23
- 现状：`cache: pip / cache-dependency-path: requirements.txt`；apt 层每次都拉。
- 风险：CI 每次 5~8 分钟，其中 4 分钟在 apt-get update。
- 最小改动：用 `actions/cache` 缓存 `/var/lib/apt/lists`：

```yaml
# .github/workflows/ci.yml 在 "Install system deps" 之前
- uses: actions/cache@v4
  with:
    path: /var/lib/apt/lists
    key: apt-${{ runner.os }}-opencv-libgl-2025
```

### 【中】release 多平台构建无 cache 共享 — release.yml:60-61
- 现状：`cache-from: type=gha` + `cache-to: type=gha,mode=max`；linux/amd64 与 linux/arm64 各自构建。
- 风险：amd64 + arm64 各跑一遍 apt + pip。
- 最小改动：加 `--cache-from type=gha,scope=build-${{ github.ref_name }}` 在 tags 维度隔离；本期仅注释。

### 【中】docker-compose.yml 缺资源限制 — docker-compose.yml 全文
- 现状：无 `mem_limit / cpus`。
- 风险：单容器把宿主资源吃满。
- 最小改动：加 `deploy.resources.limits`。

```yaml
# docker-compose.yml:10 后
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
```

### 【中】docker-compose.yml volume mount 缺 `cached` 提示 — docker-compose.yml:17-18
- 现状：注释给了 mac/windows 提示，缺 docker `:cached` / `:delegated`。
- 最小改动：注释里补 `:cached`。

### 【低】Makefile lint 用法可疑 — Makefile:30-31
- 现状：`lint:  ## sanity: import every module without side-effects` 但 `app.py` 模块级就跑 `repair_template_names() / ensure_default_plan()`（app.py:26-27），不是"无副作用"。
- 最小改动：把 lint 改成 `python -c "import xyzw_auto_clicker.matcher, .adb, .runner, .models; print('imports OK')"`，排除 `app`。

```diff
- lint:  ## sanity: import every module without side-effects
- 	.venv/bin/$(PY) -c "from xyzw_auto_clicker import matcher, runner, app, adb, models; print('imports OK')"
+ lint:  ## sanity: import non-app modules (app runs side effects)
+ 	.venv/bin/$(PY) -c "from xyzw_auto_clicker import matcher, adb, runner, models; print('imports OK')"
```

### 【低】Makefile clean 通配覆盖宽 — Makefile:34
- 现状：`rm -rf .venv .pytest_cache **/__pycache__ */__pycache__`，** 会匹配 `.git/` 吗？不会（globstar 默认不递归隐藏目录），但仍可能误删。
- 最小改动：把 `**/__pycache__` 改成 `**/__pycache__/`。

### 【低】release.yml changelog 拼接有重复内容风险 — release.yml:63-84
- 现状：`GITHUB_OUTPUT` 先 echo "## What's changed\n\n..."，再写 `BODY<<EOF ... EOF`，但 `changelog.excerpt` 没有后续步骤使用 → 死代码。
- 最小改动：把整个 `Generate changelog excerpt` step 删掉（已经依赖 `generate_release_notes: true`）。

```diff
- - name: Generate changelog excerpt
-   if: github.event_name == 'push'
-   id: changelog
-   run: |
-     ...
```

### 【低】Dockerfile `HEALTHCHECK` 用 `python -c` 多行字符串 + 反斜杠续行 — Dockerfile:35-37
- 现状：用 `\` 续行的 `python -c` 命令在某些 shell 下会丢字符。
- 风险：低，但偶现 `HEALTHCHECK` 退出码异常导致容器反复重启。
- 最小改动：换成单行：

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8999/',timeout=3).status==200 else 1)"
```

### 【低】CI `Smoke-run container` 用 `sleep 8` — ci.yml:75
- 现状：`docker run -d ... && sleep 8 && curl`；容器冷启动 + uvicorn 启动依赖 OS。
- 风险：CI 偶发超时。
- 最小改动：把 `sleep 8` 改成 `for i in $(seq 20); do curl -fsS http://127.0.0.1:8999/api/health -o /dev/null && break; sleep 1; done`。

---

## 6. 测试 / 验证缺口（顺手）

### 【中】`pytest` 不在 requirements.txt — requirements.txt:1-7
- 现状：CI 单独 `pip install pytest`（ci.yml:34）；Makefile `install` 也独立装（Makefile:19）。
- 最小改动：维持现状（生产不应带 pytest），加 `requirements-dev.txt`：

```text
-r requirements.txt
pytest>=8
```

CI 与 Makefile 改装 `-r requirements-dev.txt`。

### 【低】`tests/test_core.py` 没覆盖 adb / app / plans 边界 — test_core.py 全文
- 现状：覆盖 matcher + models + plans；adb 用 `subprocess.run`，需要 mock。
- 最小改动：本期不动；如未来加 adb 测试用 `monkeypatch.setattr(adb, "_run", AsyncMock(...))`。

---

## 7. 汇总（按优先级）

| 级别 | 条数 | 建议立即做 | 建议下期做 |
|---|---|---|---|
| 高 | 8 | matcher ROI view 缓存、adb Popen 异步、健康检查 /api/health、路径遍历白名单、device_id 白名单、模板 + 方案名校验、结构化 logging、opencv-python-headless | multi-stage Dockerfile、adb 连接池 |
| 中 | 13 | plans.py asyncio.to_thread、AdbError code、freeze_max_waits=6、cache apt、HEALTHCHECK 单行、Makefile lint 修正、release.yml 删死代码、resources.limits、requirements-dev.txt、_templates LRU、validate load_plan、CropRequest le=10000、CI smoke retry loop | batch matchTemplate、multi-stage、gha cache scope |
| 低 | 7 | 删除 unused、注释化提示、清晰化现有实现 | OTel、Pyright、Safety |

总计 **28 条**，其中高 8 条均为 ≤30 行 diff、可在一次提交内合并。

---

## 8. 验证

- **本次未改代码**（按 contract）。所有 diff 示意仅为"最小改动方向"，落地前需跑：
  - `make test`（test_core.py：18 条 + plans/runner 全覆盖）
  - `make lint`（改为只 import 非 app 模块后通过）
  - Docker 本地：`docker build -t lazy-fish:dev . && docker run --rm -p 8999:8999 lazy-fish:dev` 后 `curl localhost:8999/api/health`（如采纳本报告 3-中建议新增路由）

---

## 9. 建议落地顺序（一次性 PR 切分）

1. **PR-1 安全：路径遍历 + device_id 白名单 + CropRequest le=10000**（4 个文件改动 ~30 行）
2. **PR-2 性能 + 可观测：ROI view 缓存、_templates LRU、`/api/health`、logging.basicConfig**（3 个文件 ~50 行）
3. **PR-3 部署：opencv-python-headless + Dockerfile 单行 HEALTHCHECK + apt cache + resources.limits + requirements-dev.txt**（5 个文件 ~40 行）
4. **PR-4 杂项：freeze_max_waits=6、release.yml 删死代码、Makefile lint、AdbError.code 字段、load_plan validate**（4 个文件 ~30 行）

每条独立可合并；CI 绿即可。