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
from fastapi.responses import FileResponse, HTMLResponse, Response
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
from .runner import TaskRunner
from .settings import BASE_DIR, SHOT_DIR, STOP_FILE, TEMPLATE_DIR, repair_template_names
from .tasks.chest import build_chest_config

# 模块顶层 logger（PR-3 可观测性：所有跨模块日志统一从这里出）
logger = logging.getLogger(__name__)

# 进程启动时间，/api/health 用它算 uptime（容器重启后自动归零）
_APP_START_TIME = time.monotonic()

# JSON fmt 仅当 LOG_JSON=1 才开；不传 fmt= 时由环境变量决定
_configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))

adb = AdbClient()
matcher = ImageMatcher(TEMPLATE_DIR)
runner = TaskRunner(adb, matcher)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 模板名修复与默认方案生成涉及磁盘 IO，放到线程里跑，避免拖慢事件循环首帧。
    await asyncio.to_thread(repair_template_names)
    await asyncio.to_thread(ensure_default_plan)
    yield


app = FastAPI(title="咸鱼之王自动点击器", lifespan=_lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
    runner.state.last_screenshot = _write_latest_screenshot(data)
    return {"url": "/api/screenshots/latest.png"}


@app.get("/api/screenshots/latest.png")
async def get_latest_screenshot() -> FileResponse:
    path = runner.state.last_screenshot
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
    try:
        await runner.start(config)
        return {"status": "starting"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tasks/stop")
async def stop_task() -> dict[str, str]:
    await runner.stop()
    return {"status": "stopped"}


@app.get("/api/tasks/state")
async def task_state() -> dict[str, object]:
    return runner.state.snapshot()


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
    path = runner.state.last_screenshot
    last_screenshot_mtime: float | None = None
    if path is not None:
        try:
            last_screenshot_mtime = path.stat().st_mtime
        except OSError:
            last_screenshot_mtime = None
    return {
        "status": "ok",
        "last_screenshot_mtime": last_screenshot_mtime,
        "runner_status": runner.state.status,
        "uptime": round(time.monotonic() - _APP_START_TIME, 3),
    }


@app.get("/api/metrics")
async def metrics() -> Response:
    """运行时指标：仅 LOG_JSON=1 启用（容器化部署默认关闭，开发期手动开）。

    ponytail: 与 logging_setup.env_json_enabled 走同一套开关，确保「结构化日志 + 指标端点」
    同步启用——避免生产环境无意中暴露进程内存 / 任务计数等敏感指标。
    字段顺序稳定，便于 Prometheus / VictoriaMetrics 文本解析。
    """
    if not _metrics_enabled():
        return Response(status_code=404)
    path = runner.state.last_screenshot
    last_screenshot_mtime: float | None = None
    if path is not None:
        try:
            last_screenshot_mtime = path.stat().st_mtime
        except OSError:
            last_screenshot_mtime = None
    body = {
        "process_resident_memory_bytes": _process_resident_memory_bytes(),
        "runner_status": runner.state.status,
        "last_screenshot_mtime": last_screenshot_mtime,
        "uptime_seconds": round(time.monotonic() - _APP_START_TIME, 3),
        "tasks_started_total": runner.tasks_started_total,
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False, sort_keys=False),
        media_type="application/json",
    )


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

