from __future__ import annotations

import asyncio
import subprocess
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import IO

_POOL_MAX_SIZE = 5
_POOL_IDLE_SECONDS = 60.0


@dataclass(frozen=True)
class Device:
    id: str
    status: str


class AdbError(RuntimeError):
    """ADB 命令失败。code 字段给前端按类型分流（TIMEOUT / NOT_FOUND / NOT_AVAILABLE / FAILED）。"""

    def __init__(self, message: str, *, code: str = "FAILED", stderr: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.stderr = stderr


@dataclass
class _Slot:
    proc: subprocess.Popen[bytes]
    last_used: float


class _ProcPool:
    """按 device_id 复用 `adb shell` 长连接；LIFO 淘汰。"""

    def __init__(
        self,
        *,
        max_size: int = _POOL_MAX_SIZE,
        idle_seconds: float = _POOL_IDLE_SECONDS,
    ) -> None:
        self._max_size = max_size
        self._idle_seconds = idle_seconds
        self._slots: OrderedDict[str, _Slot] = OrderedDict()

    def get_or_create(self, device_id: str, adb_path: str) -> _Slot:
        slot = self._slots.get(device_id)
        if slot is not None and slot.proc.poll() is None:
            slot.last_used = time.monotonic()
            self._slots.move_to_end(device_id)
            return slot
        if slot is not None:  # 进程已死，清掉
            self._slots.pop(device_id, None)
        try:
            proc: subprocess.Popen[bytes] = subprocess.Popen(
                [adb_path, "-s", device_id, "shell"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise AdbError(
                f"无法启动 adb shell for {device_id}",
                code="NOT_AVAILABLE",
                stderr=str(exc),
            ) from exc
        slot = _Slot(proc=proc, last_used=time.monotonic())
        self._slots[device_id] = slot
        self._evict_idle()
        return slot

    def _evict_idle(self) -> None:
        if len(self._slots) <= self._max_size:
            return
        now = time.monotonic()
        for key in list(self._slots):
            slot = self._slots[key]
            if now - slot.last_used >= self._idle_seconds:
                self._kill_slot(key, slot)
                del self._slots[key]
                if len(self._slots) <= self._max_size:
                    return

    @staticmethod
    def _kill_slot(key: str, slot: _Slot) -> None:
        if slot.proc.poll() is None:
            slot.proc.kill()
            try:
                slot.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                slot.proc.terminate()

    def close(self) -> None:
        for key, slot in list(self._slots.items()):
            self._kill_slot(key, slot)
        self._slots.clear()

    def evict(self, device_id: str) -> None:
        """显式淘汰某台设备的 shell 长连接（BrokenPipeError / OSError 后调用）。

        ponytail: 调用方在跨模块位置，没有外部走 _slots 的需要时给一条 public 通道
        比 # noqa: SLF001 私有访问更稳——加新调用方不会再复制一份私有访问。
        """
        slot = self._slots.pop(device_id, None)
        if slot is not None:
            self._kill_slot(device_id, slot)


class AdbClient:
    def __init__(self, adb_path: str = "adb", timeout_seconds: float = 10) -> None:
        self.adb_path = adb_path
        self.timeout_seconds = timeout_seconds
        self._device_args_cache: dict[str | None, list[str]] = {None: []}
        self._pool = _ProcPool()

    async def _run(self, args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        def call() -> bytes:
            # 用 Popen + communicate(timeout=…) 取代 subprocess.run：
            # run 的 timeout=… 是「读 PIPE 阶段」的超时，进程本身不会被杀，
            # 卡死的 ADB 子进程会一直挂到下一次 shell。
            proc: subprocess.Popen[bytes] = subprocess.Popen(
                [self.adb_path, *args],
                stdin=subprocess.PIPE if input_bytes is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = proc.communicate(input=input_bytes, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                # 超时立刻杀进程 + 子进程，避免僵尸 ADB 拖垮下一轮
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                raise
            if proc.returncode != 0:
                err_text = stderr.decode("utf-8", errors="replace").strip()
                raise AdbError(err_text or "ADB 命令失败", code="FAILED", stderr=err_text or None)
            return stdout

        try:
            return await asyncio.to_thread(call)
        except subprocess.TimeoutExpired as exc:
            # 前端拿到 code="TIMEOUT" 就能识别"是命令卡了，不是设备挂了"，
            # 给出"重试 / 检查 USB 线"之类的提示，而不是把整条 stacktrace 甩给用户。
            raise AdbError("ADB 命令超时", code="TIMEOUT", stderr=str(exc)) from exc
        except FileNotFoundError as exc:
            raise AdbError(
                "未找到 adb，请把 adb 加入 PATH", code="NOT_FOUND", stderr=str(exc)
            ) from exc

    def _device_args(self, device_id: str | None) -> list[str]:
        # device_id 同一任务固定，缓存命中省一次 list 构造 + 拼接
        cached = self._device_args_cache.get(device_id)
        if cached is not None:
            return cached
        args = ["-s", device_id] if device_id else []
        self._device_args_cache[device_id] = args
        return args

    async def devices(self) -> list[Device]:
        output = (await self._run(["devices"])).decode("utf-8", errors="replace")
        devices: list[Device] = []
        for line in output.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                devices.append(Device(id=parts[0], status=parts[1]))
        return devices

    async def screenshot_png(self, device_id: str | None = None) -> bytes:
        return await self._run([*self._device_args(device_id), "exec-out", "screencap", "-p"])

    async def tap(self, x: int, y: int, device_id: str | None = None) -> None:
        if device_id is None:
            await self._run(["shell", "input", "tap", str(x), str(y)])
            return
        cmd = f"input tap {x} {y}\n".encode()

        def call() -> None:
            slot = self._pool.get_or_create(device_id, self.adb_path)
            stdin: IO[bytes] | None = slot.proc.stdin
            assert stdin is not None
            try:
                stdin.write(cmd)
                stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                # 池里的 shell 进程已死，淘汰后让上层重试 / 报 NOT_AVAILABLE
                self._pool.evict(device_id)
                raise AdbError(
                    f"adb shell 长连接不可用：{device_id}",
                    code="NOT_AVAILABLE",
                    stderr=str(exc),
                ) from exc

        await asyncio.to_thread(call)
