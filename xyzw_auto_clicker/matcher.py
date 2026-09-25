from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 多尺度参数默认值，依据 docs/RECOGNITION-RESEARCH.md 的实测标定。
# 同一个按钮在游戏里会以 395 / 438 / 487 px 三种宽度渲染（相差约 ±11%），
# 而单尺度 matchTemplate 在缩放误差 1% 时得分就跌破 0.86 阈值、3% 时掉到 0.6 以下，
# 所以必须做多尺度扫描。0.78~1.30 覆盖三档之间的全部相对误差。
DEFAULT_SCALE_MIN = 0.78
DEFAULT_SCALE_MAX = 1.30
DEFAULT_SCALE_STEP = 0.035
# 按钮所在的竖直比例带（实测按钮中心恒在 y≈0.66H）。ROI 内搜不到会自动回退全图，
# 所以这里的取值只影响速度，不影响召回。
DEFAULT_ROI_BAND = (0.55, 0.80)
# 尺度档位上限：档位越多越慢，超过这个数说明参数配错了
MAX_SCALE_STEPS = 60
# 粗扫降采样倍数：先在 1/4 分辨率上扫完所有档定位尺度，再回全分辨率精扫。
COARSE_DOWNSCALE = 4
# 精扫小窗在粗扫峰值四周留的余量。实测粗扫位置偏差 ≤7px（x/y），16px 有整整一倍余量；
# 万一没兜住会自动退回整块区域精扫，不会漏检。
COARSE_PAD_PIXELS = 16
# 画面指纹的降采样倍数（用于判断"画面是否静止"）
FINGERPRINT_SCALE = 0.25


@dataclass(frozen=True)
class MatchProfile:
    """一次识别的尺度/ROI 档位。默认值即调研标定的最优值，通常无需改动。"""

    scale_min: float = DEFAULT_SCALE_MIN
    scale_max: float = DEFAULT_SCALE_MAX
    scale_step: float = DEFAULT_SCALE_STEP
    roi_band: tuple[float, float] | None = DEFAULT_ROI_BAND

    def scales(self) -> list[float]:
        """尺度阶梯：以 1.0 为锚点向两侧展开，再补上 range 的两端。

        锚定 1.0 很关键——"模板与画面同尺寸"是最常见的情况。阶梯里如果没有 1.0，
        这种情况只能用 0.99 去匹配，白扣几分（实测：同一按钮 1.0 档 1.000 → 0.99 档 0.955）。
        """
        if self.scale_step <= 0 or self.scale_max < self.scale_min:
            return [1.0]
        low, high = self.scale_min, self.scale_max
        values: set[float] = {round(min(high, max(low, 1.0)), 4)}
        for direction in (1, -1):
            span = (high - 1.0) if direction > 0 else (1.0 - low)
            if span <= 0:
                continue
            for index in range(1, int(span / self.scale_step) + 1):
                values.add(round(1.0 + direction * index * self.scale_step, 4))
        # 步长除不尽时把上下限本身补成端点，保证覆盖完整范围
        values.add(round(low, 4))
        values.add(round(high, 4))
        return sorted(value for value in values if low - 1e-9 <= value <= high + 1e-9)

    def neighbours(self, scale: float) -> list[float]:
        """峰值档左右各一档，用于全分辨率精扫。"""
        values: list[float] = []
        for raw in (scale - self.scale_step, scale, scale + self.scale_step):
            value = round(min(self.scale_max, max(self.scale_min, raw)), 4)
            if value not in values:
                values.append(value)
        return values


@dataclass(frozen=True)
class MatchResult:
    template_name: str
    x: int
    y: int
    width: int
    height: int
    confidence: float
    scale: float = 1.0

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


@dataclass
class _Hit:
    score: float
    x: int
    y: int
    width: int
    height: int
    scale: float


@dataclass
class _TemplateEntry:
    mtime: float
    image: np.ndarray
    # 上次命中的尺度与左上角。连续命中时只在这个尺度 + 这个位置的小窗里复核（热路径），
    # 跌出阈值或位置变化才回到完整流程重新定位。
    memory_scale: float | None = None
    memory_box: tuple[int, int] | None = None


# 降采样后模板/区域的最小边长，低于它就放弃降采样直接全分辨率扫（小图专用兜底）
MIN_COARSE_TEMPLATE_PIXELS = 8
MIN_COARSE_REGION_PIXELS = 16

