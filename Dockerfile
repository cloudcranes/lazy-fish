# syntax=docker/dockerfile:1.7
#
# 多阶段构建（PR-6 契约+可维护）：
#   builder  —— 全套 python:3.12-slim + 编译工具，装齐依赖（含 setuptools/wheel）。
#               pip 下载 + 构建只在第一阶段，site-packages 复用给 runtime。
#   runtime  —— 与 builder 同 base，但只 COPY site-packages + 源码；
#               运行时 apt 只装 opencv 真正需要的 libgl1 + libglib2.0-0。
# 体积目标：< 230 MB（PR-4 ≈ 260 MB；省下 pip / build-base / 缓存约 30 MB）。
#
# 数据目录（模板/方案/截图缓存）由挂载提供；镜像里只建占位以满足容器自检。
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# builder 阶段：装依赖到 /install（独立 prefix，便于 COPY 复用）。
# 不要把 build-essential 写进 runtime：编译工具链本身就要 300+ MB，
# runtime 只需要 wheel 已经预编译好的 site-packages。
COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LAZY_FISH_HOST=0.0.0.0 \
    LAZY_FISH_PORT=8999 \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/install/lib/python3.12/site-packages"

# 运行时只装 opencv-python-headless 实际需要的两个系统库。
# 合并 RUN 层 + --no-install-recommends + 清理 apt lists。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# builder 阶段的 site-packages 复用进来；COPY --from 不会带 pip / build-base。
COPY --from=builder /install /install
WORKDIR /app

COPY pyproject.toml ./
COPY xyzw_auto_clicker ./xyzw_auto_clicker

# 数据目录（模板/方案/截图缓存）由挂载提供，这里只建好占位以便镜像自检
RUN mkdir -p /app/data/templates /app/data/plans /app/data/screenshots

EXPOSE 8999

# 容器内不连 adb，跑纯 web；adb 转发留给宿主侧的 adb-server。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, json, sys; \
r = urllib.request.urlopen('http://127.0.0.1:8999/api/health', timeout=3); \
sys.exit(0 if json.loads(r.read()).get('status') == 'ok' else 1)"

CMD ["python", "-m", "xyzw_auto_clicker"]