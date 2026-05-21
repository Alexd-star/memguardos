FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY pyproject.toml README.md LICENSE ./
COPY memguard ./memguard
COPY scripts ./scripts
COPY tests ./tests
COPY data ./data

RUN python -m pip install --no-cache-dir -U pip \
    && python -m pip install --no-cache-dir -e ".[dev]"

CMD ["python", "-m", "pytest", "-q"]
