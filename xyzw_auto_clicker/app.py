from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator, model_validator

from .adb import AdbClient
from .logging_setup import configure as _configure_logging
from .logging_setup import env_json_enabled
from .matcher import ImageMatcher
from .plans import (
    CROP_MAX_AREA,
    PlanPayload,
    PlanSaveRequest,
    delete_plan,
    ensure_default_plan,
    list_plans,
    load_plan,
    safe_field_name,
    save_plan,
)
from .runner import RunnerRegistry
from .runner import TaskRunner as TaskRunner  # noqa: F401  # 兼容旧测试/外部引用
from .settings import BASE_DIR, SHOT_DIR, STOP_FILE, TEMPLATE_DIR, repair_template_names
from .tasks.chest import build_chest_config
from .tracing_setup import load_tracer, shutdown_tracer

# PR-21 OTel：进程级 tracer 单例，OTEL_SDK_DISABLED=true 时为 NoOp。
_tracer = load_tracer("lazy-fish")

# 模块顶层 logger（PR-3 可观测性：所有跨模块日志统一从这里出）
logger = logging.getLogger(__name__)

# 进程启动时间，/api/health 用它算 uptime（容器重启后自动归零）
_APP_START_TIME = time.monotonic()

# JSON fmt 仅当 LOG_JSON=1 才开；不传 fmt= 时由环境变量决定
_configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))

adb = AdbClient()
matcher = ImageMatcher(TEMPLATE_DIR)
# PR-26：runner 升级为注册表（device_id → runner）。默认设备（None）的任务落在
# key="" 的 runner 上，旧接口/旧前端行为完全不变。
runner = RunnerRegistry(adb, matcher)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # PR-21 OTel：startup hook 触发 SDK 初始化（OTEL_SDK_DISABLED=true → NoOp）；
    # 放在 lifespan 而不是模块顶部，匹配「依赖 FastAPI app 上下文」的语义。
    load_tracer("lazy-fish")
    # 模板名修复与默认方案生成涉及磁盘 IO，放到线程里跑，避免拖慢事件循环首帧。
    await asyncio.to_thread(repair_template_names)
    await asyncio.to_thread(ensure_default_plan)
    try:
        yield
    finally:
        # shutdown 强制 flush，避免 uvicorn 优雅退出时丢掉缓冲里最后几秒的 span
        shutdown_tracer(timeout_millis=2000)


app = FastAPI(title="咸鱼之王自动点击器", lifespan=_lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def _ot_http_span_middleware(request: Request, call_next):
    """PR-21 OTel：为每个 HTTP 请求建一条 span。

    ponytail: 用 FastAPI 中间件而不是 FastAPIInstrumentor，
    省掉 contrib 包里 asgi/instrumentation 的额外 import 与 urllib/opencensus 子依赖；
    后续若需 traceparent header 透传与跨进程 trace_id，在此处加 propagator.inject / extract 即可。
    """
    with _tracer.start_as_current_span(
        f"http {request.method} {request.url.path}",
        attributes={
            "http.method": request.method,
            "http.route": request.url.path,
        },
    ) as span:
        response = await call_next(request)
        span.set_attribute("http.status_code", response.status_code)
        return response


class CropRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    device_id: str | None = None
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @field_validator("device_id")
    @classmethod
    def _validate_device_id(cls, value: str | None) -> str | None:
        # 非空时必须是 ADB serial 形态：字母数字 + . _ : -
        # 防止路径分隔符或 shell 元字符借 device_id 注入（截图接口、调试日志、文件名拼接都用得到它）
        if value is None or value == "":
            return None
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
            raise ValueError("device_id 仅允许字母数字与 . _ : -")
        return value

    @field_validator("x", "y", "width", "height")
    @classmethod
    def _validate_geometry(cls, value: int) -> int:
        # 复用 plans.safe_field_name：与 PlanPayload.crop 走同一道闸，
        # 上限 8192 防超大值绕过校验；负数由 Field(ge=0) 拒收。
        return safe_field_name(value)

    @model_validator(mode="after")
    def _validate_area(self) -> CropRequest:
        # 防「整张图覆盖」：裁剪面积超过 1080p 全屏时拒绝，怀疑值被前端 bug 写成全屏。
        # 注意这里不校 x+w / y+h，因为服务端要拿到截图后再判断；这里只挡面积超界。
        if self.width * self.height > CROP_MAX_AREA:
            raise ValueError(f"裁剪面积超过 {CROP_MAX_AREA} 像素，疑似全图覆盖")
        return self


class StartRequest(PlanPayload):
    pass


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request, "index.html", {"asset_version": _asset_version()}
    )
    # 前端资源改动后必须立刻生效，禁止浏览器缓存页面外壳
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/api/devices")
async def list_devices() -> dict[str, object]:
    try:
        devices = await adb.devices()
        return {"devices": [device.__dict__ for device in devices]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/screenshot")
async def take_screenshot(payload: dict[str, str | None] | None = None) -> dict[str, str]:
    device_id = payload.get("device_id") if payload else None
    if (
        device_id is not None
        and device_id != ""
        and not re.fullmatch(r"[A-Za-z0-9._:-]+", device_id)
    ):
        raise HTTPException(status_code=400, detail="device_id 仅允许字母数字与 . _ : -")
    data = await adb.screenshot_png(device_id)
    # 与 runner 保持一致：截图落盘一份给 /api/screenshots/latest.png 用，不在状态里塞大字节数组
    target_runner = runner.get(device_id) if isinstance(runner, RunnerRegistry) else runner
    target_runner.state.last_screenshot = _write_latest_screenshot(data)
    return {"url": "/api/screenshots/latest.png"}


@app.get("/api/screenshots/latest.png")
async def get_latest_screenshot() -> FileResponse:
    path = (
        runner.get(None) if isinstance(runner, RunnerRegistry) else runner
    ).state.last_screenshot
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="暂未采集到截图")
    return FileResponse(path, media_type="image/png")


