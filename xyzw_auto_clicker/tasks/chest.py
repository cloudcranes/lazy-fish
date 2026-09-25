from __future__ import annotations

from ..models import TaskConfig
from ..plans import PlanPayload


def build_chest_config(payload: PlanPayload) -> TaskConfig:
    """方案字段与 TaskConfig 字段一一对应，直接展开，避免 18 个参数逐个搬运。"""
    return TaskConfig(name="chest", **payload.model_dump())
