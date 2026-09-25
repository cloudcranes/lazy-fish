# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LAZY_FISH_HOST=0.0.0.0 \
    LAZY_FISH_PORT=8999

# opencv-python 需要 libgl / libglib；slim 镜像里没有。openblas 也顺手补上避免 numpy 警告。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
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