@app.get("/api/screenshots/{name}")
async def get_screenshot(name: str) -> FileResponse:
    safe_name = _safe_shot_name(name)
    path = SHOT_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="截图不存在")
    return FileResponse(path, media_type="image/png")


@app.get("/api/templates")
async def list_templates() -> dict[str, object]:
    names = sorted(path.name for path in TEMPLATE_DIR.glob("*.png"))
    return {"templates": names}


@app.get("/api/templates/{name}")
async def get_template(name: str) -> FileResponse:
    safe_name = _safe_template_name(name)
    path = TEMPLATE_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="模板不存在")
    return FileResponse(path, media_type="image/png")


@app.post("/api/templates/crop")
async def crop_template(req: CropRequest) -> dict[str, str]:
    safe_name = _safe_template_name(req.name)
    screenshot = await adb.screenshot_png(req.device_id)
    output_path = TEMPLATE_DIR / safe_name
    matcher.crop_template(screenshot, req.x, req.y, req.width, req.height, output_path)
    # 模板刚被覆盖，丢掉旧模板缓存与尺度记忆（否则还会按旧图/旧尺度去认）
    matcher.clear_cache()
    return {"name": safe_name}


@app.get("/api/plans")
async def api_list_plans() -> dict[str, object]:
    # 文件 IO（glob + read_text + json.loads）走线程：调用方是 async，不能再阻塞事件循环。
    plans = await asyncio.to_thread(list_plans)
    return {"plans": [plan.__dict__ for plan in plans]}


