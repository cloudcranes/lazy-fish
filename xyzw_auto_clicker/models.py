from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .matcher import (
    DEFAULT_ROI_BAND,
    DEFAULT_SCALE_MAX,
    DEFAULT_SCALE_MIN,
    DEFAULT_SCALE_STEP,
    MAX_SCALE_STEPS,
    MatchProfile,
)


@dataclass(frozen=True)
class TaskConfig:
    name: str
    template_names: list[str]
    click_count: int
    first_template_names: list[str] | None = None
    interval_seconds: float = 0.8
    jitter_seconds: float = 0.2
    post_click_wait_seconds: float = 4.8
    repeat_tap_count: int = 2
    repeat_tap_gap_seconds: float = 0.12
    threshold: float = 0.86
    max_misses: int = 8
    device_id: str | None = None
    # 多尺度识别参数（见 docs/RECOGNITION-RESEARCH.md）。默认值即实测标定的最优值。
    scale_min: float = DEFAULT_SCALE_MIN
    scale_max: float = DEFAULT_SCALE_MAX
    scale_step: float = DEFAULT_SCALE_STEP
    roi_band: tuple[float, float] | None = DEFAULT_ROI_BAND
    # 动作前等画面静止：消掉"截在动画中间"的无效重试（实测动画帧得分只有 0.318）
    freeze_guard: bool = True
    freeze_threshold: float = 3.0
    # 默认 6 次：之前是 4，但晚高峰宝箱动画偶尔会拉到 5+ 帧，4 次不够稳；6 次
    # 是 4.8s × 6 ≈ 28s 上限，留一倍余量给更慢的动画，又不至于把任务卡死。
    freeze_max_waits: int = 6

    def templates_for_click(self, click_index: int) -> list[str]:
        if click_index == 0 and self.first_template_names:
            return self.first_template_names
        return self.template_names

    def tap_count_for_click(self, click_index: int) -> int:
        return 1 if click_index == 0 and self.first_template_names else self.repeat_tap_count

    def match_profile(self) -> MatchProfile:
        band = tuple(self.roi_band) if self.roi_band else None
        return MatchProfile(
            scale_min=self.scale_min,
            scale_max=self.scale_max,
            scale_step=self.scale_step,
            roi_band=band,
        )

    def validate(self, template_dir: Path) -> None:
        if not self.template_names:
            raise ValueError("至少选择一个后续模板")
        if self.click_count <= 0:
            raise ValueError("点击次数必须大于 0")
        if self.interval_seconds < 0.1:
            raise ValueError("点击间隔不能小于 0.1 秒")
        if self.jitter_seconds < 0:
            raise ValueError("随机抖动不能小于 0")
        if self.post_click_wait_seconds < 0:
            raise ValueError("点后等待不能小于 0")
        if not 1 <= self.repeat_tap_count <= 5:
            raise ValueError("后续连点次数必须在 1 到 5 之间")
        if self.repeat_tap_gap_seconds < 0.03:
            raise ValueError("后续连点间隔不能小于 0.03 秒")
        if not 0.1 <= self.threshold <= 1:
            raise ValueError("置信度阈值必须在 0.1 到 1 之间")
        if self.max_misses <= 0:
            raise ValueError("连续失败次数必须大于 0")
        if not 0.1 <= self.scale_min <= 5:
            raise ValueError("最小尺度必须在 0.1 到 5 之间")
        if not 0.1 <= self.scale_max <= 5:
            raise ValueError("最大尺度必须在 0.1 到 5 之间")
        if self.scale_max < self.scale_min:
            raise ValueError("最大尺度不能小于最小尺度")
        if not 0.005 <= self.scale_step <= 0.5:
            raise ValueError("尺度步长必须在 0.005 到 0.5 之间")
        if len(self.match_profile().scales()) > MAX_SCALE_STEPS:
            raise ValueError(f"尺度档位过多（超过 {MAX_SCALE_STEPS} 档），请调大步长或收窄范围")
        if self.roi_band is not None:
            low, high = self.roi_band
            if not 0 <= low < high <= 1:
                raise ValueError("搜索区域必须是 0 到 1 之间的上下界")
        if self.freeze_threshold < 0:
            raise ValueError("静止判定阈值不能小于 0")
        if not 1 <= self.freeze_max_waits <= 20:
            raise ValueError("静止等待次数必须在 1 到 20 之间")
        for name in [*(self.first_template_names or []), *self.template_names]:
            path = template_dir / name
            if not path.exists() or not path.is_file():
                raise ValueError(f"模板不存在: {name}")