# 模板缓存上限：超出按 mtime 淘汰最旧的一个。16 对应 4 个默认按钮 × 4 个活动期，每个
# 活动期都会换一遍同名按钮，留一倍余量防 OOM。
TEMPLATES_CACHE_LIMIT = 16


_fingerprint_cache: tuple[bytes, np.ndarray | None] | None = None


def frame_fingerprint(png_bytes: bytes) -> np.ndarray | None:
    """把截图压成小灰度图，用于比较两帧画面是否一致。

    ponytail: 单调用点（Runner 每轮一次），缓存到 1 条足够；同字节复用，省 imdecode+resize。
    """
    global _fingerprint_cache
    if _fingerprint_cache is not None and _fingerprint_cache[0] is png_bytes:
        return _fingerprint_cache[1]
    array = np.frombuffer(png_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        _fingerprint_cache = (png_bytes, None)
        return None
    fp = cv2.resize(image, None, fx=FINGERPRINT_SCALE, fy=FINGERPRINT_SCALE, interpolation=cv2.INTER_AREA)
    _fingerprint_cache = (png_bytes, fp)
    return fp


def frame_delta(previous: np.ndarray, current: np.ndarray) -> float:
    """两帧的平均灰度差（0~255）。实测：静止画面 0.00，转场动画 75~82。"""
    if previous.shape != current.shape:
        return float("inf")
    diff = np.abs(previous.astype(np.int16) - current.astype(np.int16))
    return float(diff.mean())


class ImageMatcher:
    # ROI 矩形计算结果缓存：(height, width, band) -> (left, top, width, height)
    # 同一帧里 _roi_band_rect 会被调用多次（每个模板一次），但画面尺寸与 profile.roi_band
    # 在一轮里都是常量——把结果缓存住，避免每帧每模板都重算 int(...)。
    _roi_rect_cache: dict[tuple[int, int, tuple[float, float] | None], tuple[int, int, int, int]] = {}

    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir
        self._templates: dict[str, _TemplateEntry] = {}
        # 模板在不同 (scale, factor) 下的 resize 结果；命中窗口外的旧条目按插入序淘汰
        self._scaled: dict[tuple[int, float, float], np.ndarray] = {}
        self._scaled_order: deque[tuple[int, float, float]] = deque(maxlen=256)

    def match(
        self,
        screenshot_png: bytes,
        template_names: list[str],
        threshold: float,
        profile: MatchProfile | None = None,
    ) -> MatchResult | None:
        profile = profile or MatchProfile()
        screenshot = self._decode(screenshot_png)
        best: MatchResult | None = None
        for name in template_names:
            entry = self._template_entry(name)
            if entry is None:
                continue
            hit = self._match_one(screenshot, entry, threshold, profile)
            if hit is None or (best is not None and hit.score <= best.confidence):
                continue
            best = MatchResult(
                template_name=name,
                x=hit.x,
                y=hit.y,
                width=hit.width,
                height=hit.height,
                confidence=hit.score,
                scale=hit.scale,
            )
            # 命中记一条 INFO，模板名/置信度/尺度都打出来，便于事后追溯「是不是乱点了」
            logger.info(
                "matcher hit template=%s confidence=%.3f scale=%.3f",
                name,
                hit.score,
                hit.scale,
                extra={"template_name": name, "confidence": hit.score, "scale": hit.scale},
            )
        return best

    def clear_cache(self) -> None:
        """模板被覆盖或删除后调用，丢弃模板缓存与尺度记忆。"""
        self._templates.clear()

    def crop_template(self, screenshot_png: bytes, x: int, y: int, width: int, height: int, output_path: Path) -> None:
        image = self._decode(screenshot_png)
        image_height, image_width = image.shape[:2]
        left = max(0, min(x, image_width - 1))
        top = max(0, min(y, image_height - 1))
        right = max(left + 1, min(left + width, image_width))
        bottom = max(top + 1, min(top + height, image_height))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_image(output_path, image[top:bottom, left:right])

    def _match_one(
        self,
        screenshot: np.ndarray,
        entry: _TemplateEntry,
        threshold: float,
        profile: MatchProfile,
    ) -> _Hit | None:
        template = entry.image
        height, width = screenshot.shape[:2]
        if template.shape[0] > height or template.shape[1] > width:
            return None
        roi = self._roi_band_rect(screenshot, profile)

        # ① 热路径：沿用上次命中的尺度 + 位置，只在那一小块里复核
        if entry.memory_scale is not None:
            if entry.memory_box is not None:
                window = self._window_rect(screenshot, template, *entry.memory_box, entry.memory_scale)
                hit = self._scan(screenshot, entry, [entry.memory_scale], window, precise=True)
                if hit is not None and hit.score >= threshold:
                    entry.memory_box = (hit.x, hit.y)
                    return hit
            # 位置变了（换页面/换活动）→ 退回 ROI 全区域复核同一档
            hit = self._scan(screenshot, entry, [entry.memory_scale], roi, precise=True)
            if hit is not None and hit.score >= threshold:
                entry.memory_box = (hit.x, hit.y)
                return hit

        # ② ROI 内定位：命中即返回，不再白扫一遍全图
        scales = profile.scales()
        hit = self._locate(screenshot, entry, roi, scales, threshold, profile, roi_like=True)
        if hit is not None:
            self._remember(entry, hit)
            return hit

        # ③ 回退全图：按钮不在 ROI 带里（或画面里本来就没有按钮）
        full = (0, 0, width, height)
        if full != roi:
            hit = self._locate(screenshot, entry, full, scales, threshold, profile, roi_like=False)
            if hit is not None:
                self._remember(entry, hit)
                return hit
        return None

    @staticmethod
    def _remember(entry: _TemplateEntry, hit: _Hit) -> None:
        entry.memory_scale = hit.scale
        entry.memory_box = (hit.x, hit.y)

    def _locate(
        self,
        screenshot: np.ndarray,
        entry: _TemplateEntry,
        rect: tuple[int, int, int, int],
        scales: list[float],
        threshold: float,
        profile: MatchProfile,
        *,
        roi_like: bool,
    ) -> _Hit | None:
        """粗扫定位尺度 → 小窗精扫 → （只在 ROI 里）整区域精扫。

        判定必须落在精扫上：粗扫在 1/4 分辨率下分数天然偏低，拿它当阈值，
        每一帧都会白白多扫一遍全图（实测冷启动 102ms，比旧实现还慢）。
        精扫也只在小窗里做，因为实测粗扫位置偏差 ≤7px。
        """
        coarse = self._scan(screenshot, entry, scales, rect, precise=False)
        if coarse is None:
            return None
        neighbours = profile.neighbours(coarse.scale)
        window = self._window_rect(screenshot, entry.image, coarse.x, coarse.y, coarse.scale)
        hit = self._scan(screenshot, entry, neighbours, window, precise=True)
        if hit is not None and hit.score >= threshold:
            return hit
        if roi_like and window != rect:
            # 小窗没兜住（粗扫位置偏了）→ 在 ROI 里整区域复核一次
            hit = self._scan(screenshot, entry, neighbours, rect, precise=True)
            if hit is not None and hit.score >= threshold:
                return hit
        return None

    @staticmethod
    def _window_rect(
        screenshot: np.ndarray,
        template: np.ndarray,
        x: int,
        y: int,
        scale: float,
    ) -> tuple[int, int, int, int]:
        height, width = screenshot.shape[:2]
        box_width = int(round(template.shape[1] * scale))
        box_height = int(round(template.shape[0] * scale))
        left = max(0, x - COARSE_PAD_PIXELS)
        top = max(0, y - COARSE_PAD_PIXELS)
        right = min(width, x + box_width + COARSE_PAD_PIXELS)
        bottom = min(height, y + box_height + COARSE_PAD_PIXELS)
        return left, top, right - left, bottom - top

    def _scan(
        self,
        screenshot: np.ndarray,
        entry: _TemplateEntry,
        scales: list[float],
        rect: tuple[int, int, int, int],
        *,
        precise: bool,
    ) -> _Hit | None:
        left, top, width, height = rect
        if width <= 0 or height <= 0:
            return None
        region = screenshot[top : top + height, left : left + width]
        if region.size == 0:
            return None
        template = entry.image
        factor = 1.0 if precise else 1 / COARSE_DOWNSCALE
        if factor != 1.0:
            # 小图降采样后连模板特征都剩不下，这类情况直接全分辨率扫
            coarse_template = min(template.shape[0], template.shape[1]) * min(scales) * factor
            coarse_region = min(region.shape[0], region.shape[1]) * factor
            if coarse_template < MIN_COARSE_TEMPLATE_PIXELS or coarse_region < MIN_COARSE_REGION_PIXELS:
                factor = 1.0
        source = region if factor == 1.0 else cv2.resize(region, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        if source.shape[0] < 8 or source.shape[1] < 8:
            return None

        best: _Hit | None = None
        for scale in scales:
            scaled = self._scaled_template(entry, scale, factor)
            if scaled is None or scaled.shape[0] > source.shape[0] or scaled.shape[1] > source.shape[1]:
                continue
            scores = cv2.matchTemplate(source, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, max_location = cv2.minMaxLoc(scores)
            if best is not None and max_value <= best.score:
                continue
            best = _Hit(
                score=float(max_value),
                x=left + int(round(max_location[0] / factor)),
                y=top + int(round(max_location[1] / factor)),
                width=int(round(scaled.shape[1] / factor)),
                height=int(round(scaled.shape[0] / factor)),
                scale=scale,
            )
        return best

    @staticmethod
    def _resize_template(template: np.ndarray, scale: float) -> np.ndarray | None:
        if abs(scale - 1.0) < 1e-6:
            return template
        height = int(round(template.shape[0] * scale))
        width = int(round(template.shape[1] * scale))
        if height < 8 or width < 8:
            return None
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        return cv2.resize(template, (width, height), interpolation=interpolation)

    def _scaled_template(self, entry: _TemplateEntry, scale: float, factor: float) -> np.ndarray | None:
        """按 (模板版本, scale, factor) 缓存 resize 结果，避免每帧重算。
        ponytail: 256 条够 ~4 模板 × 16 档 × 4 factor；模板替换 mtime 即变，自动失效。
        """
        effective = scale * factor
        if abs(effective - 1.0) < 1e-6:
            return entry.image
        version = id(entry) ^ int(entry.mtime * 1000)
        key = (version, scale, factor)
        cached = self._scaled.get(key)
        if cached is not None:
            return cached
        resized = self._resize_template(entry.image, effective)
        if resized is None:
            return None
        self._scaled[key] = resized
        self._scaled_order.append(key)
        return resized

    @classmethod
    def _roi_band_rect(cls, screenshot: np.ndarray, profile: MatchProfile) -> tuple[int, int, int, int]:
        """把 ROI 矩形的计算结果按 (画面尺寸, roi_band) 缓存住。

        ponytail: 缓存上限跟着 class dict 自带；同一 (h, w, band) 只算一次。
        在 _match_one 里每个模板都会调用一次，N 个模板 + 每帧重算就是 N 次冗余。
        """
        height, width = screenshot.shape[:2]
        cache_key = (height, width, profile.roi_band)
        cached = cls._roi_rect_cache.get(cache_key)
        if cached is not None:
            return cached
        band = profile.roi_band
        if not band:
            rect = (0, 0, width, height)
        else:
            top = max(0, min(height, int(height * band[0])))
            bottom = max(top, min(height, int(height * band[1])))
            if top == 0 and bottom == height:
                rect = (0, 0, width, height)
            else:
                rect = (0, top, width, bottom - top)
        cls._roi_rect_cache[cache_key] = rect
        return rect

    def _template_entry(self, name: str) -> _TemplateEntry | None:
        path = self.template_dir / name
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        entry = self._templates.get(name)
        if entry is not None and entry.mtime == mtime:
            return entry
        image = self._read_image(path)
        if image is None:
            return None
        # 模板变了就重建，顺带丢掉旧模板的尺度记忆
        entry = _TemplateEntry(mtime=mtime, image=image)
        self._templates[name] = entry
        # 超出上限就按 mtime 淘汰最旧的：mtime 越老的越久没被覆盖，最先放弃。
        if len(self._templates) > TEMPLATES_CACHE_LIMIT:
            oldest_name = min(self._templates, key=lambda key: self._templates[key].mtime)
            if oldest_name != name:
                self._templates.pop(oldest_name, None)
        return entry

    def _read_image(self, path: Path) -> np.ndarray | None:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)

    def _write_image(self, path: Path, image: np.ndarray) -> None:
        ok, encoded = cv2.imencode(path.suffix or ".png", image)
        if not ok:
            raise ValueError("模板保存失败")
        encoded.tofile(path)

    def _decode(self, png_bytes: bytes) -> np.ndarray:
        array = np.frombuffer(png_bytes, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("截图解码失败")
        return image
