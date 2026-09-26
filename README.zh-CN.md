# 🐟 lazy-fish（咸鱼之王宝箱活动自动点击器）

> 一只替你点点点的「懒鱼」，专为**咸鱼之王**宝箱活动而生。
> 安卓模拟器上挂个脚本，OpenCV 看屏找按钮，ADB 帮你点 —— 你去忙别的。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](#)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#)
[![License](https://img.shields.io/badge/License-MIT-green)](#)
[![Stack](https://img.shields.io/badge/Stack-FastAPI%20%2B%20OpenCV%20%2B%20ADB-009688)](#)
[![Repo](https://img.shields.io/badge/github-cloudcranes%2Flazy--fish-181717?logo=github)](https://github.com/cloudcranes/lazy-fish)
[![CI](https://github.com/cloudcranes/lazy-fish/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudcranes/lazy-fish/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-lazy--fish-2496ED?logo=docker&logoColor=white)](https://github.com/cloudcranes/lazy-fish/pkgs/container/lazy-fish)

> 📖 [English README](./README.md)

咸鱼之王宝箱活动本质是反复点击。`lazy-fish` 帮你看屏幕、找到正确按钮、自动点下去、等动画过去 —— 你不必盯着。

程序**只**用 ADB 截图和 `input tap`，不读内存、不改客户端、不要 root。

---

## ✨ 特性

- 🎯 **多尺度模板匹配**：同一按钮会被渲染成 3 种尺寸（±11%），靠 ROI 限定 + 两级搜索一份模板全收。
- 🛡️ **动作前等画面静止**：避开动画中间帧的低分误判，不浪费重试预算。
- 🧠 **热路径记忆**：上一帧命中的位置 + 尺度，下一帧只在那小块里复核（约 20 ms）。
- 📋 **方案芯片**：一套参数存为方案，下次点一下就载入；删除是软删除，方案可找回。
- 🎨 **单页 WebUI**：五个视图（控制台 / 方案 / 模板 / 截图采样 / 日志），支持深色模式，≤900px 自动切底部导航。
- 🧪 **真实语料回归**：`tests/recognition_eval.py` 跑真机截图，输出召回 / 误报 / 耗时。

---

## 🚀 一键启动（Windows）

双击 **`启动.bat`**，脚本会：

1. 创建 `.venv`
2. 安装 / 更新依赖
3. 校验 `adb` 在 `PATH`
4. 启动 WebUI（`http://127.0.0.1:8765`）
5. 自动打开浏览器

PowerShell 同效：

```powershell
cd F:\Code\scripts\xyzw_auto_clicker
powershell -ExecutionPolicy Bypass -File .\start.ps1
# .\start.ps1 -Port 8766
# .\start.ps1 -NoBrowser
# .\start.ps1 -Reinstall
```

**前提**：MuMu / 雷电 / 任意开启 ADB 调试的安卓模拟器，`adb` 已加入 `PATH`。

---

## 🛠️ 手动启动

```bash
git clone https://github.com/cloudcranes/lazy-fish.git
cd lazy-fish
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip install
.venv/bin/python -m xyzw_auto_clicker
```

打开 <http://127.0.0.1:8765>。

---

## 🐳 Docker 部署

WebUI 完全无头，适合容器化。ADB 仍需在宿主机（或 sidecar）跑，容器能联通即可。

```bash
docker compose up -d --build
# WebUI: http://127.0.0.1:8999
```

直接用 Docker：

```bash
docker build -t lazy-fish .
docker run --rm -p 8999:8999 \
  -v "$PWD/data:/app/data" \
  lazy-fish:latest

# 或者直接拉发布镜像：
docker pull ghcr.io/cloudcranes/lazy-fish:latest
docker run --rm -p 8999:8999 \
  -v "$PWD/data:/app/data" \
  ghcr.io/cloudcranes/lazy-fish:latest
```

让容器连到宿主 `adb-server`：

- macOS / Windows（Docker Desktop）：用 `host.docker.internal:5037`，在 UI 里把 `device_id` 填成它。
- Linux：容器加 `--network=host`；或在 compose 里加 `extra_hosts: ["host.docker.internal:host-gateway"]`。

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LAZY_FISH_HOST` | `0.0.0.0`（容器）/ `127.0.0.1`（本地） | 监听地址 |
| `LAZY_FISH_PORT` | `8999`（容器）/ `8765`（本地） | HTTP 端口 |

模板 / 方案 / 最后一帧缓存都通过 `./data` 卷持久化。

---

## 🧭 使用流程

1. 在模拟器里打开宝箱活动页面。
2. 控制台点 **检测设备**；自动识别错的就在「方案与参数」手动填 `device_id`。
3. 进「**截图采样**」，拉一帧画面，拖拽框选按钮区域，保存为模板。**重采旧模板**直接从「选择已有模板」下拉里选它，会提示覆盖预览。
4. 在「**模板管理**」给每张模板勾选 *首次* 或 *后续* 角色。
5. 在「**方案与参数**」调点后等待、置信度、多尺度等细粒度参数；点 **保存方案** 以后复用。
6. 回「**控制台**」：点方案芯片载入方案，调十连次数，点 **开始执行**。3 秒倒计时后开跑。
7. 急停：点 **停止**，或在项目根目录创建 `STOP` 文件。

---

## ⚙️ 识别是怎么工作的

同一按钮在游戏里被渲染成大约 395 / 438 / 487 px 三种宽度。`cv2.matchTemplate` 在 1% 缩放误差下得分就跌破 0.86 阈值，朴素单尺度匹配根本靠不住。

识别链路（`xyzw_auto_clicker/matcher.py`）：

| 阶段 | 耗时 | 干什么 |
| --- | --- | --- |
| 热路径 | ~20 ms | 只在上一帧命中的小窗 + 尺度上复核。失败才跌到下一步。 |
| 粗扫 | 视情况 | 1/4 分辨率上对 ROI 带内的所有尺度做 `matchTemplate`，选最佳尺度。 |
| 精扫 | 视情况 | 全分辨率，仅在粗扫峰值 ±1 档的小窗里复核。 |
| 全图回退 | 兜底 | ROI 里找不到，整张图再扫一遍（兜住偏出 ROI 带的按钮）。 |

ROI 默认 `0.55 ~ 0.80`（画面高度比例），按钮恒在此带。收紧提速、放松保召回。

默认值（也是调研标定的最优值，详见 `docs/RECOGNITION-RESEARCH.md`）：

```text
scale_min = 0.78   scale_max = 1.30   scale_step = 0.035
roi_band  = (0.55, 0.80)
threshold = 0.86
freeze_guard = 开（最多等 4 帧让画面静止）
```

---

## 📦 推荐的宝箱活动参数

根据 `咸鱼之王(2).mp4` 实测：单次十连（点击 → "再抽10次" 按钮出现）约 5 秒。

- 首次模板：`video-first-open10.png`
- 后续模板：`video-repeat10.png`
- 重试间隔：`0.8 s`
- 点后等待：`4.8 s`
- 连续失败暂停阈值：`8`

如果网络 / 动画更慢，把"点后等待"调到 `5.5`–`6.0 s`。

---

## 🧪 测试

```bash
.venv/bin/python tests/test_core.py
.venv/bin/python tests/recognition_eval.py            # 1164 帧抽样
.venv/bin/python tests/recognition_eval.py --stride 1  # 全量
```

`recognition_eval.py` 在 **召回 / 位置 / 误报** 任一不为 0 时以非零退出。这里的"误报"等价于"用 A 模板去匹配 B 按钮所在的帧会不会误命中"——是任务乱点风险的直接度量。

---

## 🗂️ 目录

```text
xyzw_auto_clicker/
├── adb.py            # ADB 设备 / 截图 / 点击
├── matcher.py        # OpenCV 多尺度匹配 + 静止守卫
├── runner.py         # 任务循环 / 停止 / 日志 / 异常暂停
├── app.py            # FastAPI 路由 + 静态 UI
├── tasks/chest.py    # 宝箱任务配置入口
├── plans.py          # 方案保存 / 载入 / 软删除
├── models.py         # TaskConfig + 校验
└── settings.py       # 路径 + 文件名乱码修复
data/
├── templates/        # 按钮模板
├── plans/            # 已保存的方案
├── plans-trash/      # 软删除的方案（可找回）
└── screenshots/      # 仅缓存最近一帧，不每帧落盘
docs/
├── RECOGNITION-RESEARCH.md   # 识别根因 + 实验数据
├── UI-DESIGN.md              # 设计令牌、组件契约
└── OPTIMIZATION-ROADMAP.md   # 三阶段优化路线图（阶段 1、2 已落地）
relay/                # 可选 WebSocket 中继（远程预览用）
tests/                # 核心单测 + 识别评测 + UI 冒烟
```

---

## 🔒 安全边界

- 按固定次数停止，**绝不**读取活动币余额。
- 连续匹配失败会暂停，避免乱点。
- 分辨率 / 按钮样式变了需要重新采样（同一活动内的尺寸漂移由多尺度识别兜住，不用重采）。
- 删除方案是**软删除**，文件移到 `data/plans-trash/`，可手工找回。

---

## 🤝 贡献

欢迎 Issue 和 PR。改 `matcher.py` 时：

1. push 前先跑 `tests/recognition_eval.py`。
2. 在 PR 描述里贴 **召回 / 误报 / 耗时** 三项指标。
3. 默认尺度区间不要随意扩大 —— 已经是实测标定的最优值。

### Make 快捷命令

```text
make install       # 虚拟环境 + 依赖
make run           # 本地启动 WebUI，端口 8765
make test          # pytest tests/test_core.py
make eval          # 真机语料回归
make build         # docker build -> lazy-fish:dev
make compose-up    # compose up（拉 ghcr.io/cloudcranes/lazy-fish:latest）
make release VERSION=0.1.0   # 打 tag + push，CI 构建并发布
```

### 发布版本

1. 在 `main` 上正常提交。
2. `make release VERSION=x.y.z` —— 推送 `vx.y.z` tag。
3. `.github/workflows/release.yml` 跨架构构建（`linux/amd64,linux/arm64`），推送到 `ghcr.io/cloudcranes/lazy-fish`，并生成 GitHub Release（附 docker run 片段）。

---

## 📄 许可证

MIT。