from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .adb import AdbClient
from .matcher import ImageMatcher, frame_delta, frame_fingerprint
from .models import TaskConfig
from .settings import SHOT_DIR, STOP_FILE, TEMPLATE_DIR  # noqa: F401


@dataclass
class RunnerState:
    status: str = "idle"
    clicked: int = 0
    target: int = 0
    misses: int = 0
    last_error: str | None = None
    last_match: dict[str, object] | None = None
    last_screenshot: Path | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=200))

    def snapshot(self) -> dict[str, object]:
        progress_percent = round((self.clicked / self.target) * 100, 1) if self.target else 0
        is_running = self.status in {"starting", "running"}
        # 截图落盘后只对外暴露文件名（路径细节是实现，不该出现在契约里）
        last_screenshot = self.last_screenshot.name if self.last_screenshot else None
        return {
            "status": self.status,
            "clicked": self.clicked,
            "target": self.target,
            "progress_percent": progress_percent,
            "is_running": is_running,
            "misses": self.misses,
            "last_error": self.last_error,
            "last_match": self.last_match,
            "last_screenshot": last_screenshot,
            "logs": list(self.logs),
        }


class TaskRunner:
    def __init__(self, adb: AdbClient, matcher: ImageMatcher) -> None:
        self.adb = adb
        self.matcher = matcher
        self.state = RunnerState()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # 上一张已确认静止的画面指纹，作为下一轮"是否静止"的基准
        self._stable_frame: np.ndarray | None = None

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, config: TaskConfig) -> None:
        if self.running():
            raise RuntimeError("已有任务运行中")
        config.validate(TEMPLATE_DIR)
        self._stop_event.clear()
        self._stable_frame = None
        self.state = RunnerState(status="starting", target=config.click_count)
        self._task = asyncio.create_task(self._run(config))

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task

    async def _run(self, config: TaskConfig) -> None:
        try:
            for second in range(3, 0, -1):
                self._log(f"{second} 秒后开始，请确认游戏停在宝箱活动页")
                await asyncio.sleep(1)
                if self._should_stop():
                    self.state.status = "stopped"
                    return
            self.state.status = "running"
            state = self.state
            adb = self.adb
            device_id = config.device_id
            interval = config.interval_seconds
            post_wait = config.post_click_wait_seconds
            jitter = config.jitter_seconds
            max_misses = config.max_misses
            target = config.click_count
            threshold = config.threshold
            profile = config.match_profile()
            while state.clicked < target:
                if self._should_stop():
                    state.status = "stopped"
                    self._log("收到停止信号")
                    return
                screenshot, dropped = await self._capture_stable_frame(config)
                if dropped:
                    self._log(f"画面未静止，丢弃 {dropped} 帧后取用")
                # 落盘后只把路径留给 API/前端，避免状态对象长期持有大字节数组
                state.last_screenshot = self._write_latest_screenshot(screenshot)
                click_index = state.clicked
                template_names = config.first_template_names if (click_index == 0 and config.first_template_names) else config.template_names
                match = self.matcher.match(screenshot, template_names, threshold, profile)
                if match is None:
                    state.misses += 1
                    self._log(f"未匹配到模板，连续失败 {state.misses}/{max_misses}")
                    self._save_debug_screenshot(screenshot, tag="miss")
                    if state.misses >= max_misses:
                        state.status = "paused"
                        state.last_error = "连续匹配失败，任务已暂停，避免乱点"
                        self._log(state.last_error)
                        return
                    await asyncio.sleep(interval)
                    continue
                x, y = match.x + match.width // 2, match.y + match.height // 2
                tap_count = 1 if (click_index == 0 and config.first_template_names) else config.repeat_tap_count
                for tap_index in range(tap_count):
                    await adb.tap(x, y, device_id)
                    if tap_index + 1 < tap_count:
                        await asyncio.sleep(config.repeat_tap_gap_seconds)
                state.clicked += 1
                state.misses = 0
                state.last_match = {
                    "template_name": match.template_name,
                    "x": match.x,
                    "y": match.y,
                    "width": match.width,
                    "height": match.height,
                    "confidence": match.confidence,
                    "scale": match.scale,
                    "center": [x, y],
                    "tap_count": tap_count,
                }
                self._log(
                    f"点击 {state.clicked}/{state.target}: {match.template_name} ({x}, {y}) "
                    f"conf={match.confidence:.3f} scale={match.scale:.3f} taps={tap_count}"
                )
                await asyncio.sleep(post_wait + random.uniform(0, jitter))
            state.status = "done"
            self._log("点击次数完成")
        except asyncio.CancelledError:
            state.status = "stopped"
            raise
        except Exception as exc:
            state.status = "error"
            state.last_error = str(exc)
            self._log(f"任务异常: {exc}")

    async def _capture_stable_frame(self, config: TaskConfig) -> tuple[bytes, int]:
        """截图并确认画面已静止，返回 (采用的截图, 被丢弃的帧数)。

        实测"截在动画中间"的帧匹配得分只有 0.318，会白白累计 misses。这里拿上一轮
        已确认静止的画面做基准，所以正常轮次只截一张、零额外延迟；只有画面确实在变化
        时才丢弃重截，最多 freeze_max_waits 次后放行，避免动画过长把任务卡死。
        """
        adb = self.adb
        device_id = config.device_id
        data = await adb.screenshot_png(device_id)
        if not config.freeze_guard:
            return data, 0
        fingerprint = frame_fingerprint(data)
        if fingerprint is None:
            return data, 0
        stable = self._stable_frame
        dropped = 0
        while (
            stable is not None
            and dropped < config.freeze_max_waits
            and not self._should_stop()
            and frame_delta(stable, fingerprint) > config.freeze_threshold
        ):
            dropped += 1
            await asyncio.sleep(config.interval_seconds)
            data = await adb.screenshot_png(device_id)
            fingerprint = frame_fingerprint(data)
            if fingerprint is None:
                return data, dropped
        if dropped >= config.freeze_max_waits:
            self._log(f"画面持续变化，已等待 {dropped} 轮")
        self._stable_frame = fingerprint
        return data, dropped

    def _should_stop(self) -> bool:
        return self._stop_event.is_set() or STOP_FILE.exists()

    def _save_debug_screenshot(self, data: bytes, tag: str = "debug") -> Path | None:
        """只在异常/未匹配等需要排查时落盘，正常轮次不写盘。"""
        try:
            SHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = SHOT_DIR / f"{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.png"
            path.write_bytes(data)
            return path
        except OSError:
            return None

    def _write_latest_screenshot(self, data: bytes) -> Path | None:
        """把最近一次稳定帧覆盖写到 SHOT_DIR/last-frame.png，返回写入路径。

        用固定文件名而非带时间戳：前端只要在 filename 变化时才换图，覆盖写
        可以让 /api/screenshots/latest.png 永远拿到「最新一帧」，同时
        snapshot() 暴露的 last_screenshot 也只会是 last-frame.png 一个值。
        """
        try:
            SHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = SHOT_DIR / "last-frame.png"
            path.write_bytes(data)
            return path
        except OSError:
            return None

    def _log(self, message: str) -> None:
        self.state.logs.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
