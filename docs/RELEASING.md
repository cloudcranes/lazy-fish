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

## 3. 手动触发（应急路径）

> 仅在 release-please 失灵、或需要为某个 commit 单跑一次构建时使用。

### 3.1 通过 GitHub UI

1. 打开 Actions → **Release** workflow → **Run workflow**；
2. 选择 `main` 分支，**不要**填输入（`release.yml` 当前没暴露 input）；
3. 点 **Run workflow**。

`Extract version` 在 `workflow_dispatch` 路径下用 `${GITHUB_SHA:0:7}` 作为 `VERSION`，避免 `GITHUB_REF_NAME=main` 这种无意义 tag 被推 GHCR。注意：`if: github.event_name == 'push'` 闸会拦住 publish 与 release 创建，所以这种触发只产生镜像，**不会**推到 GHCR，也不会生成 GitHub Release——目的是做本地验证。

### 3.2 通过 Makefile（DEPRECATED）

```bash
make release-dispatch   # 走 gh workflow run release.yml --ref main
```

**已弃用**：输出会明确提示改用 release-please PR；仅作为逃生口保留。

## 4. 不要做的事

- ❌ 手动 `git tag v0.2.0 && git push origin v0.2.0`（绕过 changelog PR）；
- ❌ 手动编辑 `.release-please-manifest.json` 跳版本；
- ❌ 在 release PR 上点 **Rebase and merge**（release-please 需要 squash merge 来识别）；
- ❌ 改 `release.yml` 的 `Extract version` 让 dispatch 也走 `${GITHUB_REF_NAME#v}`（会变成 `VERSION=main`，污染 GHCR tag）。

## 5. 排障

| 现象 | 排查 |
|---|---|
| release-please 没开 PR | 检查 `.github/workflows/release-please.yml` 是否启用、push 是否触发 `main` 分支、commit 是否带 `feat:` / `fix:` 前缀 |
| Release PR 卡在 merge | 确认 CI 全绿；可能有 `pull_request` 权限问题，必要时给 `GITHUB_TOKEN` 加 `contents: write, pull-requests: write` |
| tag 推上去但 release.yml 没跑 | `.github/workflows/release.yml` 的 `on.push.tags` 是 `v*.*.*`，确认 tag 名格式 |
| workflow_dispatch 跑出来的镜像 tag 是 `main` | 这是老 bug；当前 PR-13 已用 `${GITHUB_SHA:0:7}` 修复，确保用的是最新 `release.yml` |
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
