# 发布流程（release-please 闭环）

> 同步落档：本流程与 `docs/OPTIMIZATION-ROADMAP.md` §3 表里「release changelog 由 release-please 接管」一致；本文件给出落地步骤与紧急路径。
> 状态：阶段 3 wave4 / PR-13 落地后启用。

## 1. 概览

```
本地 commit (Conventional) ──push main──▶ release-please 打开/更新 Release PR
                                          │
                                          └──merge──▶ release-please 自动打 tag (v*.*.*)
                                                      │
                                                      └──push tag──▶ release.yml
                                                                     │
                                                                     ├─ 构建多架构镜像
                                                                     ├─ 推 ghcr.io/cloudcranes/lazy-fish
                                                                     └─ 创建 GitHub Release
```

**单一事实源**：版本号由 release-please 根据 commit 历史推导，写入 `.release-please-manifest.json` 与 `release-please-config.json`；不要手动改这两个文件，也不要手动 `git tag`。

## 2. 日常发布（开发者）

### 2.1 写符合规范的 commit

按 [Conventional Commits](https://www.conventionalcommits.org/)：

| 前缀 | 影响（≥ 1.0） | 影响（< 1.0，当前） |
|---|---|---|
| `feat:` | minor 升级 | minor 升级 |
| `fix:` | patch 升级 | patch 升级 |
| `feat!:` / `BREAKING CHANGE:` 脚注 | major 升级 | minor 升级（`bump-minor-pre-major=true`） |
| `chore:` / `docs:` / `refactor:` / `perf:` / `test:` / `build:` / `ci:` | 不升级 | 不升级 |

示例：

```bash
git checkout main
git pull
git checkout -b feat/my-change
# ...改代码...
git commit -m "feat(matcher): add LRU eviction for template cache"
git push -u origin feat/my-change
# 开 PR → CI 全绿 → merge 到 main
```

### 2.2 release-please 自动开 PR

push 到 main 后，`.github/workflows/release-please.yml` 在 1 分钟内会：

1. 扫描 `feat:` / `fix:` / `feat!:` commits；
2. 计算下一个版本（从 `.release-please-manifest.json` 当前 `0.1.0` 起步）；
3. 生成 changelog；
4. 打开 / 更新一个标题为 `chore(main): release 0.2.0` 的 PR。

### 2.3 合 Release PR → 自动 tag

1. 复核 PR 的 `CHANGELOG.md` 与版本号；
2. 在 PR 上点 **Squash and merge**；
3. release-please 在 merge commit 上自动打 tag 并推送，例如 `v0.2.0`。

### 2.4 tag push → 自动构建发布

`.github/workflows/release.yml` 监听 `v*.*.*` tag：

1. `Extract version` 取 `${GITHUB_REF_NAME#v}`（如 `0.2.0`）；
2. QEMU + Buildx 起 `linux/amd64,linux/arm64` 双架构；
3. 推送到 `ghcr.io/cloudcranes/lazy-fish:0.2.0` 与 `:latest`；
4. `softprops/action-gh-release@v2` 创建 GitHub Release，含 docker run 片段与 `LICENSE` 文件。

无需任何人工干预。验证：

```bash
docker pull ghcr.io/cloudcranes/lazy-fish:0.2.0
docker run --rm -p 8999:8999 -v "$PWD/data:/app/data" ghcr.io/cloudcranes/lazy-fish:0.2.0
```

## 3. 手动触发发布（应急路径 / 解闸）

> 仅在 release-please 失灵、或需要为某个版本单跑一次发布时使用。
> PR-16（wave5）起，dispatch 路径已**解闸**：可以独立完成构建 → 推 GHCR → 创建 GitHub Release，不再被 `event_name == 'push'` 闸拦住。

### 3.0 OTel 部署

> PR-24 起，应用镜像通过 gRPC OTLP（默认 endpoint `http://host.docker.internal:4317`，协议 `grpc`）把 trace 发到宿主机 OTel Collector；Collector 再按需转发到 Jaeger / Tempo / 商业 SaaS。env 未设值时退回 `ConsoleSpanExporter` 兜底（pytest / 本地 dev / 无 Collector 的 CI runner），`OTEL_SDK_DISABLED=true` 时直接走 NoOp。详细拓扑 + 镜像变体矩阵见 `docs/OPTIMIZATION-ROADMAP.md §8.10.1「可观测拓扑」`。

### 3.1 通过 GitHub UI

1. 打开 Actions → **Release** workflow → **Run workflow**；
2. 选择 `main` 分支；
3. `version` 下拉选择一个版本（`v0.1.0` ~ `v2.0.0`），例如 `v0.2.0`；
4. 点 **Run workflow**。

> ⚠️ 1.0+ 手工 dispatch 拼档位列表：升 `v1.x` 需手动把新选项加进 `.github/workflows/release.yml` 的 `on.workflow_dispatch.inputs.version.options`（PR-19 已扩到 `v0.1.0..v2.0.0`，21 档），否则 GitHub UI 下拉里不会出现新版本。

`Extract version` 在 `workflow_dispatch` 路径下从 `inputs.version` 取版本号（自动去掉可选的 `v` 前缀），下游：

| 步骤 | dispatch 行为 |
|---|---|
| Login to GHCR | ✅ 执行 |
| Build & push image | ✅ 推到 `ghcr.io/cloudcranes/lazy-fish:<version>` 与 `:latest`（`provenance: false`） |
| Create GitHub release | ✅ 创建 Release，tag_name = `v<version>`（`generate_release_notes` 仅 tag push 启用） |

⚠️ 注意：dispatch 不需要预先打 `v*.*.*` tag，也不会替你打 tag——如果希望这次发布的版本继续被 `release-please` 管理，请在合并下一批 Conventional Commits 后由 release-please 接管。

### 3.2 通过 Makefile（DEPRECATED 但可用）

```bash
make release-dispatch                     # 使用默认 VERSION=v0.1.0
make release-dispatch VERSION=v0.2.0      # 指定版本
```

底层调用 `gh workflow run release.yml --ref main -f version=<VERSION>`。要求 `gh` 已 `auth login` 且对仓库有 `workflow` 权限。

**DEPRECATED**：输出会明确提示改用 release-please PR；仅作为逃生口保留。

### 3.3 dispatch 与 tag push 的对比

| 维度 | tag push | workflow_dispatch |
|---|---|---|
| 触发 | 推送 `v*.*.*` tag | GitHub UI / `gh workflow run` |
| VERSION 来源 | `${GITHUB_REF_NAME#v}` | `inputs.version`（去 `v` 前缀） |
| 推 GHCR | ✅ | ✅ |
| 创建 GH Release | ✅ + 自动 release notes | ✅ + 手写 docker 片段（无 release notes） |
| 打 git tag | 由 release-please 在 PR merge 时打 | 不打 |
| 推荐用途 | 正式发布 | 应急重发、CI 验证、调试镜像 |

## 4. 不要做的事

- ❌ 手动 `git tag v0.2.0 && git push origin v0.2.0`（绕过 changelog PR）；
- ❌ 手动编辑 `.release-please-manifest.json` 跳版本；
- ❌ 在 release PR 上点 **Rebase and merge**（release-please 需要 squash merge 来识别）；
- ❌ 改 `release.yml` 的 `Extract version` 让 dispatch 走 `${GITHUB_REF_NAME#v}`（会变成 `VERSION=main`，污染 GHCR tag）。当前 PR-16 已用 `inputs.version` 解闸。

## 5. 排障

| 现象 | 排查 |
|---|---|
| release-please 没开 PR | 检查 `.github/workflows/release-please.yml` 是否启用、push 是否触发 `main` 分支、commit 是否带 `feat:` / `fix:` 前缀 |
| Release PR 卡在 merge | 确认 CI 全绿；`a11y` job 自 **PR-17** 起已是合并门槛（PR + main 均 fail-fast），出现 `serious` / `critical` 即阻止 squash merge；其它可能的 `pull_request` 权限问题再单独处理，必要时给 `GITHUB_TOKEN` 加 `contents: write, pull-requests: write` |
| tag 推上去但 release.yml 没跑 | `.github/workflows/release.yml` 的 `on.push.tags` 是 `v*.*.*`，确认 tag 名格式 |
| workflow_dispatch 跑出来的镜像 tag 是 `main` 或 `GITHUB_SHA` | 这是老行为；当前 PR-16 已用 `inputs.version` 解闸，确保用的是最新 `release.yml`（`on.workflow_dispatch.inputs.version` 必须存在） |
| GHCR 镜像看不到 | 检查 repo `Settings → Packages → Visibility`，新 repo 默认是 private |

> **a11y 合并门槛（PR-17 起生效）**：PR-17 摘除了 `a11y` job 的 `continue-on-error: ${{ github.event_name == 'pull_request' }}`，Release PR 也会被阻塞；调试 release-please PR 时若 a11y 失败，优先定位 axe 报告的 serious/critical（moderate/minor 不阻塞），不要回退 `continue-on-error`。退出码语义 0/1/2 与 `tests/ui/a11y.mjs` 顶部注释一致。

> **visual 合并门槛（PR-20 起生效）**：PR-20 摘除了 `visual` job 的 `continue-on-error: ${{ github.event_name == 'pull_request' }}`，与 a11y 同语义——PR + main 均 fail-fast，Release PR 也会被阻塞。调试 release-please PR 时若 visual 失败，优先核对 `tests/ui/diffs/*.diff.png` artifact 是否为预期样式改动；预期改动须走「带新 baseline 的 PR」而非「直推 main 重生成」。退出码语义 0/1/2 与 `tests/ui/visual.mjs` 顶部注释一致；baseline 已锁 playwright chromium 1.49.x 主线版（见 `docs/UI-DESIGN.md` §12.6），不要回退 `continue-on-error`。

## 6. 关联文件

| 文件 | 角色 |
|---|---|
| `.github/workflows/release-please.yml` | 监听 main，open/update Release PR |
| `.github/workflows/release.yml` | 监听 `v*.*.*` tag，构建+发布 |
| `release-please-config.json` | release-please 配置（`releaseType=python`、`bump-minor-pre-major=true`） |
| `.release-please-manifest.json` | 当前版本（不要手动改） |
| `Makefile`（`release-dispatch`） | DEPRECATED 应急入口 |
| `README.md` / `README.zh-CN.md` | 面向用户的发布说明 |
| `docs/OPTIMIZATION-ROADMAP.md` §3 | 触发条件表（release-please 已激活） |

## 6. 升 1.0 前 checklist（PR-22 评估）

> 触发条件：`docs/OPTIMIZATION-ROADMAP.md` §3 表「release-please 升 1.0 评估」触发项；本节是 PR-22 落地时的快照，下一次手动升 1.0 之前请逐条复核。
> 升 1.0 一旦发生：`release-please-config.json` 的 `bump-minor-pre-major: true` / `bump-patch-for-minor-pre-major: true` 自动 fall through 到 release-please 默认 major 规则（PR-19 已确认），且 §1 表里的 `feat!:` / `BREAKING CHANGE` 行从「minor 升级」变回「major 升级」，所有下游文档（README、CHANGELOG 标题、GitHub Release）将出现首次反映 + 在 title/notes 出现「1.0.0」字样。
> 因此升 1.0 之前**必须**确认下面 6 条都已收口，否则就是「赌下游兼容性」。

| # | 项 | 谁负责 | 复核路径 |
|---|---|---|---|
| 1 | **API 稳定**：所有 `/api/*` 路由的请求 / 响应字段名与类型不再变动；新功能只能新增路由 / 新增字段，不能改既有字段语义 | FS | `git grep -nE "^(async )?def (get\|post\|put\|delete)" xyzw_auto_clicker/app.py` + `tests/test_pr8_contract.py` + `tests/test_pr15_metrics.py` 全绿 |
| 2 | **字段冻结**：`xyzw_auto_clicker/models.py` 所有 Pydantic model 不再删字段、不再改类型；新增字段须走次版本（< 1.0 走 minor，≥ 1.0 走 minor 因 `bump-minor-pre-major=true` 已失效，需提前评估是否切 major） | BE | `git diff v0.x.0 -- xyzw_auto_clicker/models.py` 应为空（升 1.0 当下）；`tests/test_pr6_contract.py` 全绿 |
| 3 | **模板系统固化**：`xyzw_auto_clicker/matcher.py` 模板 schema（`_TemplateEntry` 字段集）冻结；hot path TTL / LRU / `_roi_rect_cache` 参数已是常驻基础设施（PR-8 落地） | BE | `docs/OPTIMIZATION-ROADMAP.md` §8.3 PR-8 行确认 `HOT_PATH_TTL_SECONDS=60` / `ROI_CACHE_MAXSIZE=128` 已沉淀；`tests/test_pr8_contract.py` 16 例全绿 |
| 4 | **计划 schema stable**：`plans.PlanPayload` / `CropRequest`（`safe_field_name` + `CROP_MAX_DIM` + `CROP_MAX_AREA`，PR-8 + PR-12 落地）已无破坏性变更；保存的 `.json` 计划文件可被新版本读回且行为一致 | BE | `tests/test_pr6_contract.py` + `tests/test_pr8_contract.py` 全绿；手动 `python -c "import json; PlanPayload.model_validate(json.load(open('data/plans/sample.json')))"` 不抛 |
| 5 | **部署文档完备**：`README.md` / `README.zh-CN.md` / `docs/RELEASING.md` 三处版本说明一致；`docs/UI-DESIGN.md` §11 / §12 与 `docs/RELEASING.md` §5 双 a11y + visual 合并门槛注脚已锁定（PR-17 / PR-20 已落） | OPS + FE | `grep -nE "v1\\.0\\.0\|1\\.0" README.md README.zh-CN.md docs/RELEASING.md docs/UI-DESIGN.md` 不出现「1.0 + 行为改动」字样（仅版本号本身）；`docs/RELEASING.md` §5 a11y + visual 脚注齐全 |
| 6 | **breaking change 梳理**：从 `v0.1.0` 到当前 head 的所有 `feat!:` / `BREAKING CHANGE:` commits 已归并到 CHANGELOG.md「Breaking Changes」节，且每个都注明「影响面 / 迁移路径」 | FS + BE | `git log --grep="BREAKING CHANGE\\|feat!" --oneline v0.1.0..HEAD` 列出的 commit 数 == CHANGELOG.md「Breaking Changes」条目数；`docs/OPTIMIZATION-ROADMAP.md` §6 风险登记里的「PR-1 后端改 `last_screenshot` 字段名」之类条目已勾掉 |

**升 1.0 操作步骤**（checklist 全绿后才能动）：

1. **关闭 minor-pre-major 闸**（`release-please-config.json`）：
   ```jsonc
   {
     "releaseType": "python",
     "packageName": "lazy-fish",
     "bump-minor-pre-major": false,         // 由 true 改 false
     "bump-patch-for-minor-pre-major": false // 由 true 改 false
   }
   ```
   > ⚠️ 升 1.0 当天**不要** 同时改这两个 flag + 提交第一个 `feat!:`：先单独立一个 `chore(release): drop bump-minor-pre-major before 1.0` commit 让 release-please 把当前 `0.x.x` 推到 `1.0.0`，下一批 Conventional Commits 才走 major 规则。

2. **CHANGELOG.md 升段标题**：把「## [Unreleased]」改「## [1.0.0] - <date>」，并在顶部加一行 `## 🎉 1.0 稳定版` 说明（本节 checklist 6 条全过的证据链）。

3. **发版闸自检**：升 1.0 前 24 小时内跑完 `pytest tests/ -q`（必须 112 例全绿）+ `node --check tests/ui/{a11y,visual}.mjs` + `python -m ruff check .` + `python scripts/pyright_check.py`（必须 0 诊断）。

4. **README 三处对齐**：`README.md` / `README.zh-CN.md` 顶部徽章 / 安装段 / 配置表里所有「0.x」改成「1.x」；`docs/RELEASING.md` §1 表的「影响（< 1.0，当前）」列去掉（升 1.0 后无意义）。

5. **首次反映后守门**：升 1.0 后第一个 `feat!:` commit 必须显式带 `BREAKING CHANGE:` 脚注说明影响面，让 release-please 把版本号推到 `2.0.0` 而不是 minor；如不希望跳 major，把 `bump-minor-pre-major` 重新打开即可（**不建议**，升 1.0 的目的就是切 major 闸）。

**未升 1.0 前的提示**：当前（PR-22 落地后）`release-please-config.json` 仍维持 `bump-minor-pre-major: true` + `bump-patch-for-minor-pre-major: true`；`prerelease: false` 显式标注（PR-22 新增），1.0 当天不打 rc。`docs/RELEASING.md` §2.1 表里的 `< 1.0` 列保持有效。

> **关联引用**：`docs/OPTIMIZATION-ROADMAP.md` §3 表「release-please 升 1.0 评估」触发项（PR-22 已勾掉）；§6 风险登记里所有「字段名变更」类条目都已关闭（PR-1 + PR-8 + PR-12 三轮落地）；§8.4 / §8.5 / §8.6 / §8.7 持续把 release-please 链路打磨成正式基础设施。
