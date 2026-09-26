# Changelog

## [0.1.1](https://github.com/cloudcranes/lazy-fish/compare/xyzw-auto-clicker-v0.1.0...xyzw-auto-clicker-v0.1.1) (2026-09-26)


### Features

* **ci:** a11y job fail-fast on PR — merge threshold (PR-17) ([8c3eb8a](https://github.com/cloudcranes/lazy-fish/commit/8c3eb8a372a686256c0ef9adfc690ca5c3261b8e))
* **concurrency:** multi-runner per device (PR-26) ([6e189c1](https://github.com/cloudcranes/lazy-fish/commit/6e189c175f5b53ff049e18f08f27bcbbd7d7eb03))
* make STOP_FILE path configurable via LAZY_FISH_STOP_FILE env (PR-14) ([120ea09](https://github.com/cloudcranes/lazy-fish/commit/120ea09c8db58bc5a6f013cbf5ca7c2f8fbcdf5b))
* **metrics:** Prometheus text/plain; version=0.0.4 via Accept negotiation (PR-15) ([602cd64](https://github.com/cloudcranes/lazy-fish/commit/602cd64591afec09975ad906566d0eb8146f77d9))
* **observability:** OTel collector sidecar (PR-25) ([e750089](https://github.com/cloudcranes/lazy-fish/commit/e750089df3408522a9fb1b15059a318370138691))
* **observability:** OTel traces (PR-21) ([c99f1f9](https://github.com/cloudcranes/lazy-fish/commit/c99f1f90567ff2ac8325c06b9a8e97aa76492b28))
* **observability:** OTLP exporter 接入 (PR-24) ([8e54507](https://github.com/cloudcranes/lazy-fish/commit/8e5450757f18979d307bc48282d7dd2809db4693))
* PR-1 安全+契约 ([9b449ad](https://github.com/cloudcranes/lazy-fish/commit/9b449ad1367143b33692a565b457477f12d1676b))
* PR-10 axe-core 自动 a11y ([5df7c72](https://github.com/cloudcranes/lazy-fish/commit/5df7c7205f1d8531a05939ab5fcc5b310a633612))
* PR-11 release-please 自动发版 ([c99b6a9](https://github.com/cloudcranes/lazy-fish/commit/c99b6a957fa57a265438cf3db9ab6b5b954819e3))
* PR-13 release-please closure (Makefile release-dispatch + docs/RELEASING.md + dispatch short-SHA tag) ([16f8f47](https://github.com/cloudcranes/lazy-fish/commit/16f8f472f7be76c11b9df8868e7654c52ea0acae))
* PR-18 视觉回归（playwright 截图 + pixelmatch 0.1%） ([fe4791e](https://github.com/cloudcranes/lazy-fish/commit/fe4791e600ad4dd8a37eeec01cf87eee616e7d3e))
* PR-2 前端契约+a11y ([ce8e33c](https://github.com/cloudcranes/lazy-fish/commit/ce8e33c6ce3d331775daf363342ff0e08a1d87f5))
* PR-3 可观测性 (logging + /api/health + 容器健康检查) ([0f14aa9](https://github.com/cloudcranes/lazy-fish/commit/0f14aa94f9acd59449881723c4efe37a25399348))
* PR-4 部署收口 ([e7c10ed](https://github.com/cloudcranes/lazy-fish/commit/e7c10edfe20a39097d118f5b935f7ea8b6b0835c))
* PR-5 性能 ([59dd192](https://github.com/cloudcranes/lazy-fish/commit/59dd1920269232d4c0eae9f1d39f9bd42deb2b34))
* PR-6 契约+可维护 ([1a2f75a](https://github.com/cloudcranes/lazy-fish/commit/1a2f75ad9bb40939cddaf3fce4bfa0d703104d67))
* PR-7 静态检查+安全扫描 (track A) ([cc64a72](https://github.com/cloudcranes/lazy-fish/commit/cc64a7223b87c6294e9a8ac2692a9ea0362a15bf))
* PR-8 契约硬化 ([90ff13b](https://github.com/cloudcranes/lazy-fish/commit/90ff13b1cad67ecc11ed0f7a35bb9254faa9c91d))
* PR-9 adb 长连接池 (track B-engineer) ([9cfc2c8](https://github.com/cloudcranes/lazy-fish/commit/9cfc2c80e2a3cf0babf0d7c37853d26fa863bcce))


### Bug Fixes

* **a11y:** empty-state/tpl-thumb/plan-card-meta 等残余 text-tertiary 提对比度 ([e34c834](https://github.com/cloudcranes/lazy-fish/commit/e34c8346beec1c7c90dd20e9b2be0ec575e604cc))
* **a11y:** 回退 tablist/tab/tabpanel 三件套（nav 混有非 tab 子元素触发 axe critical），nav-brand-text span 提对比度 ([0d863ab](https://github.com/cloudcranes/lazy-fish/commit/0d863ab0e4cc5f0c85efc8c265a185ee79eed390))
* **a11y:** 文本类 tertiary 改 secondary 满足对比度 ([e961ce8](https://github.com/cloudcranes/lazy-fish/commit/e961ce8f890a52bd11eb28bda878d9bf16e979a2))
* **a11y:** 清空 color-contrast 违规（text-tertiary→secondary、btn-primary/tpl-flag 提底对比、brand-600 加深），移除 button aria-selected ([b595bc4](https://github.com/cloudcranes/lazy-fish/commit/b595bc4c026598fe4aff988a3b4b1b34e125bf4a))
* **ci:** a11y/visual 各自 build+run 容器，移除跨 job 共享端口的 docker-up ([f07ea71](https://github.com/cloudcranes/lazy-fish/commit/f07ea713d5cb81aade2d348198f531f91290f85d))
* **ci:** build-push-action 加 load: true，镜像进本地 daemon 供 docker run 使用 ([778b737](https://github.com/cloudcranes/lazy-fish/commit/778b7371bfef8a2250068a85aae169b964d27903))
* **ci:** re-export TaskRunner with noqa F401 (back-compat for tests) ([7a92439](https://github.com/cloudcranes/lazy-fish/commit/7a924398580572dadc9ec5d7c2d110e7b981653f))
* **ci:** restore TaskRunner export + satisfy ruff (E501 line lengths, F811 test shadowing) ([107f7f8](https://github.com/cloudcranes/lazy-fish/commit/107f7f8427b61715c03d7b9a813b579c5ef63ca3))
* **ci:** 容器异常退出时保留现场并 docker logs 抓崩溃原因 ([9c0ccb1](https://github.com/cloudcranes/lazy-fish/commit/9c0ccb14e4730a790a8b5aada996cef81ed98d2b))
* **ci:** 补 httpx dev 依赖（starlette testclient 需要） ([3a15272](https://github.com/cloudcranes/lazy-fish/commit/3a15272e7bfe6630f0f0b5881a1d236a656bfb04))
* Dockerfile 缺 COPY static/templates，容器启动即崩（StaticFiles 目录不存在） ([9894d0b](https://github.com/cloudcranes/lazy-fish/commit/9894d0b2168f568f0a221ec507aa217a87f6e045))
* **otel:** pytest 噪声收口 (PR-23) ([09276da](https://github.com/cloudcranes/lazy-fish/commit/09276da555a00dd9b05890b8971965141d8fd616))
* **release:** config 改 packages 格式，修复 release-please 不识别顶层键 ([a787ec3](https://github.com/cloudcranes/lazy-fish/commit/a787ec36a9a7dd8fe7516de10445663f0acc14e6))
* **ui-test:** networkidle 改 domcontentloaded，前端每秒轮询 /api/tasks/state 使 networkidle 永不达成 ([6de8676](https://github.com/cloudcranes/lazy-fish/commit/6de86768d03647ea47354a3878409dffc04416b2))
* 兼容旧单 runner 契约，pyright strict 清零 ([1a35ec0](https://github.com/cloudcranes/lazy-fish/commit/1a35ec0a7cd1a964e9e4a04df57fd11e8ec6372f))
