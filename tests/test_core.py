from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from xyzw_auto_clicker.matcher import (
    ImageMatcher,
    MatchProfile,
    frame_delta,
    frame_fingerprint,
)
from xyzw_auto_clicker.models import TaskConfig


def test_template_match_returns_center(tmp_path: Path) -> None:
    screenshot = np.zeros((120, 160, 3), dtype=np.uint8)
    screenshot[40:60, 70:95] = (255, 255, 255)
    screenshot[45:55, 78:88] = (0, 0, 0)
    template = screenshot[40:60, 70:95]
    template_path = tmp_path / "button.png"
    assert cv2.imwrite(str(template_path), template)
    ok, encoded = cv2.imencode(".png", screenshot)
    assert ok

    matcher = ImageMatcher(tmp_path)
    result = matcher.match(encoded.tobytes(), ["button.png"], 0.9)

    assert result is not None
    assert result.center == (82, 50)
    assert result.confidence >= 0.9


def test_config_rejects_missing_template(tmp_path: Path) -> None:
    config = TaskConfig(name="chest", template_names=["missing.png"], click_count=1)

    with pytest.raises(ValueError, match="模板不存在"):
        config.validate(tmp_path)


def test_config_rejects_invalid_count(tmp_path: Path) -> None:
    template = tmp_path / "button.png"
    template.write_bytes(b"x")
    config = TaskConfig(name="chest", template_names=["button.png"], click_count=0)

    with pytest.raises(ValueError, match="点击次数"):
        config.validate(tmp_path)


def test_config_uses_first_template_only_for_first_click() -> None:
    config = TaskConfig(
        name="chest",
        template_names=["再抽10次.png"],
        first_template_names=["打开10个宝箱.png"],
        click_count=2,
    )

    assert config.templates_for_click(0) == ["打开10个宝箱.png"]
    assert config.templates_for_click(1) == ["再抽10次.png"]


def test_config_double_taps_repeat_clicks_only() -> None:
    config = TaskConfig(
        name="chest",
        template_names=["再抽10次.png"],
        first_template_names=["打开10个宝箱.png"],
        click_count=2,
        repeat_tap_count=2,
    )

    assert config.tap_count_for_click(0) == 1
    assert config.tap_count_for_click(1) == 2


def test_plan_filename_keeps_safe_suffix() -> None:
    from xyzw_auto_clicker.plans import safe_plan_filename

    assert safe_plan_filename("木箱十连") == "木箱十连.json"
    assert safe_plan_filename("../bad name") == "bad_name.json"


def test_runner_state_exposes_ui_fields() -> None:
    from xyzw_auto_clicker.runner import RunnerState

    state = RunnerState(status="running", clicked=2, target=4)
    snapshot = state.snapshot()

    assert snapshot["progress_percent"] == 50
    assert snapshot["is_running"] is True


def test_saved_plan_default_field(tmp_path: Path) -> None:
    from xyzw_auto_clicker.plans import PlanPayload, PlanSaveRequest

    req = PlanSaveRequest(
        name="默认方案",
        default=True,
        payload=PlanPayload(template_names=["repeat.png"], click_count=1),
    )

    assert req.default is True
    assert req.payload.template_names == ["repeat.png"]


# --- 多尺度识别（对应 docs/RECOGNITION-RESEARCH.md） ---------------------------------
#
# 线上失败的本质是"同一个按钮会以 3 种尺寸渲染"，所以这里全部围绕"同一张模板要能
# 认出不同尺寸的按钮，同时不能把没有按钮的画面认成有"来断言。

CANVAS_HEIGHT = 600
CANVAS_WIDTH = 400
BUTTON_CENTER = (200, 395)  # 落在默认 ROI 带（y ∈ [330, 480]）内


