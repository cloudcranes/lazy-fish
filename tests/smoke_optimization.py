"""端到端 smoke：构造合成截图与模板，跑一遍 matcher 主路径，确认改动未破坏行为。

依赖 cv2/numpy；环境缺包时静默跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import cv2
    import numpy as np
except ImportError:
    print("SKIP: cv2/numpy not installed")
    sys.exit(0)

from xyzw_auto_clicker.matcher import ImageMatcher, MatchProfile, frame_delta, frame_fingerprint


def make_pair(seed: int, scale: float = 1.0) -> tuple[bytes, np.ndarray]:
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 255, (160, 90, 3), dtype=np.uint8)
    h, w = 24, 60
    template = image[40:40 + h, 10:10 + w].copy()
    if scale != 1.0:
        template = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes(), template


def main() -> None:
    tmp = Path(ROOT / "data" / "templates")
    tmp.mkdir(parents=True, exist_ok=True)
    png_bytes, template = make_pair(0)
    tpath = tmp / "t.png"
    cv2.imwrite(str(tpath), template)

    matcher = ImageMatcher(tmp)
    profile = MatchProfile()

    hit = matcher.match(png_bytes, ["t.png"], 0.9, profile)
    assert hit is not None, "同尺度应命中"
    assert hit.confidence > 0.99, hit
    print("ok exact match", hit.confidence, hit.center)

    png2, template2 = make_pair(1, scale=0.9)
    cv2.imwrite(str(tpath), template2)
    matcher.clear_cache()
    hit2 = matcher.match(png2, ["t.png"], 0.85, MatchProfile())
    assert hit2 is not None, "0.9 倍模板应命中"
    assert 0.8 <= hit2.scale <= 1.2
    print("ok scale match", hit2.scale, hit2.confidence)

    fp1 = frame_fingerprint(png_bytes)
    fp2 = frame_fingerprint(png_bytes)
    assert fp1 is fp2
    print("ok fingerprint cache")

    assert frame_delta(fp1, fp2) == 0.0
    fp3 = frame_fingerprint(png2)
    assert frame_delta(fp1, fp3) > 0.0
    print("ok frame_delta")

    cv2.imwrite(str(tpath), template)
    matcher.clear_cache()
    h1 = matcher.match(png_bytes, ["t.png"], 0.9, profile)
    h2 = matcher.match(png_bytes, ["t.png"], 0.9, profile)
    assert h1 is not None and h2 is not None
    assert h1.center == h2.center
    print("ok hot path repeat")

    print("ALL OK")


if __name__ == "__main__":
    main()