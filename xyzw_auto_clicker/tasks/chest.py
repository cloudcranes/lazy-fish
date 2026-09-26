from __future__ import annotations

from ..models import TaskConfig
from ..plans import PlanPayload


def build_chest_config(payload: PlanPayload) -> TaskConfig:
    """方案字段与 TaskConfig 字段一一对应，直接展开，避免 18 个参数逐个搬运。

    ponytail: PlanPayload 自带的 `crop` 字段只用于前端截图采样校验，不参与任务运行，
    所以这里要 pop 掉再展开，避免 TaskConfig 收到未知键。
    """
    data = payload.model_dump()
    data.pop("crop", None)
    return TaskConfig(name="chest", **data)
