from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    id: str
    status: str


class AdbError(RuntimeError):
    pass


class AdbClient:
    def __init__(self, adb_path: str = "adb", timeout_seconds: float = 10) -> None:
        self.adb_path = adb_path
        self.timeout_seconds = timeout_seconds
        self._device_args_cache: dict[str | None, list[str]] = {None: []}

    async def _run(self, args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        def call() -> bytes:
            completed = subprocess.run(
                [self.adb_path, *args],
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.decode("utf-8", errors="replace").strip()
                raise AdbError(stderr or "ADB 命令失败")
            return completed.stdout

        try:
            return await asyncio.to_thread(call)
        except subprocess.TimeoutExpired as exc:
            raise AdbError("ADB 命令超时") from exc
        except FileNotFoundError as exc:
            raise AdbError("未找到 adb，请把 adb 加入 PATH") from exc

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
