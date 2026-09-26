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

### 3.1 通过 GitHub UI

1. 打开 Actions → **Release** workflow → **Run workflow**；
2. 选择 `main` 分支；
3. `version` 下拉选择一个版本（`v0.1.0` ~ `v1.0.0`），例如 `v0.2.0`；
4. 点 **Run workflow**。

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
| Release PR 卡在 merge | 确认 CI 全绿；可能有 `pull_request` 权限问题，必要时给 `GITHUB_TOKEN` 加 `contents: write, pull-requests: write` |
| tag 推上去但 release.yml 没跑 | `.github/workflows/release.yml` 的 `on.push.tags` 是 `v*.*.*`，确认 tag 名格式 |
| workflow_dispatch 跑出来的镜像 tag 是 `main` 或 `GITHUB_SHA` | 这是老行为；当前 PR-16 已用 `inputs.version` 解闸，确保用的是最新 `release.yml`（`on.workflow_dispatch.inputs.version` 必须存在） |
| GHCR 镜像看不到 | 检查 repo `Settings → Packages → Visibility`，新 repo 默认是 private |

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
