"""OTel traces 初始化。

本模块负责 SDK 的开关与全局 TracerProvider 单例。FastAPI 中间件 / runner /
matcher 都从这里拿 tracer，不再各自读 OTEL_SDK_DISABLED，避免真值表漂移。

设计要点：
- 进程级 TracerProvider 只创建一次（幂等）。OTEL_SDK_DISABLED=true 时不进 SDK，
  直接返回 NoOp tracer，所有 `start_as_current_span` 变成零开销的 context manager。
- service_name='lazy-fish' / service_version=os.environ.get('LAZY_FISH_VERSION','dev')，
  与 release-please 升 1.0 前评估里要求的 service.name / service.version 一致。
- PR-24 接入 OTLP gRPC exporter：`OTEL_EXPORTER_OTLP_ENDPOINT` 设值时挂
  `BatchSpanProcessor(OTLPSpanExporter())`；未设值仍走 `ConsoleSpanExporter`
  兜底（pytest / 本地开发 / 无 collector 的 CI runner）。stdout 关闭时不挂
  ConsoleSpanExporter（pytest capture / 子进程退出后 BatchSpanProcessor 后台
  flush 会写满屏 ValueError）。ponytail: OTLP 协议默认 grpc
  （`OTEL_EXPORTER_OTLP_PROTOCOL=grpc`），与 `requirements.txt` 装的
  `opentelemetry-exporter-otlp==1.34.1`（含 proto.grpc.trace_exporter）一致；
  若日后切换 http/protobuf，需改 import 路径（`exporter.otlp.proto.http`）。
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
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


def _otlp_endpoint() -> str | None:
    """返回 OTLP gRPC endpoint，未设值时 None（让调用方走 console 兜底）。
    ponytail: 标准 env 名 `OTEL_EXPORTER_OTLP_ENDPOINT`（OTel SDK 全局约定）；
    `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 是 traces-only 专用，本项目统一用
    全局名即可。strip 后空串视为未设。
    """
    raw = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    return raw or None


def configure_tracer(name: str = _SERVICE_NAME, service_version: str | None = None) -> Tracer:
    """获取进程级 Tracer，单例。

    OTEL_SDK_DISABLED=true → 返回 NoOp tracer，零开销；不进 SDK。
    其他情况：创建并注册一次 TracerProvider，按以下优先级挂 exporter：
      1) `OTEL_EXPORTER_OTLP_ENDPOINT` 设值 → `BatchSpanProcessor(OTLPSpanExporter)`
         （生产期 collector 通过 gRPC OTLP 接收）；
      2) 未设值且 stdout 还活着 → `BatchSpanProcessor(ConsoleSpanExporter)`
         （pytest / 本地开发 / 无 collector 的 CI runner 兜底）；
      3) stdout 已闭（pytest capture / 子进程退出）→ 不挂 console，避免
         BSP 后台 flush 写满屏 `ValueError: I/O operation on closed file`。
    后续调用直接返回同一个 provider 上的 tracer。

    ponytail: name 参数为 FastAPI middleware 留口，httpx/grpc 等客户端 instrumentation
    后续接入时可以直接拿同名 tracer 形成 trace 树；当前唯一调用方固定传 'lazy-fish'。
    """
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        # 直接从 NoOp provider 拿 tracer，不走全局：避免「SDK provider 已注册后
        # 临时切到 disabled」时仍然拿到 SDK tracer 的语义偏差。
        return trace.NoOpTracerProvider().get_tracer(name)
    version = (
        service_version
        if service_version is not None
        else os.environ.get("LAZY_FISH_VERSION", "dev")
    )
    global _provider
    with _provider_lock:
        if _provider is None:
            resource = Resource.create(
                {"service.name": _SERVICE_NAME, "service.version": version}
            )
            provider = TracerProvider(resource=resource)
            otlp_endpoint = _otlp_endpoint()
            if otlp_endpoint:
                # 生产路径：把 span 发到 collector。endpoint 由 env 决定（默认
                # http://host.docker.internal:4317，详见 docker-compose.yml / Dockerfile）。
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            elif _stdout_alive():
                # 本地/pytest 路径：stdout 还活着就挂 console，stderr / 控制台能直接看 span。
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            # else: stdout 已闭且无 OTLP endpoint → 不挂任何 exporter（pytest 子进程退出场景）
            trace.set_tracer_provider(provider)
            _provider = provider
            _ensure_atexit_shutdown()
    return trace.get_tracer(name)


def load_tracer(name: str = _SERVICE_NAME) -> Tracer:
    """FastAPI startup/shutdown 钩子调用的轻量别名。

    与 configure_tracer 等价；命名上让 startup hook 读起来更直白：
    `await load_tracer('lazy-fish')` 比 `configure_tracer()` 更像初始化动作。
    ponytail: 当前实现不返回 provider，未来若需要 on_shutdown 强制 flush span 缓冲，
    在这里拿 _provider 调 provider.force_flush() 即可，不影响其他调用方。
    """
    return configure_tracer(name)


def shutdown_tracer(timeout_millis: int = 2000) -> None:
    """FastAPI shutdown 时主动 flush 缓冲的 span。

    ponytail: BatchSpanProcessor 默认 5s flush，长跑进程丢信号不至于丢 span；
    测试场景里进程立即退出 → 必须显式 flush，否则最后几秒的 span 全丢。
    与 startup 端 `load_tracer` 配对：startup 建 provider、shutdown flush。
    timeout_millis=2000：OTLP 收不到就丢（不阻塞 uvicorn 优雅退出）。
    """
    if _provider is not None:
        _provider.force_flush(timeout_millis=timeout_millis)


def force_shutdown() -> None:
    """测试 / 进程退出场景的强制收口：flush 后 shutdown，把 BSP 后台线程 join 掉。

    pytest 单测通常一个进程跑几十条用例；每条用例都触发 BatchSpanProcessor 起一个
    worker 线程去 flush span。如果只 force_flush 不 shutdown，BSP 的 daemon thread
    会一直活着 → 测试进程结束时 BSP 后台线程还在尝试写 ConsoleSpanExporter，
    而 pytest 已关闭捕获流，stderr 满屏 `ValueError: I/O operation on closed file`。
    加 shutdown() 等价于「等 BSP worker 把自己队列里所有 span flush 完，再 join 掉」。

    同时处理「模块 _provider 被 monkeypatch 替换后旧 provider 漏在全局」的情况：
    遍历 ot_trace 全局 tracer provider + 通过 gc 找仍活着的 BSP worker 句柄，统一
    shutdown/join。这是兜底：测试里 reset 全局后再 monkeypatch _provider，旧 provider
    的 BSP 线程没人引用但 daemon thread 仍在后台跑。

    ponytail: 长跑进程不要在 hot loop 里调它，只在 FastAPI lifespan shutdown 或
    测试 fixture teardown 调一次。
    """
    global _provider

    def _shutdown_provider(p: TracerProvider | None) -> None:
        if p is None:
            return
        try:
            p.force_flush(timeout_millis=2000)
        except Exception:
            pass
        try:
            p.shutdown()
        except Exception:
            pass

    if _provider is not None:
        _shutdown_provider(_provider)
        _provider = None
    # 兜底：全局 ot_trace 持有的 provider 若仍是 SDK，关掉其内部 BSP
    try:
        import opentelemetry.trace as ot_trace
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

        gp = ot_trace.get_tracer_provider()
        if isinstance(gp, SDKTracerProvider):
            _shutdown_provider(gp)
    except Exception:
        pass
    # 再兜底：gc 里搜 BatchProcessor 残留 worker thread（monkeypatch 已丢旧 provider
    # 但 daemon thread 仍活着的情况）
    try:
        import gc

        from opentelemetry.sdk._shared_internal import BatchProcessor

        for obj in gc.get_objects():
            if isinstance(obj, BatchProcessor):
                try:
                    # 探测私有 _shutdown 属性（pyright 会因受保护访问报错，用 getattr 绕开）
                    if not getattr(obj, "_shutdown", True):  # pyright: ignore[reportUnknownArgumentType]
                        obj.shutdown()
                except Exception:
                    pass
    except Exception:
        pass


_atexit_registered = False


def _ensure_atexit_shutdown() -> None:
    """模块加载完成后第一次创建 SDK provider 时注册 atexit finalizer。

    测试进程结束（pytest 捕获流关闭）→ atexit 触发 force_shutdown → BSP daemon
    worker 在 pytest 关闭 stdout 前 join 完，不再有 `Exception while exporting Span`
    噪音。这一条 finalizer 是兜底：业务进程走 FastAPI lifespan shutdown；测试进程
    走 atexit；二者至少一个能 join。
    ponytail: 注册一次就够，多次 register 会被 atexit 排队多次，反而可能阻塞。
    """
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(force_shutdown)
        _atexit_registered = True
