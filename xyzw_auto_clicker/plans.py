from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .matcher import DEFAULT_ROI_BAND, DEFAULT_SCALE_MAX, DEFAULT_SCALE_MIN, DEFAULT_SCALE_STEP
from .settings import DATA_DIR, TRASH_DIR, fix_mojibake_name

PLAN_DIR = DATA_DIR / "plans"
PLAN_DIR.mkdir(parents=True, exist_ok=True)


class PlanPayload(BaseModel):
    device_id: str | None = None
    first_template_names: list[str] | None = None
    template_names: list[str] = Field(min_length=1)
    click_count: int = Field(gt=0, le=10000)
    interval_seconds: float = Field(default=0.8, ge=0.1, le=30)
    jitter_seconds: float = Field(default=0.2, ge=0, le=10)
    post_click_wait_seconds: float = Field(default=4.8, ge=0, le=20)
    repeat_tap_count: int = Field(default=2, ge=1, le=5)
    repeat_tap_gap_seconds: float = Field(default=0.12, ge=0.03, le=2)
    threshold: float = Field(default=0.86, ge=0.1, le=1)
    max_misses: int = Field(default=8, ge=1, le=100)
    # 多尺度识别参数（旧方案文件没有这些键，会用默认值补齐）
    scale_min: float = Field(default=DEFAULT_SCALE_MIN, ge=0.1, le=5)
    scale_max: float = Field(default=DEFAULT_SCALE_MAX, ge=0.1, le=5)
    scale_step: float = Field(default=DEFAULT_SCALE_STEP, ge=0.005, le=0.5)
    roi_band: tuple[float, float] | None = Field(default=DEFAULT_ROI_BAND)
    freeze_guard: bool = True
    freeze_threshold: float = Field(default=3.0, ge=0, le=64)
    freeze_max_waits: int = Field(default=4, ge=1, le=20)


class PlanSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    payload: PlanPayload
    default: bool = False


@dataclass(frozen=True)
class SavedPlan:
    name: str
    filename: str
    payload: dict[str, object]
    default: bool = False


def safe_plan_filename(name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", Path(name).stem).strip("._")
    if not stem:
        raise ValueError("方案名称无效")
    return f"{stem}.json"


def repair_plan_names() -> list[tuple[str, str]]:
    repaired: list[tuple[str, str]] = []
    for path in PLAN_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        original_name = str(data.get("name") or path.stem)
        fixed_name = fix_mojibake_name(original_name)
        fixed_filename = safe_plan_filename(fixed_name)
        changed = fixed_name != original_name or fixed_filename != path.name
        if not changed:
            continue
        data["name"] = fixed_name
        target = PLAN_DIR / fixed_filename
        _write_json(target, data)
        if target != path and path.exists():
            _move_to_trash(path)
        repaired.append((original_name, fixed_name))
    return repaired


def list_plans() -> list[SavedPlan]:
    repair_plan_names()
    plans: list[SavedPlan] = []
    for path in sorted(PLAN_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plans.append(
                SavedPlan(
                    name=str(data.get("name") or path.stem),
                    filename=path.name,
                    payload=dict(data.get("payload") or {}),
                    default=bool(data.get("default", False)),
                )
            )
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return sorted(plans, key=lambda item: (not item.default, item.name))


def save_plan(req: PlanSaveRequest) -> SavedPlan:
    filename = safe_plan_filename(req.name)
    path = PLAN_DIR / filename
    if req.default:
        _clear_default_flags(filename)
    data = {"name": req.name, "payload": req.payload.model_dump(), "default": req.default}
    _write_json(path, data)
    return SavedPlan(name=req.name, filename=filename, payload=data["payload"], default=req.default)


def ensure_default_plan() -> SavedPlan | None:
    existing = list_plans()
    if any(plan.default for plan in existing):
        return next(plan for plan in existing if plan.default)
    first_template = "video-first-open10.png"
    repeat_template = "video-repeat10.png"
    if not (DATA_DIR / "templates" / first_template).exists() or not (DATA_DIR / "templates" / repeat_template).exists():
        return None
    return save_plan(
        PlanSaveRequest(
            name="推荐宝箱方案",
            default=True,
            payload=PlanPayload(
                first_template_names=[first_template],
                template_names=[repeat_template],
                click_count=10,
                post_click_wait_seconds=4.8,
                repeat_tap_count=2,
            ),
        )
    )


def load_plan(filename: str) -> SavedPlan:
    path = PLAN_DIR / Path(filename).name
    if not path.exists():
        raise FileNotFoundError("方案不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    return SavedPlan(
        name=str(data.get("name") or path.stem),
        filename=path.name,
        payload=dict(data.get("payload") or {}),
        default=bool(data.get("default", False)),
    )


def delete_plan(filename: str) -> None:
    """把方案移入回收目录（软删除），不真正 unlink。

    两个原因：
    1. 文件删除在不少运行环境里会被守护策略拦下（终端/沙箱对批量删除设了守卫，
       直接 unlink 会抛 SystemExit，接口就变成 500，用户看到的是"删不掉"）；
    2. 方案是用户一点点调出来的参数，误删一次代价很大，移走还能捞回来。
    回收目录里同名文件会被覆盖，所以不会无限堆积。
    """
    path = PLAN_DIR / Path(filename).name
    if not path.exists():
        return
    _move_to_trash(path)


def _move_to_trash(path: Path) -> None:
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    os.replace(path, TRASH_DIR / path.name)


def _clear_default_flags(skip_filename: str) -> None:
    for path in PLAN_DIR.glob("*.json"):
        if path.name == skip_filename:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("default"):
            data["default"] = False
            _write_json(path, data)


def _write_json(path: Path, data: dict[str, object]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=PLAN_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
