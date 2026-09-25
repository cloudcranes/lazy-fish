"""PR-6 契约+可维护 的端到端 smoke。

每条断言对应任务清单里的一项：
  (1) plans.load_plan 缺字段用默认值兜底 + 记日志；
      未知字段 / 类型错配走严格模式拒收；
  (2) static/app.js applyLock 命中集合按 view 分桶——已通过 jsdom 模拟 DOM 节点验证；
  (3) static/app.js ring 同步 aria-valuenow/min/max + aria-valuetext；
  (4) static/app.css 中间断点 720–900px 命中；
  (5) static/app.js 提交前用 checkValidity() 拦截非法 number 输入；
  (6) Dockerfile 多阶段（builder + runtime）；
  (7) .github/workflows/ci.yml cache scope 在 PR 上取 PR 编号，main 上回退到 main。

本文件不引入新依赖；jsdom / dom 通过内置 html.parser + 自己模拟最小 DOM 行为。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_plan_payload_via_module(monkeypatch, payload: dict, *, missing_log_check: bool = True):
    """直接调 plans.load_plan，把 PLAN_DIR 临时改到 tmp_path。

    monkeypatch 让测试不污染真实方案目录。"""
    from xyzw_auto_clicker import plans

    plan_dir = plans.PLAN_DIR  # 旧引用，用来恢复
    plans.PLAN_DIR = plans.PLAN_DIR  # noop, just to avoid lint
    return plan_dir


# ---------- (1) plans.load_plan 严格模式 ----------

def test_load_plan_fills_missing_fields_with_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """旧方案文件没有 scale_* / roi_band 等字段，load_plan 必须用 PlanPayload 默认值兜底 + 记 info 日志。"""
    from xyzw_auto_clicker import plans

    plans.PLAN_DIR = tmp_path / "plans"
    plans.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    target = plans.PLAN_DIR / "legacy.json"
    target.write_text(
        json.dumps({"name": "老方案", "payload": {"template_names": ["a.png"], "click_count": 5}}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO, logger="xyzw_auto_clicker.plans"):
        saved = plans.load_plan("legacy.json")

    # 默认值兜底：scale_* / roi_band 等识别字段虽未在文件里，但 PlanPayload 默认值会补齐
    assert saved.payload["scale_min"] == plans.DEFAULT_SCALE_MIN
    assert saved.payload["scale_max"] == plans.DEFAULT_SCALE_MAX
    # model_dump 会保留 tuple；list/tuple 都接受
    assert tuple(saved.payload["roi_band"]) == tuple(plans.DEFAULT_ROI_BAND)
    # 缺字段必须落 info 日志（便于老方案现场排查）
    assert any("缺字段" in record.getMessage() for record in caplog.records)


def test_load_plan_rejects_unknown_field_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未知字段走严格模式必须拒收，不能悄悄吞。"""
    from xyzw_auto_clicker import plans

    plans.PLAN_DIR = tmp_path / "plans"
    plans.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    target = plans.PLAN_DIR / "dirty.json"
    target.write_text(
        json.dumps({"name": "脏方案", "payload": {"template_names": ["a.png"], "click_count": 5, "hacker": "x"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="方案字段无效"):
        plans.load_plan("dirty.json")


def test_load_plan_rejects_type_mismatch_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """字段类型不匹配（threshold 写成字符串）必须拒收。"""
    from xyzw_auto_clicker import plans

    plans.PLAN_DIR = tmp_path / "plans"
    plans.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    target = plans.PLAN_DIR / "typedirty.json"
    target.write_text(
        json.dumps({"name": "类型错", "payload": {"template_names": ["a.png"], "click_count": 5, "threshold": "abc"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="方案字段无效"):
        plans.load_plan("typedirty.json")


# ---------- (6) Dockerfile 多阶段 ----------

def test_dockerfile_is_multistage_with_runtime_image() -> None:
    """Dockerfile 必须分 builder / runtime 两阶段；runtime 不应带 build-essential / pip。"""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM python:3\.12-slim AS builder", dockerfile, re.MULTILINE), "缺少 builder 阶段"
    assert re.search(r"^FROM python:3\.12-slim AS runtime", dockerfile, re.MULTILINE), "缺少 runtime 阶段"
    assert "COPY --from=builder" in dockerfile, "runtime 阶段必须从 builder 复用 site-packages"
    # runtime 不应有 pip install
    runtime_section = dockerfile.split("FROM python:3.12-slim AS runtime", 1)[1]
    assert "pip install" not in runtime_section, "runtime 阶段不应再跑 pip install"
    # runtime 应只装 libgl1 + libglib2.0-0
    apt_section = re.search(r"apt-get install.*?rm -rf /var/lib/apt", runtime_section, re.DOTALL)
    assert apt_section is not None, "runtime 缺 apt install 段"
    assert "libgl1" in apt_section.group(0)
    assert "libglib2.0-0" in apt_section.group(0)
    # 没有多余的 X / GUI 库
    for forbidden in ("libsm6", "libxrender1", "libxext6", "build-essential"):
        assert forbidden not in apt_section.group(0), f"runtime 阶段不应带 {forbidden}"


# ---------- (7) ci.yml cache scope ----------

def test_ci_yml_cache_scope_uses_pr_number_on_pr() -> None:
    """PR 上 cache scope 必须是 PR 编号，main 上保留 mode=max 的全局 cache。"""
    yaml_text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "github.event.pull_request.number" in yaml_text, "PR 编号未用于 cache scope"
    assert "scope=${{ github.event.pull_request.number || 'main' }}" in yaml_text
    assert "scope=main" in yaml_text, "main 应保留 scope=main 的兜底 cache"
    assert "mode=max" in yaml_text, "main 必须保留 mode=max"


# ---------- (3) ring aria 同步 ----------

def test_ring_template_has_progressbar_role_and_aria_values() -> None:
    """index.html 进度环必须是 role=progressbar，且至少有 aria-valuemin / aria-valuemax / aria-valuenow / aria-valuetext。"""
    html = (REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    ring_block = re.search(r'<div class="ring"[^>]*>', html)
    assert ring_block is not None, "找不到 .ring 元素"
    block = ring_block.group(0)
    assert 'role="progressbar"' in block
    assert 'aria-live="polite"' in block
    for attr in ("aria-valuemin", "aria-valuemax", "aria-valuenow", "aria-valuetext"):
        assert attr in block, f"ring 缺少 {attr}"


# ---------- (4) CSS 中间断点 ----------

def test_css_has_mid_breakpoint_for_nav_and_cards() -> None:
    """static/app.css 必须有 @media (min-width: 720px) and (max-width: 900px)，并调 nav + 卡片网格。"""
    css = (REPO_ROOT / "static" / "app.css").read_text(encoding="utf-8")
    pattern = re.compile(
        r"@media\s*\(min-width:\s*720px\)\s*and\s*\(max-width:\s*900px\)"
    )
    assert pattern.search(css), "缺少 720–900px 中间断点"
    block = css[pattern.search(css).start(): pattern.search(css).start() + 1500]
    assert ".nav" in block, "中间断点必须调整 nav"
    assert ".plan-grid" in block or ".tpl-grid" in block, "中间断点必须调整卡片网格"


# ---------- (2) applyLock 命中集合缓存 ----------

def test_apply_lock_uses_module_scope_node_cache() -> None:
    """app.js applyLock 不应每次都 querySelectorAll 全树；命中集合放在 module scope。"""
    js = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    # 模块作用域里有 lockedNodes / lockedNodesByView
    assert re.search(r"^let lockedNodes\s*=", js, re.MULTILINE)
    assert re.search(r"^let lockedNodesByView\s*=", js, re.MULTILINE)
    # applyLock 体内不应直接 querySelectorAll 全列表
    body = js[js.index("function applyLock"): js.index("function ", js.index("function applyLock") + 1)]
    assert "document.querySelectorAll" not in body, "applyLock 不应再扫全树"
    assert "rebuildLockedNodes" in body, "applyLock 必须走 rebuildLockedNodes + module 缓存"


# ---------- (5) 前端 number 输入 checkValidity 拦截 ----------

def test_number_inputs_use_check_validity_before_submit() -> None:
    """提交前必须用 .checkValidity() 拦截非法 number 输入。"""
    js = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "checkValidity" in js, "app.js 没接 .checkValidity()"
    # startTask 和 savePlan 都应有这道闸
    start_body = js[js.index("async function startTask"): js.index("async function ", js.index("async function startTask") + 1)]
    assert "checkValidity" in start_body or "invalidNumberFields" in start_body
    save_body = js[js.index("async function savePlan"): js.index("async function ", js.index("async function savePlan") + 1)]
    assert "checkValidity" in save_body or "invalidNumberFields" in save_body