def _make_button(width: int = 100, height: int = 30, seed: int = 0) -> np.ndarray:
    """造一个有结构纹理的按钮：琥珀底 + 深色文字条 + 轻微噪声。"""
    rng = np.random.default_rng(seed)
    button = np.full((height, width, 3), (30, 170, 240), dtype=np.uint8)
    step = (width - 16) / 6.0
    for index in range(6):
        left = int(8 + index * step)
        button[8 : height - 8, left : left + 6] = (20, 40, 90)
    noise = rng.integers(-6, 7, button.shape, dtype=np.int16)
    return np.clip(button.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _make_canvas(button: np.ndarray, scale: float = 1.0, center: tuple[int, int] = BUTTON_CENTER) -> np.ndarray:
    """把按钮按 scale 缩放到带纹理的背景上。"""
    rng = np.random.default_rng(99)
    base = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 55, dtype=np.uint8)
    canvas = np.clip(base.astype(np.int16) + rng.integers(-14, 15, base.shape, dtype=np.int16), 0, 255).astype(np.uint8)
    width = max(8, int(round(button.shape[1] * scale)))
    height = max(8, int(round(button.shape[0] * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    patch = cv2.resize(button, (width, height), interpolation=interpolation)
    left = int(round(center[0] - width / 2))
    top = int(round(center[1] - height / 2))
    canvas[top : top + height, left : left + width] = patch
    return canvas


def _encode(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _write_template(tmp_path: Path, image: np.ndarray, name: str = "button.png") -> Path:
    path = tmp_path / name
    assert cv2.imwrite(str(path), image)
    return path


@pytest.mark.parametrize("scale", [0.90, 1.00, 1.11, 1.23])
def test_multiscale_matches_scaled_button(tmp_path: Path, scale: float) -> None:
    """0.90~1.23 各档渲染尺寸都必须命中——这几档正是线上失败的那三种宽度。"""
    button = _make_button()
    _write_template(tmp_path, button)
    canvas = _make_canvas(button, scale=scale)

    result = ImageMatcher(tmp_path).match(_encode(canvas), ["button.png"], 0.86)

    assert result is not None, f"缩放 {scale} 漏检"
    assert abs(result.scale - scale) <= 0.05, f"缩放 {scale} 报出的尺度 {result.scale} 偏差过大"
    center_x, center_y = result.center
    assert abs(center_x - BUTTON_CENTER[0]) <= 4
    assert abs(center_y - BUTTON_CENTER[1]) <= 4


def test_single_scale_baseline_would_miss_same_frame(tmp_path: Path) -> None:
    """对照实验：同一张图单尺度必然漏检、多尺度必须命中（证明修的是真问题）。"""
    button = _make_button()
    _write_template(tmp_path, button)
    canvas = _make_canvas(button, scale=1.11)

    single_scale = float(cv2.minMaxLoc(cv2.matchTemplate(canvas, button, cv2.TM_CCOEFF_NORMED))[1])
    result = ImageMatcher(tmp_path).match(_encode(canvas), ["button.png"], 0.86)

    assert single_scale < 0.86, f"单尺度竟然过了阈值（{single_scale:.3f}），这组对照失去意义"
    assert result is not None and result.confidence >= 0.86


def test_flat_screen_does_not_match(tmp_path: Path) -> None:
    """纯色画面（转场黑屏/纯白）绝不误报——误点比漏点更糟。"""
    button = _make_button()
    _write_template(tmp_path, button)
    matcher = ImageMatcher(tmp_path)

    for value in (0, 128, 255):
        flat = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), value, dtype=np.uint8)
        assert matcher.match(_encode(flat), ["button.png"], 0.86) is None, f"纯色 {value} 误报"


def test_textured_screen_without_button_does_not_match(tmp_path: Path) -> None:
    """有纹理但没有按钮的画面同样不误报。"""
    button = _make_button()
    _write_template(tmp_path, button)
    rng = np.random.default_rng(7)
    base = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 55, dtype=np.uint8)
    noisy = np.clip(base.astype(np.int16) + rng.integers(-14, 15, base.shape, dtype=np.int16), 0, 255).astype(np.uint8)

    assert ImageMatcher(tmp_path).match(_encode(noisy), ["button.png"], 0.86) is None


def test_match_falls_back_outside_roi(tmp_path: Path) -> None:
    """按钮跑到默认 ROI 带之外时，必须靠全图回退命中，而不是直接漏检。"""
    button = _make_button()
    _write_template(tmp_path, button)
    canvas = _make_canvas(button, scale=1.0, center=(200, 120))  # y=120 远在 ROI 带（330~480）之外

    result = ImageMatcher(tmp_path).match(_encode(canvas), ["button.png"], 0.86)

    assert result is not None
    assert abs(result.center[1] - 120) <= 4


def test_scale_memory_reuses_last_hit(tmp_path: Path) -> None:
    """命中后记住尺度，下一帧先按记忆档复核（热路径）。"""
    button = _make_button()
    _write_template(tmp_path, button)
    matcher = ImageMatcher(tmp_path)
    canvas = _make_canvas(button, scale=1.11)

    first = matcher.match(_encode(canvas), ["button.png"], 0.86)
    assert first is not None
    entry = matcher._templates["button.png"]
    assert entry.memory_scale == pytest.approx(first.scale, abs=0.001)
    assert entry.memory_box == (first.x, first.y)

    second = matcher.match(_encode(canvas), ["button.png"], 0.86)
    assert second is not None
    assert second.scale == first.scale
    assert second.center == first.center


def test_scale_memory_recovers_when_button_moves(tmp_path: Path) -> None:
    """按钮换了位置时，热路径的小窗会落空，但必须能自动重新定位（不能就此漏检）。"""
    button = _make_button()
    _write_template(tmp_path, button)
    matcher = ImageMatcher(tmp_path)

    assert matcher.match(_encode(_make_canvas(button)), ["button.png"], 0.86) is not None
    moved = _make_canvas(button, center=(120, 360))
    result = matcher.match(_encode(moved), ["button.png"], 0.86)

    assert result is not None
    assert abs(result.center[0] - 120) <= 4
    assert abs(result.center[1] - 360) <= 4


def test_scale_ladder_anchors_exact_one() -> None:
    """1.0 必须在阶梯里：模板与画面同尺寸是最常见的情况。"""
    for low, high, step in [(0.78, 1.30, 0.035), (0.5, 2.0, 0.5), (0.9, 1.1, 0.05)]:
        scales = MatchProfile(scale_min=low, scale_max=high, scale_step=step).scales()
        assert 1.0 in scales, f"[{low},{high}] 步长 {step} 的阶梯里没有 1.0"
        assert scales[0] == pytest.approx(low)
        assert scales[-1] == pytest.approx(high)


def test_exact_size_template_uses_scale_one(tmp_path: Path) -> None:
    """模板与画面同尺寸时应当直接命中 1.0 档，而不是被迫用 0.99 去凑。"""
    button = _make_button()
    _write_template(tmp_path, button)

    result = ImageMatcher(tmp_path).match(_encode(_make_canvas(button, scale=1.0)), ["button.png"], 0.86)

    assert result is not None
    assert result.scale == pytest.approx(1.0)


def test_template_cache_invalidates_when_file_changes(tmp_path: Path) -> None:
    """模板被「截图采样」覆盖后，模板缓存与尺度记忆都要失效。"""
    button = _make_button()
    path = _write_template(tmp_path, button)
    matcher = ImageMatcher(tmp_path)
    assert matcher.match(_encode(_make_canvas(button)), ["button.png"], 0.86) is not None
    assert matcher._templates["button.png"].memory_scale is not None

    replacement = _make_button(width=60, height=24, seed=3)
    assert cv2.imwrite(str(path), replacement)
    os.utime(path, (1_700_000_000, 1_700_000_000))  # 显式改 mtime，绕开文件系统时间精度

    assert matcher.match(_encode(_make_canvas(button)), ["button.png"], 0.86) is None
    assert matcher._templates["button.png"].memory_scale is None

    matcher.clear_cache()
    assert matcher._templates == {}


def test_match_picks_highest_confidence_template(tmp_path: Path) -> None:
    button = _make_button()
    _write_template(tmp_path, button, "good.png")
    _write_template(tmp_path, _make_button(width=70, height=22, seed=5), "other.png")

    result = ImageMatcher(tmp_path).match(_encode(_make_canvas(button, scale=1.11)), ["other.png", "good.png"], 0.86)

    assert result is not None
    assert result.template_name == "good.png"


def test_match_returns_none_for_missing_template(tmp_path: Path) -> None:
    assert ImageMatcher(tmp_path).match(_encode(_make_canvas(_make_button())), ["nope.png"], 0.86) is None


def test_match_profile_scale_ladder() -> None:
    profile = MatchProfile()
    scales = profile.scales()

    assert scales[0] == pytest.approx(0.78)
    assert scales[-1] == pytest.approx(1.30)
    assert 15 <= len(scales) <= 17  # 步长 0.035 → 约 16 档
    assert all(later > earlier for earlier, later in zip(scales, scales[1:]))
    assert profile.neighbours(1.0) == pytest.approx([0.965, 1.0, 1.035])
    assert min(profile.neighbours(0.78)) >= 0.78
    assert max(profile.neighbours(1.30)) <= 1.30


def test_match_profile_without_roi_scans_whole_screen(tmp_path: Path) -> None:
    """关掉 ROI 带后仍能正常匹配（只是少了提速）。"""
    button = _make_button()
    _write_template(tmp_path, button)

    result = ImageMatcher(tmp_path).match(
        _encode(_make_canvas(button, scale=1.11)), ["button.png"], 0.86, MatchProfile(roi_band=None)
    )

    assert result is not None


def test_frame_delta_detects_motion() -> None:
    still = np.full((100, 100), 60, dtype=np.uint8)
    assert frame_delta(still, still.copy()) == 0.0

    moved = still.copy()
    moved[40:60, 40:60] = 90
    assert frame_delta(still, moved) > 1.0
    assert frame_delta(still, np.full((50, 50), 60, dtype=np.uint8)) == float("inf")


def test_frame_fingerprint_downscales() -> None:
    fingerprint = frame_fingerprint(_encode(np.zeros((800, 480, 3), dtype=np.uint8)))

    assert fingerprint is not None
    assert fingerprint.shape == (200, 120)


def test_frame_fingerprint_rejects_garbage() -> None:
    assert frame_fingerprint(b"not a png") is None


def test_config_defaults_match_research_baseline() -> None:
    """旧方案文件缺这些字段时，默认值必须是调研标定的最优值。"""
    config = TaskConfig(name="chest", template_names=["a.png"], click_count=1)

    assert config.scale_min == 0.78
    assert config.scale_max == 1.30
    assert config.scale_step == 0.035
    assert config.roi_band == (0.55, 0.80)
    assert config.freeze_guard is True


def test_config_match_profile_carries_params() -> None:
    config = TaskConfig(
        name="chest",
        template_names=["a.png"],
        click_count=1,
        scale_min=0.9,
        scale_max=1.2,
        scale_step=0.05,
        roi_band=(0.5, 0.9),
    )
    profile = config.match_profile()

    assert profile.scale_min == 0.9
    assert profile.scale_max == 1.2
    assert profile.scale_step == 0.05
    assert profile.roi_band == (0.5, 0.9)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"scale_min": 1.5, "scale_max": 1.0}, "最大尺度"),
        ({"scale_step": 0.0}, "尺度步长"),
        ({"roi_band": (0.9, 0.5)}, "搜索区域"),
        ({"freeze_max_waits": 0}, "静止等待"),
        ({"freeze_threshold": -1}, "静止判定"),
    ],
)
def test_config_validates_recognition_params(override: dict, message: str, tmp_path: Path) -> None:
    (tmp_path / "button.png").write_bytes(b"x")
    config = TaskConfig(name="chest", template_names=["button.png"], click_count=1, **override)

    with pytest.raises(ValueError, match=message):
        config.validate(tmp_path)


