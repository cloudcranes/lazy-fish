# syntax=docker/dockerfile:1.7
#
# 镜像大小估算（参考值，需以 docker images 实测为准）:
#   - python:3.12-slim         ~  130 MB
#   - apt runtime deps (libgl1 + libglib2.0-0)  ~   15 MB
#   - opencv-python-headless 4.x                 ~   45 MB
#   - 其余 Python 依赖（fastapi/uvicorn/numpy/pydantic/jinja2） ~   60 MB
#   - 应用代码 + 占位 data 目录                  ~    1 MB
# 合计目标: < 260 MB（PR-4 前 ≈ 360 MB；libsm6/libxrender1/libxext6 砍掉 ≈ -8 MB；opencv-python → headless ≈ -30 MB；apt --no-install-recommends + lists 清理 ≈ -50 MB）。
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LAZY_FISH_HOST=0.0.0.0 \
    LAZY_FISH_PORT=8999

# 运行时只装 opencv-python-headless 实际需要的两个系统库。
# libsm6/libxrender1/libxext6 是 opencv-python（非 headless）的 GUI 依赖，headless 用不上。
# 合并 RUN 层 + --no-install-recommends + 清理 apt lists，缩小镜像约 50MB。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY xyzw_auto_clicker ./xyzw_auto_clicker

# 数据目录（模板/方案/截图缓存）由挂载提供，这里只建好占位以便镜像自检
RUN mkdir -p /app/data/templates /app/data/plans /app/data/screenshots

EXPOSE 8999

# 容器内不连 adb，跑纯 web；adb 转发留给宿主侧的 adb-server。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8999/', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "xyzw_auto_clicker"]