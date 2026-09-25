from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

"""结构化日志：默认 plain，只在显式 fmt="json" 或环境变量 LOG_JSON=1 时才走 JSON。

不引第三方依赖，输出格式稳定可控；stdout 单行，便于容器/日志聚合直接消费。
"""

_DEFAULT_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure(level: int | str = "INFO", fmt: str | None = None) -> None:
    """一次性配好根 logger；幂等，重复调用会按传入参数重新生效。

    - fmt: "json" 走 JSON 单行；"plain" 或 None 走标准 logging 格式；其他值抛 ValueError。
    - level: 同 logging.basicConfig，接受 int 或名字。
    """
    root = logging.getLogger()
    # 清掉既有 handler，避免重复输出（uvicorn、reload、pytest 都可能提前装过）
    for handler in list(root.handlers):
        root.removeHandler(handler)
    if fmt == "json" or (fmt is None and _env_json()):
        handler: logging.Handler = _JsonHandler()
    elif fmt in (None, "plain"):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_DEFAULT_FMT))
    else:
        raise ValueError(f"未知 fmt: {fmt!r}")
    root.addHandler(handler)
    root.setLevel(level)


def _env_json() -> bool:
    import os

    return os.environ.get("LOG_JSON", "").strip() in {"1", "true", "TRUE", "yes"}


class _JsonHandler(logging.StreamHandler):
    """每条记录一行 JSON：timestamp/level/logger/message，extra 字段平铺到顶层。"""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            # extra={...} 注入的字段
            for key, value in record.__dict__.items():
                if key in _STANDARD_RECORD_KEYS:
                    continue
                if key.startswith("_"):
                    continue
                try:
                    json.dumps(value)
                    payload[key] = value
                except TypeError:
                    payload[key] = repr(value)
            if record.exc_info:
                payload["exc_info"] = self.formatException(record.exc_info)
            self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.stream.flush()
        except Exception:  # noqa: BLE001  日志失败绝不能拖垮业务
            self.handleError(record)


# logging.LogRecord 的标准字段名；额外字段（即 extra 注入的）才需要进 JSON。
_STANDARD_RECORD_KEYS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
})