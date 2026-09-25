"""识别链路回归评测：在真实语料上量召回 / 误报 / 跨状态误匹配 / 耗时 / 尺度分布。

用途：`docs/RECOGNITION-RESEARCH.md` 里的结论（召回 100%、误报 0%、尺度三档全覆盖、
热路径约 20ms）都在这个脚本上复现。改动 `matcher.py` 或识别参数的默认值后跑一遍，
能立刻看出是否退化。

真值（ground truth）由**颜色 + 几何特征**独立标注，与被测算法完全解耦。

### 为什么真值要分状态

`data/screenshots/` 3491 帧里混了三个页面，底部都有琥珀金按钮：

| 页面状态 | 按钮中心 x | 按钮宽度 | 出现帧数（1164 抽样） | 对应模板 |
| --- | --- | --- | --- | --- |
| 其它页面（6 月招募为主，含战斗/转场） | ≈ 500（招募按钮） | 240 ~ 280 | 1112 | 无 |
| 宝箱·打开10个宝箱 | **≈ 533** | 435 | 13 | `video-first-open10.png` |
| 宝箱·再抽10次 | **≈ 803** | 395/436/487 | 39 | `video-repeat10.png` |

两个宝箱状态的**按钮宽度几乎一样、中心 x 差了 270px**。只按"底部有没有琥珀色块"标真值，
会把招募按钮和另一个宝箱状态全都算成真值，召回率假性掉到个位数。
所以每个模板声明自己对应的状态几何，脚本据此：
- 只在**自己状态的帧**上算召回（漏检风险）；
- 在**其它所有帧**（另一个宝箱状态 + 招募 + 战斗等）上算误报（乱点风险）。
后者等价于"用 A 模板去匹配 B 按钮所在帧会不会误命中"，是交叉验证的核心。

  python tests/recognition_eval.py                      # 默认模板 + 每 3 帧抽样
  python tests/recognition_eval.py --stride 1           # 全量（慢，3491 帧级）
  python tests/recognition_eval.py --templates fish.png # 换模板看是否误报

判读方式：
- `召回` = 自己状态的帧中命中的比例。掉下来通常意味着尺度范围或步长配错了。
- `误报` = 命中落在非自己状态的帧上。涨起来意味着阈值太低，或两档按钮区分度不足 —— **这一项最危险**，
  它代表任务会在错误的按钮上乱点。
- `位置错` = 命中在正确状态内、但中心偏离真值超过容差。说明尺度扫描定位歪了。
- 耗时按**命中帧**（热路径/冷启动）与**无目标帧**（完整兜底）分开看，混在一起没有意义。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyzw_auto_clicker.matcher import ImageMatcher, MatchProfile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = ROOT / "data" / "screenshots"
TEMPLATE_DIR = ROOT / "data" / "templates"
THRESHOLD = 0.86
# 按钮带（比例），比 matcher 的 ROI 略宽，避免真值标注被 ROI 边界裁掉
GT_BAND = (0.50, 0.88)
# 位置容差：命中中心与真值中心的最大允许偏差
POSITION_TOLERANCE_X = 60
POSITION_TOLERANCE_Y = 60


@dataclass(frozen=True)
class StateSpec:
    """一个页面状态的按钮几何特征。"""

    key: str
    label: str
    center_x: tuple[int, int]
    width: tuple[int, int]


# 宝箱活动的两个状态（模板与状态的对应关系写在这里）
CHEST_FIRST = StateSpec("first", "宝箱·打开10个宝箱", (450, 620), (350, 560))
CHEST_REPEAT = StateSpec("repeat", "宝箱·再抽10次", (700, 900), (350, 560))
STATES = [CHEST_FIRST, CHEST_REPEAT]

# 每个模板对应的状态；未列出的模板按"任意宝箱状态"处理（只测误报，不测召回）
TEMPLATE_STATE = {
    "video-first-open10.png": CHEST_FIRST,
    "video-repeat10.png": CHEST_REPEAT,
}

# 单个琥珀块的最小尺寸，过滤文字描边等碎块
MIN_BLOCK_WIDTH = 120
MIN_BLOCK_HEIGHT = 40
MIN_BLOCK_AREA = 8000


def _amber_blocks(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """找按钮带里的琥珀金连通块，返回 [(中心x, 中心y, 宽, 高)]，按宽度降序。

    纯几何/颜色判定，不调用任何匹配算法。
    """
    height, _ = image.shape[:2]
    top, bottom = int(height * GT_BAND[0]), int(height * GT_BAND[1])
    band = image[top:bottom]
    blue, green, red = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    # 琥珀金：R 高、G 中、B 低，且 R > G > B
    mask = (red > 150) & (green > 90) & (green < 215) & (blue < 110) & (red > green) & (green > blue)
    mask = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    blocks: list[tuple[int, int, int, int]] = []
    for index in range(1, count):
        _, _, width, height_, area = stats[index]
        if width < MIN_BLOCK_WIDTH or height_ < MIN_BLOCK_HEIGHT or area < MIN_BLOCK_AREA:
            continue
        if not 1.8 <= width / max(height_, 1) <= 5.0:
            continue
        blocks.append((int(round(centroids[index][0])), int(round(centroids[index][1])) + top, int(width), int(height_)))
    return sorted(blocks, key=lambda block: -block[2])


def classify(image: np.ndarray) -> tuple[StateSpec | None, tuple[int, int, int] | None]:
    """判定这一帧属于哪个宝箱状态。返回 (状态, (中心x, 中心y, 宽度))。"""
    for center_x, center_y, width, _ in _amber_blocks(image):
        for state in STATES:
            if state.width[0] <= width <= state.width[1] and state.center_x[0] <= center_x <= state.center_x[1]:
                return state, (center_x, center_y, width)
    return None, None


def evaluate(template_names: list[str], stride: int) -> int:
    all_frames = sorted(SCREENSHOT_DIR.glob("*.png"))
    frames = all_frames[::stride]
    if not frames:
        print(f"没有找到截图：{SCREENSHOT_DIR}")
        return 2
    missing = [name for name in template_names if not (TEMPLATE_DIR / name).exists()]
    if missing:
        print(f"模板不存在：{', '.join(missing)}")
        return 2

    print(f"语料：{len(frames)} 帧（每 {stride} 帧取 1，共 {len(all_frames)} 帧）")
    profile = MatchProfile()
    print(f"尺度阶梯：{len(profile.scales())} 档  {profile.scales()[0]} ~ {profile.scales()[-1]}"
          f"  （含 1.0：{1.0 in profile.scales()}）  阈值：{THRESHOLD}")
    print()

    # 真值标注只做一次（与模板无关），供所有模板共用
    corpus: list[tuple[bytes, StateSpec | None, tuple[int, int, int] | None]] = []
    skipped = 0
    for path in frames:
        png = path.read_bytes()
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            skipped += 1
            continue
        state, box = classify(image)
        corpus.append((png, state, box))

    counts = Counter(state.key if state else "none" for _, state, _ in corpus)
    print("真值分布：" + "  ".join(f"{state.label if state else '非宝箱页'}({state.key if state else '-'})={counts[state.key if state else 'none']}"
                                   for state in STATES)
          + f"  非宝箱页={counts['none']}"
          + (f"  跳过={skipped}" if skipped else ""))
    print()

    exit_code = 0
    for name in template_names:
        own = TEMPLATE_STATE.get(name)
        matcher = ImageMatcher(TEMPLATE_DIR)
        single = ImageMatcher(TEMPLATE_DIR)
        single_profile = MatchProfile(scale_min=1.0, scale_max=1.0, scale_step=0.035)

        own_total = 0
        hit = miss = fp = off = 0
        scores: list[float] = []
        scales: Counter[float] = Counter()
        times_hit: list[float] = []
        times_miss: list[float] = []
        single_hit = 0

        for png, state, box in corpus:
            start = time.perf_counter()
            result = matcher.match(png, [name], THRESHOLD, profile)
            elapsed = (time.perf_counter() - start) * 1000

            is_own = own is not None and state is not None and state.key == own.key
            if is_own:
                own_total += 1

            if result is not None:
                times_hit.append(elapsed)
                scores.append(result.confidence)
                scales[result.scale] += 1
                if not is_own:
                    fp += 1  # 命中落在非自己状态的帧上：另一个宝箱状态 / 招募 / 战斗
                else:
                    hit += 1
                    center_x, center_y, width = box  # type: ignore[misc]
                    if (abs(result.center[0] - center_x) > POSITION_TOLERANCE_X
                            or abs(result.center[1] - center_y) > POSITION_TOLERANCE_Y):
                        off += 1
            else:
                times_miss.append(elapsed)
                if is_own:
                    miss += 1

            if single.match(png, [name], THRESHOLD, single_profile) is not None:
                single_hit += 1

        print(f"=== {name} ===")
        if own is None:
            print(f"  （未登记状态；只测误报，不测召回）")
        else:
            recall = (own_total - miss) / own_total * 100 if own_total else float("nan")
            print(f"  目标状态：{own.label}（真值 {own_total} 帧）")
            print(f"  命中 {hit} · 漏检 {miss} · 位置错 {off}")
            print(f"  召回 {recall:.1f}%")
        fp_rate = fp / max(len(corpus) - own_total, 1) * 100
        print(f"  误报 {fp}（非目标帧共 {len(corpus) - own_total} 帧）→ 误报率 {fp_rate:.2f}%")
        if scores:
            ordered = sorted(scores)
            print(f"  置信度 最低 {ordered[0]:.3f} / 5% {ordered[len(ordered) // 20]:.3f} / "
                  f"中位 {statistics.median(ordered):.3f}")
            print(f"  命中尺度 { {round(k, 3): v for k, v in sorted(scales.items())} }")
        for label, series in (("命中帧（热路径/冷启动）", times_hit), ("无目标帧（完整兜底）", times_miss)):
            if not series:
                continue
            ordered_t = sorted(series)
            print(f"  {label} 中位 {statistics.median(ordered_t):.1f} ms / "
                  f"p90 {ordered_t[int(len(ordered_t) * 0.9)]:.1f} / 最大 {ordered_t[-1]:.1f}  （{len(series)} 帧）")
        print(f"  单尺度(1.0) 对照：命中 {single_hit}（多尺度 {hit + fp}，多救回 {hit - single_hit} 帧）")

        bad = fp > 0 or miss > 0 or off > 0 or (own is not None and (own_total - miss) / max(own_total, 1) < 0.99)
        if bad:
            print("  ⚠️  未达预期（漏检 / 位置错 / 误报 应全为 0，召回应 ≥99%）")
            exit_code = 1
        print()

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="识别链路回归评测")
    parser.add_argument("--stride", type=int, default=3, help="抽样间隔，1 = 全量")
    parser.add_argument("--templates", nargs="*", default=list(TEMPLATE_STATE))
    args = parser.parse_args()
    return evaluate(args.templates, max(1, args.stride))


if __name__ == "__main__":
    raise SystemExit(main())
