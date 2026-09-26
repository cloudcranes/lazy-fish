"""PR-26 多 Runner 并发：单进程两 device 并行（mock adb），验证互不干扰 + 各自 state 独立。

覆盖任务清单：
  (1) RunnerRegistry：device_id → runner 查/建；同一设备复用同一 runner 实例；
  (2) TaskRunner 并发隔离：两设备同时 start，asyncio.create_task 并发跑、state 互不覆盖；
  (3) 注册表 snapshots() 按 device_id 分组，stop 支持指定 device_id。

OTel 噪声防护：runner 模块 import 时 load_tracer 会建 SDK provider（Console BSP）。
本模块跑真实任务会发 span，若不做处理，BSP worker 会在 pytest 捕获流关闭后
flush → 满屏 `ValueError: I/O operation on closed file`。autouse fixture 把
runner 的 _tracer 换成 NoOp，从源头消灭 span 产出。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyzw_auto_clicker.matcher import ImageMatcher
from xyzw_auto_clicker.models import TaskConfig
from xyzw_auto_clicker.runner import RunnerRegistry, TaskRunner

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(1) 模板必须真实存在（TaskConfig.validate 会查文件）；(2) 关掉 OTel span 噪声。"""
    import xyzw_auto_clicker.runner as runner_module
    import xyzw_auto_clicker.settings as settings_module

    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "btn.png").write_bytes(_TINY_PNG)
    monkeypatch.setattr(settings_module, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(runner_module, "TEMPLATE_DIR", template_dir)
    from opentelemetry.trace import NoOpTracerProvider

    monkeypatch.setattr(runner_module, "_tracer", NoOpTracerProvider().get_tracer("test"))


class _MockMatch:
    template_name = "btn.png"
    x = 0
    y = 0
    width = 10
    height = 10
    confidence = 0.99
    scale = 1.0


class _MockMatcher:
    """每台设备返回各自的命中计数：截图帧首字节 = 设备名首字符。"""

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}

    def match(self, screenshot: bytes, template_names, threshold, profile):
        key = chr(screenshot[0])
        self.hits[key] = self.hits.get(key, 0) + 1
        return _MockMatch()


class _MockAdb:
    def __init__(self) -> None:
        self.taps: dict[str, list[tuple[int, int]]] = {}

    async def screenshot_png(self, device_id=None):
        # 帧首字节 = 设备名首字符：让 matcher / state 能区分两台设备
        return (device_id or "?").encode()[:1] + _TINY_PNG

    async def tap(self, x: int, y: int, device_id=None):
        self.taps.setdefault(device_id or "", []).append((x, y))


def _config(device_id: str | None, click_count: int, post_wait: float = 0.0) -> TaskConfig:
    return TaskConfig(
        name="chest",
        template_names=["btn.png"],
        click_count=click_count,
        device_id=device_id,
        post_click_wait_seconds=post_wait,
        jitter_seconds=0.0,
        interval_seconds=0.1,
        repeat_tap_count=1,
    )


async def _wait_until(cond, timeout: float) -> None:
    """事件循环内轮询等待条件成立，超时抛错（避免 stop 与任务竞态）。"""
    waited = 0.0
    while waited < timeout:
        if cond():
            return
        await asyncio.sleep(0.05)
        waited += 0.05
    raise AssertionError(f"等待超时（{timeout}s）：{cond!r} 始终不成立")


def test_registry_reuses_same_runner_per_device() -> None:
    """同一 device_id 必须拿到同一 runner；不同 device_id 必须不同实例。"""
    registry = RunnerRegistry(_MockAdb(), _MockMatcher())
    r1 = registry.get_or_create("emulator-5554")
    r2 = registry.get_or_create("emulator-5554")
    r3 = registry.get_or_create("emulator-5556")
    assert r1 is r2
    assert r1 is not r3
    assert registry.get("emulator-5554") is r1
    assert registry.get("nonexistent") is None


def test_two_devices_run_concurrently_with_independent_state() -> None:
    """单进程两设备并行跑：各自截图/点击落到对应设备，state 各自独立不互相覆盖。"""
    adb = _MockAdb()
    registry = RunnerRegistry(adb, _MockMatcher())
    dev_a, dev_b = "emulator-5554", "emulator-5556"

    async def scenario() -> None:
        ra, rb = registry.get_or_create(dev_a), registry.get_or_create(dev_b)
        await asyncio.gather(ra.start(_config(dev_a, 3)), rb.start(_config(dev_b, 2)))
        await _wait_until(lambda: not ra.running() and not rb.running(), 15.0)

    asyncio.run(scenario())

    # 互不干扰：两设备分别点了 3 次 / 2 次，且都点在目标设备上
    assert len(adb.taps[dev_a]) == 3
    assert len(adb.taps[dev_b]) == 2
    # 各自 state 独立：进度按各自 target 走，device_id 归属正确
    sa = registry.get(dev_a).state
    sb = registry.get(dev_b).state
    assert sa.clicked == 3 and sa.target == 3
    assert sb.clicked == 2 and sb.target == 2
    assert sa.device_id == dev_a
    assert sb.device_id == dev_b
    assert sa is not sb
    # snapshots() 按 device_id 分组返回两份独立状态
    grouped = registry.snapshots()
    assert set(grouped) == {dev_a, dev_b}
    assert grouped[dev_a]["clicked"] == 3
    assert grouped[dev_b]["clicked"] == 2


def test_default_device_uses_separate_runner() -> None:
    """未绑定设备（device_id=None）的任务与指定设备任务不共享 runner/state。"""
    registry = RunnerRegistry(_MockAdb(), _MockMatcher())
    default_runner = registry.get_or_create(None)
    dev_runner = registry.get_or_create("emulator-5554")
    assert default_runner is not dev_runner
    assert registry.snapshots().keys() == {"", "emulator-5554"}


def test_stop_isolates_one_device() -> None:
    """stop 指定 device_id 只停该设备，另一设备继续不受影响。"""
    adb = _MockAdb()
    registry = RunnerRegistry(adb, _MockMatcher())
    dev_a, dev_b = "emulator-5554", "emulator-5556"

    async def scenario() -> None:
        ra, rb = registry.get_or_create(dev_a), registry.get_or_create(dev_b)
        # 每轮 0.2s：50 次 ≈ 10s+3s 倒计时，足够在跑完前完成 stop
        await asyncio.gather(
            ra.start(_config(dev_a, 50, post_wait=0.2)),
            rb.start(_config(dev_b, 50, post_wait=0.2)),
        )
        await _wait_until(lambda: ra.running() and rb.running(), 10.0)
        await ra.stop()
        sa = ra.state
        assert sa.status == "stopped", "被 stop 的设备必须停止"
        assert sa.clicked < 50
        # B 设备仍在运行（stop 只作用 A）
        assert rb.running(), "未 stop 的设备必须继续运行"
        await rb.stop()

    asyncio.run(scenario())
