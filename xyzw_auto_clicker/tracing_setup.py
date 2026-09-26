"""OTel traces 初始化。

本模块负责 SDK 的开关与全局 TracerProvider 单例。FastAPI 中间件 / runner /
matcher 都从这里拿 tracer，不再各自读 OTEL_SDK_DISABLED，避免真值表漂移。

设计要点：
- 进程级 TracerProvider 只创建一次（幂等）。OTEL_SDK_DISABLED=true 时不进 SDK，
  直接返回 NoOp tracer，所有 `start_as_current_span` 变成零开销的 context manager。
- service_name='lazy-fish' / service_version=os.environ.get('LAZY_FISH_VERSION','dev')，
  与 release-please 升 1.0 前评估里要求的 service.name / service.version 一致。
- 不引入 exporter：开发期靠 OTEL 默认 console exporter 兜底，生产环境由 collector
  通过 OTLP/HTTP 接收。ponytail: 接入 OTLP exporter 时只需在 configure_tracer 里
  多一行 BatchSpanSpanExporter(provider.add_span_processor(...))，无需改业务调用方。
"""
from __future__ import annotations

import os
import sys
import threading
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

_SERVICE_NAME = "lazy-fish"

_provider_lock = threading.Lock()
_provider: TracerProvider | None = None


def _stdout_alive() -> bool:
    """检测 stdout 是否仍可写：pytest 捕获模式或子进程退出后 stdout 可能已关闭，
    ConsoleSpanExporter 还在异步线程 flush 会写满屏 ValueError。
    ponytail: 探测开销可忽略，只在 configure_tracer 启动期调用一次。
    """
    stdout = sys.stdout
    if stdout is None:
        return False
    try:
        return not stdout.closed
    except (AttributeError, ValueError):
        return False


def configure_tracer(name: str = _SERVICE_NAME, service_version: str | None = None) -> Tracer:
    """获取进程级 Tracer，单例。

    OTEL_SDK_DISABLED=true → 返回 NoOp tracer，零开销；不进 SDK。
    其他情况：创建并注册一次 TracerProvider（带 ConsoleSpanExporter 兜底），
    后续调用直接返回同一个 provider 上的 tracer。

    ponytail: name 参数为 FastAPI middleware 留口，httpx/grpc 等客户端 instrumentation
    后续接入时可以直接拿同名 tracer 形成 trace 树；当前唯一调用方固定传 'lazy-fish'。
    """
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        # 直接从 NoOp provider 拿 tracer，不走全局：避免「SDK provider 已注册后
        # 临时切到 disabled」时仍然拿到 SDK tracer 的语义偏差。
        return trace.NoOpTracerProvider().get_tracer(name)
    version = service_version if service_version is not None else os.environ.get("LAZY_FISH_VERSION", "dev")
    global _provider
    with _provider_lock:
        if _provider is None:
            resource = Resource.create(
                {"service.name": _SERVICE_NAME, "service.version": version}
            )
            provider = TracerProvider(resource=resource)
            # 默认 console 兜底：开发期人工跑可看 span；生产期 collector 接走后，
            # 只需把 BatchSpanProcessor 换成 OTLPSpanExporter，不必再动业务调用方。
            # 仅当 stdout 还活着才挂 ConsoleSpanExporter（pytest 捕获 / 子进程退出时
            # stdout 可能已关闭，BatchSpanProcessor 后台 flush 会写满屏 ValueError）。
            if _stdout_alive():
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
            _provider = provider
    return trace.get_tracer(name)


def load_tracer(name: str = _SERVICE_NAME) -> Tracer:
    """FastAPI startup/shutdown 钩子调用的轻量别名。

    与 configure_tracer 等价；命名上让 startup hook 读起来更直白：
    `await load_tracer('lazy-fish')` 比 `configure_tracer()` 更像初始化动作。
    ponytail: 当前实现不返回 provider，未来若需要 on_shutdown 强制 flush span 缓冲，
    在这里拿 _provider 调 provider.force_flush() 即可，不影响其他调用方。
    """
    return configure_tracer(name)


def shutdown_tracer() -> None:
    """FastAPI shutdown 时主动 flush 缓冲的 span。

    ponytail: BatchSpanProcessor 默认 5s flush，长跑进程丢信号不至于丢 span；
    测试场景里进程立即退出 → 必须显式 flush，否则最后几秒的 span 全丢。
    与 startup 端 `load_tracer` 配对：startup 建 provider、shutdown flush。
    """
    if _provider is not None:
        _provider.force_flush()