def test_plan_payload_accepts_legacy_payload() -> None:
    """旧方案文件里没有识别字段，加载后必须自动补齐默认值。"""
    from xyzw_auto_clicker.plans import PlanPayload

    payload = PlanPayload(template_names=["a.png"], click_count=3)

    assert payload.scale_min == 0.78
    assert payload.scale_max == 1.30
    assert payload.roi_band == (0.55, 0.80)
    assert payload.freeze_guard is True


def test_plan_payload_roundtrip_keeps_roi_band() -> None:
    """JSON 往返（list）后仍要还原成 tuple，否则 TaskConfig 校验会失败。"""
    from xyzw_auto_clicker.plans import PlanPayload

    payload = PlanPayload.model_validate({"template_names": ["a.png"], "click_count": 1, "roi_band": [0.5, 0.85]})

    assert payload.roi_band == (0.5, 0.85)


def test_delete_plan_moves_file_to_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """删除 = 移入回收目录：不碰 unlink（部分环境对批量删除有守卫，会直接 SystemExit），
    而且误删还能捞回来。"""
    from xyzw_auto_clicker import plans

    plan_dir = tmp_path / "plans"
    trash_dir = tmp_path / "plans-trash"
    plan_dir.mkdir()
    monkeypatch.setattr(plans, "PLAN_DIR", plan_dir)
    monkeypatch.setattr(plans, "TRASH_DIR", trash_dir)

    target = plan_dir / "demo.json"
    target.write_text('{"name": "demo", "payload": {"template_names": ["a.png"], "click_count": 1}}', encoding="utf-8")

    plans.delete_plan("demo.json")

    assert not target.exists()
    assert (trash_dir / "demo.json").exists()

    # 幂等：文件已经不在时再删一次不应报错
    plans.delete_plan("demo.json")

    # 同名文件重复删除只覆盖回收目录里的那一份，不会无限堆积
    target.write_text("{}", encoding="utf-8")
    plans.delete_plan("demo.json")
    assert len(list(trash_dir.glob("*.json"))) == 1


