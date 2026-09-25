from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .adb import AdbClient
from .matcher import ImageMatcher
from .plans import PlanPayload, PlanSaveRequest, delete_plan, ensure_default_plan, list_plans, load_plan, save_plan
from .runner import TaskRunner
from .settings import BASE_DIR, SHOT_DIR, STOP_FILE, TEMPLATE_DIR, repair_template_names
from .tasks.chest import build_chest_config

app = FastAPI(title="咸鱼之王自动点击器")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

adb = AdbClient()
matcher = ImageMatcher(TEMPLATE_DIR)
runner = TaskRunner(adb, matcher)
repair_template_names()
ensure_default_plan()


class CropRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    device_id: str | None = None
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class StartRequest(PlanPayload):
    pass


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(request, "index.html", {"asset_version": _asset_version()})
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
    data = await adb.screenshot_png(device_id)
    runner.state.last_screenshot = data
    return {"url": "/api/screenshots/latest.png"}


@app.get("/api/screenshots/latest.png")
async def get_latest_screenshot() -> Response:
    data = runner.state.last_screenshot
    if not data:
        raise HTTPException(status_code=404, detail="暂未采集到截图")
    return Response(content=data, media_type="image/png")


@app.get("/api/screenshots/{name}")
async def get_screenshot(name: str) -> FileResponse:
    path = SHOT_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="截图不存在")
    return FileResponse(path, media_type="image/png")


@app.get("/api/templates")
async def list_templates() -> dict[str, object]:
    names = sorted(path.name for path in TEMPLATE_DIR.glob("*.png"))
    return {"templates": names}


@app.get("/api/templates/{name}")
async def get_template(name: str) -> FileResponse:
    path = TEMPLATE_DIR / name
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
    return {"plans": [plan.__dict__ for plan in list_plans()]}


@app.post("/api/plans")
async def api_save_plan(req: PlanSaveRequest) -> dict[str, object]:
    try:
        return save_plan(req).__dict__
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/plans/{filename}")
async def api_load_plan(filename: str) -> dict[str, object]:
    try:
        return load_plan(filename).__dict__
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/plans/{filename}")
async def api_delete_plan(filename: str) -> Response:
    try:
        delete_plan(filename)
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


def _safe_template_name(name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", Path(name).stem).strip("._")
    if not stem:
        raise HTTPException(status_code=400, detail="模板名称无效")
    return f"{stem}.png"


def _asset_version() -> str:
    """以 static 目录最新修改时间作为版本号，前端资源一改，缓存立刻失效。"""
    latest = 0.0
    for path in (BASE_DIR / "static").iterdir():
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return str(int(latest))

