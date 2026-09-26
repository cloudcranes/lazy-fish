# lazy-fish developer shortcuts
# Use `make <target>` on macOS / Linux. On Windows PowerShell run `gmake <target>`
# or invoke the underlying commands directly.

PY        ?= python
PIP       ?= $(PY) -m pip
PORT      ?= 8765
COMPOSE   ?= docker compose
TAG       ?= dev

.PHONY: help install dev run test test-core eval lint smoke-import format clean build compose-up compose-down compose-build release

help:  ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

install:  ## create venv and install runtime deps
	$(PY) -m venv .venv
	.venv/bin/$(PIP) install -r requirements.txt

dev:  ## install runtime + dev (pytest, ruff, pyright, pip-audit) deps
	$(PIP) install -r requirements-dev.txt

run:  ## run WebUI locally on LAZY_FISH_PORT (default 8765)
	LAZY_FISH_PORT=$(PORT) $(PY) -m xyzw_auto_clicker

test:  ## run unit tests
	$(PY) -m pytest tests/test_core.py -x -q

eval:  ## recognition regression on real footage
	$(PY) tests/recognition_eval.py

lint:  ## ruff check . + pyright strict (xyzw_auto_clicker) against pyright-baseline.json
	ruff check .
	@$(PY) scripts/pyright_check.py

smoke-import:  ## import every module without side-effects (cheap smoke test)
	$(PY) -c "from xyzw_auto_clicker import matcher, runner, app, adb, models; print('imports OK')"

format:  ## run black on the package if installed; no-op otherwise
	@if $(PY) -c "import black" >/dev/null 2>&1; then \
	  $(PY) -m black xyzw_auto_clicker tests; \
	else \
	  echo "black not installed; pip install -r requirements-dev.txt to enable format target"; \
	fi

clean:  ## remove caches and venv
	rm -rf .venv .pytest_cache **/__pycache__ */__pycache__

build:  ## build local image as lazy-fish:dev
	docker build -t lazy-fish:$(TAG) .

compose-up:  ## compose up (pulls published image; override with COMPOSE_TAG)
	$(COMPOSE) up -d

compose-down:  ## compose down
	$(COMPOSE) down

compose-build:  ## build from local source via compose
	COMPOSE_TAG=$(TAG) $(COMPOSE) build

release:  ## cut a release: bump tag, push, let CI build + publish
	@if [ -z "$(VERSION)" ]; then echo "VERSION=x.y.z required (e.g. make release VERSION=0.1.0)" >&2; exit 1; fi
	git tag v$(VERSION)
	git push origin v$(VERSION)
	@echo "Release v$(VERSION) triggered: https://github.com/cloudcranes/lazy-fish/releases"