def test_delete_plan_survives_unlink_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """即使运行环境把 Path.unlink 换成了"抛 SystemExit 的守卫"，删除也必须成功。"""
    from xyzw_auto_clicker import plans

    plan_dir = tmp_path / "plans"
    trash_dir = tmp_path / "plans-trash"
    plan_dir.mkdir()
    monkeypatch.setattr(plans, "PLAN_DIR", plan_dir)
    monkeypatch.setattr(plans, "TRASH_DIR", trash_dir)
    (plan_dir / "demo.json").write_text("{}", encoding="utf-8")

    def guard(*args, **kwargs):  # 模拟被守护策略接管的 unlink
        raise SystemExit(1)

    monkeypatch.setattr(Path, "unlink", guard)

    plans.delete_plan("demo.json")  # 不应抛出

    assert (trash_dir / "demo.json").exists()


def test_build_chest_config_maps_every_plan_field() -> None:
    """方案字段与 TaskConfig 字段必须一一对应（chest.py 是整体展开，缺一个就会报错）。"""
    from xyzw_auto_clicker.plans import PlanPayload
    from xyzw_auto_clicker.tasks.chest import build_chest_config

    payload = PlanPayload(template_names=["a.png"], click_count=5, threshold=0.9, scale_max=1.2)
    config = build_chest_config(payload)

    assert config.name == "chest"
    assert config.click_count == 5
    assert config.threshold == 0.9
    assert config.scale_max == 1.2
