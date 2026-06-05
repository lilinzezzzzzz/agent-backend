FROM python:3.12.13-slim-trixie AS builder

# 基础环境变量
ENV TZ=Etc/UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

# uv 虚拟环境配置
# UV_INDEX_URL: 使用阿里云 PyPI 镜像源加速包下载
# UV_COMPILE_BYTECODE: 编译字节码加快启动速度
# UV_LINK_MODE: copy 模式更适合容器环境（避免硬链接问题）
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

# 安装依赖
# 此时 VIRTUAL_ENV 已经生效，uv 会自动把包安装到 /app/.venv 中
RUN uv sync --frozen --no-cache --no-default-groups

FROM python:3.12.13-slim-trixie AS runtime

# 基础环境变量
ENV TZ=Etc/UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import socket; s = socket.create_connection(('127.0.0.1', 8000), 2); s.close()"]

# 启动命令
ENTRYPOINT ["uvicorn", "entrypoints.api:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop", "--http", "httptools", "--access-log"]
