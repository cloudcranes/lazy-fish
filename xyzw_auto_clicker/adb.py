from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    id: str
    status: str


class AdbError(RuntimeError):
    """ADB 命令失败。code 字段给前端按类型分流（TIMEOUT / NOT_FOUND / FAILED）。"""

    def __init__(self, message: str, *, code: str = "FAILED", stderr: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.stderr = stderr


class AdbClient:
    def __init__(self, adb_path: str = "adb", timeout_seconds: float = 10) -> None:
        self.adb_path = adb_path
        self.timeout_seconds = timeout_seconds
        self._device_args_cache: dict[str | None, list[str]] = {None: []}

    async def _run(self, args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        def call() -> bytes:
            # 用 Popen + communicate(timeout=…) 取代 subprocess.run：
            # run 的 timeout=… 是「读 PIPE 阶段」的超时，进程本身不会被杀，
            # 卡死的 ADB 子进程会一直挂到下一次 shell。
            proc = subprocess.Popen(
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
            raise AdbError("未找到 adb，请把 adb 加入 PATH", code="NOT_FOUND", stderr=str(exc)) from exc

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
        await self._run([*self._device_args(device_id), "shell", "input", "tap", str(x), str(y)])
