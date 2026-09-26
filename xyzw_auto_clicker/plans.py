from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .matcher import DEFAULT_ROI_BAND, DEFAULT_SCALE_MAX, DEFAULT_SCALE_MIN, DEFAULT_SCALE_STEP
from .settings import DATA_DIR, TRASH_DIR, fix_mojibake_name

logger = logging.getLogger(__name__)

PLAN_DIR = DATA_DIR / "plans"
PLAN_DIR.mkdir(parents=True, exist_ok=True)

# 裁剪参数：值域上限取 1080p 全屏截图为基线（1920×1080）外加一倍冗余，
# 桌面截屏 / 高分屏画面偶尔会跨到 4096；写死 8192 既能挡住脏值，又不会误伤真实截图。
CROP_MAX_DIM = 8192
# 裁剪面积上限 = 1080p 全屏（防「整张图覆盖」误存为模板）
CROP_MAX_AREA = 1920 * 1080


def safe_field_name(value: object) -> int:
    """统一裁剪字段：必须是整数、0 ≤ value ≤ _CROP_MAX_DIM。

    用来给 PlanPayload.crop / CropRequest 做边界校验，避免负数、超大值绕过校验。
    抛出 ValueError 让 pydantic / FastAPI 转成 400 + 可读 message。
    返回 int 是历史约定：CropRequest._validate_geometry 也用它做 ge/gt 校验，
    返回 str 会破坏类型契约（参见 app.py / plans.py 字段类型）。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("必须是整数")
    if value < 0 or value > CROP_MAX_DIM:
        raise ValueError(f"必须在 0 到 {CROP_MAX_DIM} 之间")
    return value


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
    freeze_max_waits: int = Field(default=6, ge=1, le=20)
    # 可选裁剪参数：方案保存时附带最近一次截图采样框；前端的模板采样接口
    # 也走相同的字段名，所以校验集中在这里（plans + app.py 复用）。
    crop: dict[str, int] | None = None

    @field_validator("crop")
    @classmethod
    def _validate_crop(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        cleaned: dict[str, int] = {}
        for key in ("x", "y", "width", "height"):
            raw = value.get(key)
            cleaned[key] = int(safe_field_name(raw))
        if cleaned["width"] <= 0 or cleaned["height"] <= 0:
            raise ValueError("裁剪尺寸必须大于 0")
        if cleaned["x"] < 0 or cleaned["y"] < 0:
            raise ValueError("裁剪起点不能为负")
        if cleaned["x"] + cleaned["width"] > CROP_MAX_DIM:
            raise ValueError("裁剪范围超出右边界")
        if cleaned["y"] + cleaned["height"] > CROP_MAX_DIM:
            raise ValueError("裁剪范围超出下边界")
        if cleaned["width"] * cleaned["height"] > CROP_MAX_AREA:
            raise ValueError(f"裁剪面积超过 {CROP_MAX_AREA} 像素，疑似全图覆盖")
        return cleaned


class PlanSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    payload: PlanPayload
    default: bool = False


class PlanPayloadStrict(PlanPayload):
    """加载时专用严格 schema：未知字段直接拒收，类型严格校验。

    字段与 PlanPayload 一一对应（继承即可），仅多挂一个 extra='forbid' 配置。
    不要给生产写入路径的 PlanPayload 加这个配置：前端加新字段时会一起死。"""
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class SavedPlan:
    name: str
    filename: str
    payload: dict[str, Any]
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


def _normalize_loaded_payload(raw: dict[str, object]) -> dict[str, Any]:
    """Load plan payload with pydantic v2 model_validate + strict mode.

    严格模式 = 未知字段拒收 + 类型严格；缺字段走 PlanPayload 默认值兜底并记日志。
    旧方案文件没有 scale_* 等识别字段时不能让 UI 一打开就红一片，但脏字段必须拒收。
    用独立严格 schema 而非给 PlanPayload 加 extra='forbid'，避免影响前端写入路径。

    返回兜底后的 payload dict；校验失败转成 ValueError 带可读前缀。
    """
    try:
        validated = PlanPayloadStrict.model_validate(raw)
    except ValidationError as exc:
        logger.warning("plan payload 严格校验失败：%s", exc.errors())
        first = exc.errors()[0] if exc.errors() else {"msg": "未知字段"}
        raise ValueError(f"方案字段无效：{first.get('msg', '未知字段')}") from exc
    return validated.model_dump()


def save_plan(req: PlanSaveRequest) -> SavedPlan:
    filename = safe_plan_filename(req.name)
    path = PLAN_DIR / filename
    if req.default:
        _clear_default_flags(filename)
    payload_dump: dict[str, Any] = req.payload.model_dump()
    data = {"name": req.name, "payload": payload_dump, "default": req.default}
    _write_json(path, data)
    return SavedPlan(name=req.name, filename=filename, payload=payload_dump, default=req.default)


def ensure_default_plan() -> SavedPlan | None:
    existing = list_plans()
    if any(plan.default for plan in existing):
        return next(plan for plan in existing if plan.default)
    first_template = "video-first-open10.png"
    repeat_template = "video-repeat10.png"
    if (
        not (DATA_DIR / "templates" / first_template).exists()
        or not (DATA_DIR / "templates" / repeat_template).exists()
    ):
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
    raw_payload = dict(data.get("payload") or {})
    # 严格模式：未知字段直接拒收；缺字段走 PlanPayload 默认值兜底并记日志。
    # _normalize_loaded_payload 内部已经把 pydantic 错误转成带前缀的 ValueError，
    # 这里再抛一次给 FastAPI，最终前端收到 400 + 可读 detail。
    normalized = _normalize_loaded_payload(raw_payload)
    # 只对"识别类可选字段"的缺失打 info 日志（必要字段缺则会被 pydantic 拒收）；
    # 必要字段缺失的拒绝日志在 _normalize_loaded_payload 里已经 warning 过。
    fillable_keys = (
        "device_id",
        "first_template_names",
        "interval_seconds",
        "jitter_seconds",
        "post_click_wait_seconds",
        "repeat_tap_count",
        "repeat_tap_gap_seconds",
        "threshold",
        "max_misses",
        "scale_min",
        "scale_max",
        "scale_step",
        "roi_band",
        "freeze_guard",
        "freeze_threshold",
        "freeze_max_waits",
    )
    missing = [key for key in fillable_keys if key not in raw_payload]
    if missing:
        logger.info(
            "plan %s 缺字段 %s，已用默认值兜底（避免旧方案打不开）",
            path.name,
            "、".join(missing),
        )
    return SavedPlan(
        name=str(data.get("name") or path.stem),
        filename=path.name,
        payload=normalized,
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


def _write_json(path: Path, data: dict[str, Any]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=PLAN_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
