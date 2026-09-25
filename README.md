# 🐟 lazy-fish

> A lazy friend for **咸鱼之王 (Salted Fish King)** chest events.
> Hands-off auto-clicker for Android emulators, driven by ADB + OpenCV template matching.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](#)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#)
[![License](https://img.shields.io/badge/License-MIT-green)](#)
[![Stack](https://img.shields.io/badge/Stack-FastAPI%20%2B%20OpenCV%20%2B%20ADB-009688)](#)
[![Repo](https://img.shields.io/badge/github-cloudcranes%2Flazy--fish-181717?logo=github)](https://github.com/cloudcranes/lazy-fish)
[![CI](https://github.com/cloudcranes/lazy-fish/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudcranes/lazy-fish/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-lazy--fish-2496ED?logo=docker&logoColor=white)](https://github.com/cloudcranes/lazy-fish/pkgs/container/lazy-fish)

Salted Fish King's chest event rewards repeat-tap grinding. `lazy-fish` watches the screen, finds the right button at any size, taps it, and waits for the animation — so you can go do something else.

It **only** uses ADB screenshots and `input tap`. No memory reads, no client patching, no root.

> 📖 [中文说明](./README.zh-CN.md)

---

## ✨ Features

- 🎯 **Multi-scale template matching** — the same button renders at 3 sizes (±11%). A ROI-banded, two-stage search handles all of them with one template.
- 🛡️ **Freeze guard** — waits for the screen to settle before recognizing, so mid-animation captures don't drain your retry budget.
- 🧠 **Hot-path memory** — once a button is found, the next frame only re-checks a small window (~20 ms).
- 📋 **Plan chips** — save a full run as a plan, reload it with one click; soft-delete keeps trash recoverable.
- 🎨 **Single-page WebUI** — five views (Console / Plans / Templates / Sampler / Log), dark mode, mobile-friendly at ≤900 px.
- 🧪 **Regression harness** — `tests/recognition_eval.py` measures recall / false-positive / latency on real footage.

---

## 🚀 Quick start (Windows)

Double-click **`启动.bat`**. It will:

1. Create `.venv`
2. Install / update dependencies
3. Verify `adb` is on `PATH`
4. Start the WebUI on `http://127.0.0.1:8765`
5. Open the browser for you

PowerShell equivalent:

```powershell
cd F:\Code\scripts\xyzw_auto_clicker
powershell -ExecutionPolicy Bypass -File .\start.ps1
# .\start.ps1 -Port 8766
# .\start.ps1 -NoBrowser
# .\start.ps1 -Reinstall
```

**Prereqs**: MuMu / LDPlayer / any emulator with ADB debugging on, `adb` on `PATH`.

---

## 🛠️ Manual start

```bash
git clone https://github.com/cloudcranes/lazy-fish.git
cd lazy-fish
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # or .venv\Scripts\pip install on Windows
.venv/bin/python -m xyzw_auto_clicker
```

Open <http://127.0.0.1:8765>.

---

## 🐳 Docker

The web UI is fully headless — perfect for a container. ADB still needs to run on the host (or a sidecar) and be reachable from the container.

```bash
docker compose up -d --build
# WebUI: http://127.0.0.1:8999
```

Plain Docker:

```bash
docker build -t lazy-fish .
docker run --rm -p 8999:8999 \
  -v "$PWD/data:/app/data" \
  lazy-fish:latest

# or pull the published image:
docker pull ghcr.io/cloudcranes/lazy-fish:latest
docker run --rm -p 8999:8999 \
  -v "$PWD/data:/app/data" \
  ghcr.io/cloudcranes/lazy-fish:latest
```

Talking to host-side `adb-server`:

- macOS / Windows (Docker Desktop): `host.docker.internal:5037` — set `device_id` accordingly in the UI.
- Linux: run the container with `--network=host`, or add `extra_hosts: ["host.docker.internal:host-gateway"]` in compose and use the same hostname.

Configurable via env:

| Var | Default | Notes |
| --- | --- | --- |
| `LAZY_FISH_HOST` | `0.0.0.0` (container) / `127.0.0.1` (local) | bind address |
| `LAZY_FISH_PORT` | `8999` (container) / `8765` (local) | HTTP port |

Templates, plans and the last-frame cache persist via the `./data` volume mount.

---

## 🧭 Usage

1. Open the chest event page in your emulator.
2. Hit **检测设备** in the Console. Set `device_id` in *Plans* if auto-detect picked the wrong one.
3. Go to **截图采样**, capture a frame, drag-select the button region, save as a template. Re-sampling an existing one? Pick it from the dropdown — you'll get a "will overwrite" preview.
4. In **模板管理**, assign each template to *首次* (first click) or *后续* (repeat).
5. In **方案与参数**, tune per-tap interval / confidence / multi-scale settings; **保存方案** to reuse later.
6. Back to **控制台**: tap a plan chip to load it, set the click count, hit **开始执行**. 3-second countdown, then it runs.
7. To abort: hit **停止**, or drop a `STOP` file in the project root.

---

## ⚙️ How recognition works

The same button renders at ~395 / 438 / 487 px wide. `cv2.matchTemplate` drops below 0.86 confidence at just 1% scale error, so naive single-scale matching is unreliable.

Recognition pipeline (`xyzw_auto_clicker/matcher.py`):

| Stage | Cost | What it does |
| --- | --- | --- |
| Hot path | ~20 ms | Re-check only the last-known window + scale. Falls through on miss. |
| Coarse scan | varies | 1/4-resolution `matchTemplate` over all scales inside the ROI band. Picks the best scale. |
| Precise scan | varies | Full-resolution scan at the coarse peak ±1 scale, in a small window around the coarse position. |
| Full-frame fallback | last resort | If ROI scan missed, scan the entire frame (catches off-band buttons). |

ROI default `0.55~0.80` of screen height — buttons live there. Tighten for speed, loosen for unfamiliar screens.

Defaults (also the calibration sweet spot, see `docs/RECOGNITION-RESEARCH.md`):

```text
scale_min = 0.78   scale_max = 1.30   scale_step = 0.035
roi_band  = (0.55, 0.80)
threshold = 0.86
freeze_guard = on (waits up to 4 frames for screen to settle)
```

---

## 📦 Recommended chest-event params

From the `咸鱼之王(2).mp4` calibration: a single 10-pull cycle (tap → "再抽10次" button visible) takes ~5 s.

- First template: `video-first-open10.png`
- Repeat template: `video-repeat10.png`
- Interval between retries: `0.8 s`
- Post-tap wait: `4.8 s`
- Consecutive-miss pause: `8`

If your network is slower, push post-tap wait to `5.5`–`6.0 s`.

---

## 🧪 Tests

```bash
.venv/bin/python tests/test_core.py
.venv/bin/python tests/recognition_eval.py            # 1164-frame sample
.venv/bin/python tests/recognition_eval.py --stride 1  # full corpus
```

`recognition_eval.py` exits non-zero if **recall, position, or false-positive is anything other than 0**. False-positive here means "does template A falsely match a frame where button B is showing" — the direct risk of wild clicks.

---

## 🗂️ Layout

```text
xyzw_auto_clicker/
├── adb.py            # ADB devices / screenshot / tap
├── matcher.py        # OpenCV multi-scale matcher + freeze guard
├── runner.py         # Task loop, stop, logs, exception pause
├── app.py            # FastAPI routes + static UI
├── tasks/chest.py    # Chest-task config builder
├── plans.py          # Plan save / load / soft-delete
├── models.py         # TaskConfig + validation
└── settings.py       # Paths + safe filename repair
data/
├── templates/        # Per-button templates
├── plans/            # Saved run configs
├── plans-trash/      # Soft-deleted plans (recoverable)
└── screenshots/      # Last-frame cache only; no per-frame dumps
docs/
├── RECOGNITION-RESEARCH.md   # Recognition root cause + experiment data
└── UI-DESIGN.md              # Design tokens, component contract
relay/                # Optional WebSocket relay for remote preview
tests/                # Core tests + recognition eval + UI smoke
```

---

## 🔒 Safety boundaries

- Stops on a fixed click count. **Never** reads in-game currency.
- Pauses after N consecutive misses — wild-tap protection.
- Resolution / button-style changes need a fresh template (multi-scale already absorbs size drift within an event).
- Plan deletion is **soft**: files move to `data/plans-trash/`.

---

## 🤝 Contributing

Issues and PRs welcome. If you touch the matcher:

1. Run `tests/recognition_eval.py` before pushing.
2. Report `recall / false-positive / latency` in the PR description.
3. Don't widen the default scale range without a reason — it's already calibrated.

### Make shortcuts

```text
make install       # venv + deps
make run           # local WebUI on :8765
make test          # pytest tests/test_core.py
make eval          # recognition regression on real footage
make build         # docker build -> lazy-fish:dev
make compose-up    # compose up (pulls ghcr.io/cloudcranes/lazy-fish:latest)
make release VERSION=0.1.0   # tag + push, CI builds & publishes
```

### Publishing a release

1. Bump / commit whatever you want on `main`.
2. `make release VERSION=x.y.z` — pushes the `vx.y.z` tag.
3. `.github/workflows/release.yml` builds multi-arch images (`linux/amd64,linux/arm64`), pushes to `ghcr.io/cloudcranes/lazy-fish`, and creates a GitHub release with the docker run snippet.

---

## 📄 License

MIT.