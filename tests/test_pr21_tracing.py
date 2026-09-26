"""PR-21 OTel traces:
- configure_tracer 在 OTEL_SDK_DISABLED=true 时不进 SDK；
- span 手动构造验证；
- matcher.match span 记录 template_count / hit_template_name；
- FastAPI 中间件为每个请求建 span（http.method / http.route / http.status_code）。

设计：tracing_setup 模块持有 _provider 单例；各业务模块（app/runner/matcher）
在 import 时调一次 load_tracer 把 tracer 绑到模块级 _tracer 上。OTel 全局
TracerProvider set_tracer_provider 一次锁定，后续 set 会被静默拒绝。
所以测试不能依赖重置全局 provider，而是：
  1) OTEL_SDK_DISABLED=true 测试：直接走 NoOp 路径，无副作用。
  2) OTEL_SDK_DISABLED=false 测试：在现有 provider 上再挂一个 InMemoryExporter，
     然后重新拿一个走当前全局 provider 的 tracer 给业务模块用，业务模块的
     旧 _tracer 绑定不重置也无妨——只要 provider 还有效，span 就会被记录。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_configure_tracer_disabled_skips_sdk(monkeypatch):
    """OTEL_SDK_DISABLED=true 必须不进 SDK：拿到 NoOp tracer，即使全局已被设成 SDK。
    ponytail: 这一条契约保证「运行时 OTEL_SDK_DISABLED=true 可以临时关掉追踪」
    无需重启进程，不依赖 SDK provider 的全局单例状态。
    """
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    import xyzw_auto_clicker.tracing_setup as tracing_setup
    from xyzw_auto_clicker.tracing_setup import configure_tracer

    monkeypatch.setattr(tracing_setup, "_provider", None)
    tracer = configure_tracer("lazy-fish", service_version="test")
    assert tracing_setup._provider is None
    # 即使全局已被其他测试设置过 SDK provider，这里也必须返回 NoOp
    from opentelemetry.trace import NoOpTracer

    assert isinstance(tracer, NoOpTracer)
    with tracer.start_as_current_span("test.noop") as span:
        assert span.is_recording() is False


def test_configure_tracer_enabled_initializes_sdk_with_resource(monkeypatch):
    """OTEL_SDK_DISABLED != true 时创建 SDK provider 并带 service.name/version。
    重置 OTel 全局后 set_tracer_provider 才会真正生效。
    """
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("LAZY_FISH_VERSION", "9.9.9-test")
    import opentelemetry.trace as ot_trace
    import xyzw_auto_clicker.tracing_setup as tracing_setup
    from xyzw_auto_clicker.tracing_setup import configure_tracer

    monkeypatch.setattr(tracing_setup, "_provider", None)
    ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
    ot_trace._TRACER_PROVIDER = None
    try:
        configure_tracer("lazy-fish")
        provider = ot_trace.get_tracer_provider()
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

        assert isinstance(provider, SDKTracerProvider)
        assert tracing_setup._provider is provider
        attrs = dict(provider.resource.attributes)
        assert attrs["service.name"] == "lazy-fish"
        assert attrs["service.version"] == "9.9.9-test"
        # 幂等：再次 configure_tracer 不重新创建
        configure_tracer("lazy-fish")
        assert ot_trace.get_tracer_provider() is provider
    finally:
        ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
        ot_trace._TRACER_PROVIDER = None
        monkeypatch.setattr(tracing_setup, "_provider", None)


def test_manual_span_records_attributes(monkeypatch):
    """手动构造 span 验证 attributes 可读。"""
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    import opentelemetry.trace as ot_trace
    import xyzw_auto_clicker.tracing_setup as tracing_setup
    from xyzw_auto_clicker.tracing_setup import configure_tracer

    monkeypatch.setattr(tracing_setup, "_provider", None)
    ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
    ot_trace._TRACER_PROVIDER = None
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    try:
        configure_tracer("lazy-fish")
        exporter = InMemorySpanExporter()
        tracing_setup._provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = ot_trace.get_tracer("lazy-fish")
        with tracer.start_as_current_span(
            "http GET /api/health",
            attributes={"http.method": "GET", "http.route": "/api/health"},
        ) as span:
            span.set_attribute("http.status_code", 200)
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        finished = spans[0]
        assert finished.attributes["http.method"] == "GET"
        assert finished.attributes["http.route"] == "/api/health"
        assert finished.attributes["http.status_code"] == 200
    finally:
        ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
        ot_trace._TRACER_PROVIDER = None
        monkeypatch.setattr(tracing_setup, "_provider", None)


def _reset_global_and_get_provider(monkeypatch, service_version=None):
    """helper：清掉全局 + 模块级单例，重建一个干净的 SDK provider + InMemoryExporter。"""
    import opentelemetry.trace as ot_trace
    import xyzw_auto_clicker.tracing_setup as tracing_setup

    monkeypatch.setattr(tracing_setup, "_provider", None)
    ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
    ot_trace._TRACER_PROVIDER = None
    configure_tracer_kwargs = {"name": "lazy-fish"}
    if service_version is not None:
        configure_tracer_kwargs["service_version"] = service_version
    from xyzw_auto_clicker.tracing_setup import configure_tracer

    configure_tracer(**configure_tracer_kwargs)
    return tracing_setup, ot_trace


def test_matcher_match_span_records_template_count_and_hit(monkeypatch, tmp_path):
    """matcher.match span 必带 template_count；命中时多带 hit_template_name，未命中不带。
    matcher 模块的 _tracer 在 import 时已绑定，必须重新指向当前全局 provider 上的 tracer。
    """
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    tracing_setup, ot_trace = _reset_global_and_get_provider(monkeypatch)
    exporter = InMemorySpanExporter()
    tracing_setup._provider.add_span_processor(SimpleSpanProcessor(exporter))

    # 关键：matcher 模块的 _tracer 重新绑定到当前全局 provider 的 tracer
    import xyzw_auto_clicker.matcher as matcher_module
    monkeypatch.setattr(matcher_module, "_tracer", ot_trace.get_tracer("lazy-fish"))

    try:
        import cv2
        import numpy as np

        from xyzw_auto_clicker.matcher import MatchProfile

        template_dir = tmp_path / "templates"
        template_dir.mkdir(parents=True, exist_ok=True)
        # 模板：渐变色（中心红、外围蓝）—— 与纯色 / 噪点 / 全白都明确不一致
        template_img = np.zeros((20, 20, 3), dtype=np.uint8)
        template_img[5:15, 5:15] = (0, 0, 255)  # 中心红
        template_img[0:5, :] = (255, 0, 0)  # 顶蓝
        template_img[15:20, :] = (255, 0, 0)  # 底蓝
        template_img[:, 0:5] = (255, 0, 0)
        template_img[:, 15:20] = (255, 0, 0)
        cv2.imwrite(str(template_dir / "logo.png"), template_img)
        (template_dir / "broken.png").write_bytes(b"not-an-image")
        matcher = matcher_module.ImageMatcher(template_dir)
        # roi_band=None：跳过默认 ROI 让 40x40 截图能命中任意位置的模板
        profile = MatchProfile(roi_band=None)

        # 1) 全白截图：与彩色模板必未命中
        exporter.clear()
        white = np.full((40, 40, 3), 255, dtype=np.uint8)
        ok, white_buf = cv2.imencode(".png", white)
        assert ok
        result_miss = matcher.match(
            white_buf.tobytes(), ["logo.png", "broken.png"], threshold=0.95, profile=profile
        )
        assert result_miss is None, "全白 vs 彩色模板必须未命中"
        spans = exporter.get_finished_spans()
        matcher_spans = [s for s in spans if s.name == "matcher.match"]
        assert matcher_spans, "matcher.match span 未生成"
        miss = matcher_spans[-1]
        assert miss.attributes["template_count"] == 2
        assert "hit_template_name" not in miss.attributes

        # 2) 把同一张模板原图直接当截图：必命中
        exporter.clear()
        ok2, hit_buf = cv2.imencode(".png", template_img)
        assert ok2
        result = matcher.match(hit_buf.tobytes(), ["logo.png"], threshold=0.99, profile=profile)
        assert result is not None, "原图自比必命中"
        hit_spans = [s for s in exporter.get_finished_spans() if s.name == "matcher.match"]
        assert hit_spans, "命中时 matcher.match span 未生成"
        hit_span = hit_spans[-1]
        assert hit_span.attributes["template_count"] == 1
        assert hit_span.attributes["hit_template_name"] == "logo.png"
    finally:
        ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
        ot_trace._TRACER_PROVIDER = None
        monkeypatch.setattr(tracing_setup, "_provider", None)


def test_otlp_path_initializes_when_endpoint_set(monkeypatch):
    """PR-24: 设了 OTEL_EXPORTER_OTLP_ENDPOINT 时，provider 上挂的必须是 OTLPSpanExporter。

    pytest 默认无该 env → 走 ConsoleSpanExporter；本用例 monkeypatch 设上
    endpoint 后再调用 configure_tracer，校验 provider 的 BatchSpanProcessor
    里包的是 OTLPSpanExporter 实例。stdout 关闭时 console 路径会跳过，OTLP
    路径必须无这个约束——任何 endpoint 设值都挂 OTLP。
    """
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.example:4317")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    import opentelemetry.trace as ot_trace
    import xyzw_auto_clicker.tracing_setup as tracing_setup
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from xyzw_auto_clicker.tracing_setup import configure_tracer

    monkeypatch.setattr(tracing_setup, "_provider", None)
    ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
    ot_trace._TRACER_PROVIDER = None
    try:
        configure_tracer("lazy-fish")
        provider = tracing_setup._provider
        assert provider is not None
        processors = provider._active_span_processor._span_processors
        assert processors, "endpoint 设值但 provider 上未挂任何 span processor"
        # 至少一个 processor 的 exporter 是 OTLPSpanExporter
        exporters = [getattr(p, "span_exporter", None) for p in processors]
        assert any(isinstance(e, OTLPSpanExporter) for e in exporters), (
            f"endpoint 设值但未挂 OTLPSpanExporter; got {[type(e).__name__ for e in exporters]}"
        )
    finally:
        ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
        ot_trace._TRACER_PROVIDER = None
        monkeypatch.setattr(tracing_setup, "_provider", None)


def test_app_http_middleware_emits_span(monkeypatch, tmp_path):
    """FastAPI 中间件为每个请求建一条 span，attributes 含 method/route/status_code。
    app 模块的 _tracer 同理必须重新绑定到当前全局 provider。
    """
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    import opentelemetry.trace as ot_trace
    from fastapi.testclient import TestClient

    from xyzw_auto_clicker import app as app_module
    from xyzw_auto_clicker import plans as plans_module
    from xyzw_auto_clicker import settings as settings_module
    from xyzw_auto_clicker.adb import AdbClient
    from xyzw_auto_clicker.matcher import ImageMatcher
    from xyzw_auto_clicker.runner import TaskRunner
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    tracing_setup, fresh_trace = _reset_global_and_get_provider(monkeypatch)
    exporter = InMemorySpanExporter()
    tracing_setup._provider.add_span_processor(SimpleSpanProcessor(exporter))
    # 重新绑定 app 模块的 _tracer 到当前全局 provider
    monkeypatch.setattr(app_module, "_tracer", fresh_trace.get_tracer("lazy-fish"))

    class _StubAdb:
        async def devices(self):
            return []

        async def screenshot_png(self, device_id=None):
            return b""

    try:
        monkeypatch.setattr(AdbClient, "__init__", lambda self: None)
        monkeypatch.setattr(app_module, "adb", _StubAdb())
        monkeypatch.setattr(settings_module, "TEMPLATE_DIR", tmp_path / "templates")
        monkeypatch.setattr(settings_module, "SHOT_DIR", tmp_path / "screenshots")
        monkeypatch.setattr(plans_module, "PLAN_DIR", tmp_path / "plans")
        monkeypatch.setattr(settings_module, "TRASH_DIR", tmp_path / "plans-trash")
        settings_module.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        settings_module.SHOT_DIR.mkdir(parents=True, exist_ok=True)
        plans_module.PLAN_DIR.mkdir(parents=True, exist_ok=True)
        settings_module.TRASH_DIR.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module, "TEMPLATE_DIR", settings_module.TEMPLATE_DIR)
        monkeypatch.setattr(app_module, "SHOT_DIR", settings_module.SHOT_DIR)
        monkeypatch.setattr(app_module, "matcher", ImageMatcher(settings_module.TEMPLATE_DIR))
        monkeypatch.setattr(app_module, "runner", TaskRunner(app_module.adb, app_module.matcher))

        with TestClient(app_module.app) as client:
            response = client.get("/api/health")
        assert response.status_code == 200
        http_spans = [s for s in exporter.get_finished_spans() if s.name.startswith("http ")]
        assert http_spans, "FastAPI 中间件未产生 http span"
        last = http_spans[-1]
        assert last.attributes["http.method"] == "GET"
        assert last.attributes["http.route"] == "/api/health"
        assert last.attributes["http.status_code"] == 200
    finally:
        ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
        ot_trace._TRACER_PROVIDER = None
        monkeypatch.setattr(tracing_setup, "_provider", None)


@pytest.fixture(autouse=True)
def _otel_force_shutdown_after_test(monkeypatch):
    """每条用例跑完都强制收口 OTel provider，把 BSP 后台线程 join 掉，避免 pytest 关闭捕获流后
    BSP worker 还在异步写 ConsoleSpanExporter → stderr 满屏 I/O closed ValueError。

    关键点：模块级 `tracing_setup._provider` 不一定是当前活动的 provider —— 测试里
    `monkeypatch.setattr(tracing_setup, "_provider", None)` 后又重新 `configure_tracer()`，
    但全局 `ot_trace._TRACER_PROVIDER` 可能仍是旧对象。所以这里同时拿「模块 _provider」
    和「全局 tracer provider」来 shutdown。
    ponytail: 用 yield-before 形式实现 teardown；fixture 自身不需要 setup 动作。
    """
    yield
    from xyzw_auto_clicker import tracing_setup as _tracing_setup

    _tracing_setup.force_shutdown()


def test_shutdown_cleans_bsp(monkeypatch):
    """force_shutdown() 必须把 BatchSpanProcessor 的后台 worker thread 收掉。

    之前测试 stderr 的 `ValueError: I/O operation on closed file` 来自 BSP daemon
    在 pytest 捕获流关闭后还在异步 flush；shutdown() 通过 BatchProcessor.shutdown()
    把 _worker_thread.join() → 线程不再存活。
    """
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    import opentelemetry.trace as ot_trace
    import xyzw_auto_clicker.tracing_setup as tracing_setup
    from xyzw_auto_clicker.tracing_setup import configure_tracer, force_shutdown

    monkeypatch.setattr(tracing_setup, "_provider", None)
    ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
    ot_trace._TRACER_PROVIDER = None
    try:
        configure_tracer("lazy-fish")
        provider = tracing_setup._provider
        assert provider is not None
        # 触发 BSP 启动：起一条 span 并 end，让 worker thread 进入 running 状态
        tracer = ot_trace.get_tracer("lazy-fish")
        with tracer.start_as_current_span("warmup.bsp"):
            pass
        # 拿 BSP 的 BatchProcessor worker thread 句柄，断言它 alive
        bsp = provider._active_span_processor._span_processors[0]
        bp = bsp._batch_processor
        worker_thread = bp._worker_thread
        assert worker_thread.is_alive(), "BSP worker 应该在 flush span 后仍 alive"
        # 收口：force_shutdown 等价于 flush + shutdown，必须 join BSP worker
        force_shutdown()
        # shutdown() 后 provider 应被清空（避免下一次测试拿到被 shutdown 的旧 provider）
        assert tracing_setup._provider is None
        # BSP 的 worker thread 必须被 join → is_alive() == False
        assert not worker_thread.is_alive(), (
            "BSP worker thread 未被 join，仍在后台运行 → 后续测试 stderr 会爆 ValueError"
        )
    finally:
        ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
        ot_trace._TRACER_PROVIDER = None
        monkeypatch.setattr(tracing_setup, "_provider", None)