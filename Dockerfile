FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

# 证据导出与打包测试需要 git 可执行文件（与 CI ubuntu-latest 环境一致）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 与 CI 一致：使用完整仓库内容（.dockerignore 排除运行时产物与本地材料），
# 保证 native/、packaging/、docs/（含 evidence）等仓库内容测试可在容器内复验。
COPY . .

RUN python -m pip install --no-cache-dir -U pip \
    && python -m pip install --no-cache-dir -e ".[dev]"

CMD ["python", "-m", "pytest", "-q"]