@app.post("/api/plans")
async def api_save_plan(req: PlanSaveRequest) -> dict[str, object]:
    try:
        saved = await asyncio.to_thread(save_plan, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved.__dict__


@app.get("/api/plans/{filename}")
async def api_load_plan(filename: str) -> dict[str, object]:
    safe_name = _safe_plan_filename(filename)
    try:
        plan = await asyncio.to_thread(load_plan, safe_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return plan.__dict__


@app.delete("/api/plans/{filename}")
async def api_delete_plan(filename: str) -> Response:
    safe_name = _safe_plan_filename(filename)
    try:
        await asyncio.to_thread(delete_plan, safe_name)
    except OSError as exc:
        raise HTTPException(status_code=409, detail=f"方案移出失败：{exc}") from exc
    return Response(status_code=204)


@app.post("/api/tasks/chest/start")
async def start_chest(req: StartRequest) -> dict[str, str]:
    config = build_chest_config(req)
    task_runner = runner.get_or_create(req.device_id)
    try:
        await task_runner.start(config)
        return {"status": "starting", "device_id": req.device_id or ""}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tasks/stop")
async def stop_task(req: dict[str, str | None] | None = None) -> dict[str, str]:
    device_id = req.get("device_id") if req else None
    if (
        device_id is not None
        and device_id != ""
        and not re.fullmatch(r"[A-Za-z0-9._:-]+", device_id)
    ):
        raise HTTPException(status_code=400, detail="device_id 仅允许字母数字与 . _ : -")
    task_runner = runner.get(device_id)
    if task_runner is None:
        return {"status": "stopped", "device_id": device_id or ""}
    await task_runner.stop()
    return {"status": "stopped", "device_id": device_id or ""}


@app.get("/api/tasks/state")
async def task_state(device_id: str | None = None) -> dict[str, object]:
    # 注册表模式：传入 device_id → 该设备自己的状态；不传 → 默认设备快照
    # （保持旧接口平铺契约），并附带按 device_id 分组的 devices 字段。
    if device_id is not None:
        task_runner = runner.get(device_id) if isinstance(runner, RunnerRegistry) else runner
        return task_runner.state.snapshot() if task_runner else {}
    default_runner = runner.get(None) if isinstance(runner, RunnerRegistry) else runner
    if default_runner is None:
        return {"devices": runner.snapshots()}
    snapshot = default_runner.state.snapshot()
    if isinstance(runner, RunnerRegistry):
        snapshot["devices"] = runner.snapshots()
    return snapshot


@app.delete("/api/stop-file")
async def clear_stop_file() -> Response:
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    return Response(status_code=204)


@app.get("/api/health")
async def health() -> dict[str, object]:
    """供 Dockerfile HEALTHCHECK 与 docker-compose healthcheck 复用。

    关键信号：
    - last_screenshot_mtime: 最近一次采集到截图的时间戳；None 表示尚未截图。
    - runner_status: idle/starting/running/paused/stopped/done/error。
    - uptime: 进程启动到现在的秒数（容器视角的"活了多久"）。
    """
    default_runner = runner.get(None) if isinstance(runner, RunnerRegistry) else runner
    path = default_runner.state.last_screenshot
    last_screenshot_mtime: float | None = None
    if path is not None:
        try:
            last_screenshot_mtime = path.stat().st_mtime
        except OSError:
            last_screenshot_mtime = None
    return {
        "status": "ok",
        "last_screenshot_mtime": last_screenshot_mtime,
        "runner_status": default_runner.state.status,
        "uptime": round(time.monotonic() - _APP_START_TIME, 3),
    }


@app.get("/api/metrics")
async def metrics(request: Request) -> Response:
    """运行时指标：仅 LOG_JSON=1 启用（容器化部署默认关闭，开发期手动开）。

    ponytail: 与 logging_setup.env_json_enabled 走同一套开关，确保「结构化日志 + 指标端点」
    同步启用——避免生产环境无意中暴露进程内存 / 任务计数等敏感指标。
    Accept 头协商：Accept 含 application/json 走 JSON；含 text/plain 或 */* 走
    Prometheus text/plain; version=0.0.4；默认 JSON。
    字段顺序稳定，便于 Prometheus / VictoriaMetrics 文本解析。
    """
    if not _metrics_enabled():
        return Response(status_code=404)
    snapshot = _collect_metrics_snapshot()
    if _wants_prometheus(request.headers.get("accept")):
        body = _render_prometheus(snapshot)
        return PlainTextResponse(
            content=body,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    return Response(
        content=json.dumps(snapshot, ensure_ascii=False, sort_keys=False),
        media_type="application/json",
    )


def _wants_prometheus(accept_header: str | None) -> bool:
    """Accept 头协商：application/json 优先；否则 text/plain 或 */* 走 Prometheus。

    ponytail: 兼容 cURL / wget 默认 Accept=*/*，但拒绝把 application/json 误判成文本——
    显式 application/json 走 JSON，含 text/plain 才走 Prometheus。
    """
    if not accept_header:
        return False
    types = [item.strip().split(";", 1)[0].lower() for item in accept_header.split(",")]
    has_json = any(t == "application/json" for t in types)
    has_text_plain = any(t == "text/plain" for t in types)
    has_wildcard = any(t == "*/*" for t in types)
    if has_json and not has_text_plain:
        return False
    return has_text_plain or has_wildcard


def _collect_metrics_snapshot() -> dict[str, object]:
    default_runner = runner.get(None) if isinstance(runner, RunnerRegistry) else runner
    path = default_runner.state.last_screenshot
    last_screenshot_mtime: float | None = None
    if path is not None:
        try:
            last_screenshot_mtime = path.stat().st_mtime
        except OSError:
            last_screenshot_mtime = None
    return {
        "process_resident_memory_bytes": _process_resident_memory_bytes(),
        "runner_status": default_runner.state.status,
        "last_screenshot_mtime": last_screenshot_mtime,
        "uptime_seconds": round(time.monotonic() - _APP_START_TIME, 3),
        "tasks_started_total": default_runner.tasks_started_total,
    }


def _render_prometheus(snapshot: dict[str, object]) -> str:
    """按 Prometheus text/plain; version=0.0.4 文本格式渲染指标。

    ponytail: 字符串里出现的 \\ 与 \\n 是 Prometheus 转义规则；这里只输出整数 / 浮点 /
    受控枚举，不带引号字符串，省去转义。
    """
    last_mtime = snapshot.get("last_screenshot_mtime")
    lines: list[str] = [
        "# HELP lazy_fish_uptime_seconds 进程启动到现在的秒数（容器视角存活时间）。",
        "# TYPE lazy_fish_uptime_seconds gauge",
        f"lazy_fish_uptime_seconds {snapshot['uptime_seconds']}",
        "# HELP lazy_fish_process_resident_memory_bytes "
        "进程 RSS 字节数；非 Linux 降级返回 0。",
        "# TYPE lazy_fish_process_resident_memory_bytes gauge",
        f"lazy_fish_process_resident_memory_bytes {snapshot['process_resident_memory_bytes']}",
        "# HELP lazy_fish_runner_status Runner 当前状态："
        "idle/starting/running/paused/stopped/done/error。",
        "# TYPE lazy_fish_runner_status gauge",
        f"lazy_fish_runner_status {snapshot['runner_status']}",
        "# HELP lazy_fish_last_screenshot_mtime_seconds "
        "最近一次截图 mtime；未截图时 0。",
        "# TYPE lazy_fish_last_screenshot_mtime_seconds gauge",
        "lazy_fish_last_screenshot_mtime_seconds "
        f"{last_mtime if last_mtime is not None else 0}",
        "# HELP lazy_fish_tasks_started_total 累计启动的任务次数。",
        "# TYPE lazy_fish_tasks_started_total counter",
        f"lazy_fish_tasks_started_total {snapshot['tasks_started_total']}",
        "",
    ]
    return "\n".join(lines)


def _metrics_enabled() -> bool:
    """LOG_JSON=1 → /api/metrics 同步开启；其他情况返回 404。

    与 logging_setup.env_json_enabled 复用同一套真值表，避免两边规则漂移。
    """
    return env_json_enabled()


def _process_resident_memory_bytes() -> int:
    """读 /proc/self/status 的 VmRSS；非 Linux / 文件不存在时降级返回 0。

    ponytail: 不引 psutil，只读一行；容器内 /proc 几乎一定有，沙箱/Windows 测试场景
    拿不到时返回 0 而不是抛异常，避免把指标端点变成 500。
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1]) * 1024
    except (OSError, ValueError):
        return 0
    return 0


def _safe_template_name(name: str) -> str:
    return _safe_name(name, allowed_extension=".png", display="模板")


def _safe_shot_name(name: str) -> str:
    return _safe_name(name, allowed_extension=".png", display="截图")


def _safe_plan_filename(name: str) -> str:
    return _safe_name(name, allowed_extension=".json", display="方案")


def _safe_name(name: str, allowed_extension: str, display: str) -> str:
    """统一白名单：拒空、拒 ..、拒绝对路径、拒目录分隔符、拒非指定扩展名。

    三个 GET 共用：任何「按文件名取文件」的接口都不能相信客户端发来的字符串，
    否则 /api/screenshots/../app.py 之类的请求会一路走出 SHOT_DIR。
    """
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail=f"{display}名称无效")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail=f"{display}名称无效")
    if name in {".", ".."} or name.startswith(".."):
        raise HTTPException(status_code=400, detail=f"{display}名称无效")
    if Path(name).is_absolute():
        raise HTTPException(status_code=400, detail=f"{display}名称无效")
    if Path(name).suffix.lower() != allowed_extension:
        raise HTTPException(status_code=400, detail=f"{display}名称无效")
    return name


def _write_latest_screenshot(data: bytes) -> Path | None:
    """与 runner._write_latest_screenshot 行为一致：手动截图也落到同一个 last-frame.png。

    runner 和手动截图共用一份固定文件名，/api/screenshots/latest.png 就能稳定给出最新一帧。
    """
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / "last-frame.png"
        path.write_bytes(data)
        return path
    except OSError:
        return None


def _asset_version() -> str:
    """以 static 目录最新修改时间作为版本号，前端资源一改，缓存立刻失效。"""
    latest = 0.0
    for path in (BASE_DIR / "static").iterdir():
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return str(int(latest))

