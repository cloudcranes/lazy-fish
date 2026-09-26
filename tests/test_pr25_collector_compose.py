"""PR-25 OTel collector sidecar：docker-compose.yml / .otel/collector.yaml 结构契约。

设计：
- 纯 YAML 解析断言（不依赖 docker 二进制，CI/本地恒跑）；
- 若环境有 docker，追加 `docker compose config` 解析验证（无 docker 则 pytest.skip）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(rel: str) -> dict:
    with open(REPO_ROOT / rel, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{rel} 顶层必须是 mapping"
    return data


def test_compose_has_otel_collector_service():
    compose = _load_yaml("docker-compose.yml")
    services = compose["services"]
    assert "otel-collector" in services, "docker-compose.yml 缺 otel-collector 服务"
    svc = services["otel-collector"]
    assert svc["image"] == "otel/opentelemetry-collector-contrib:0.118.0"
    ports = svc["ports"]
    assert "4317:4317" in ports, "grpc 4317 端口缺失"
    assert "4318:4318" in ports, "http 4318 端口缺失"
    assert "8889:8889" in ports, "prometheus 8889 端口缺失"
    # lazy-fish 必须依赖 collector 且条件为 service_healthy
    lazy = services["lazy-fish"]
    assert lazy["depends_on"]["otel-collector"]["condition"] == "service_healthy"


def test_compose_lazy_fish_endpoint_points_at_collector():
    compose = _load_yaml("docker-compose.yml")
    env = compose["services"]["lazy-fish"]["environment"]
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4317"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "grpc"


def test_collector_yaml_parses_and_has_required_sections():
    cfg = _load_yaml(".otel/collector.yaml")
    receivers = cfg["receivers"]
    assert "otlp" in receivers
    protocols = receivers["otlp"]["protocols"]
    assert "grpc" in protocols and "http" in protocols
    exporters = cfg["exporters"]
    assert "debug" in exporters and "prometheus" in exporters
    pipelines = cfg["service"]["pipelines"]
    assert "traces" in pipelines and "metrics" in pipelines
    # traces pipeline 必须走 debug；metrics pipeline 必须含 prometheus
    assert "debug" in pipelines["traces"]["exporters"]
    assert "prometheus" in pipelines["metrics"]["exporters"]


def test_docker_compose_config_resolves():
    """有 docker 就跑 `docker compose config` 让 compose 官方解析器过一遍；无 docker 跳过。"""
    if shutil.which("docker") is None:
        pytest.skip("docker 不在 PATH，跳过 compose config 解析")
    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"docker compose config 失败:\n{result.stderr}"
    resolved = yaml.safe_load(result.stdout)
    assert "otel-collector" in resolved["services"]
    assert "otel-collector" in resolved["services"]["lazy-fish"]["depends_on"]
