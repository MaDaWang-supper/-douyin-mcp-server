# ============================================
# Stage 1: Builder - 安装 Python 依赖
# ============================================
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装 uv（比 pip 快 10-100 倍）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 先复制依赖文件，利用 Docker 缓存层
COPY pyproject.toml uv.lock ./

# 安装依赖到 .venv（含 web 可选依赖）
RUN uv sync --frozen --no-dev --extra web --no-install-project

# ============================================
# Stage 2: Runtime - 最小运行时镜像
# ============================================
FROM python:3.12-slim AS runtime

# 安装 FFmpeg（完整版，含 libmp3lame 音频编码器）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl && \
    rm -rf /var/lib/apt/lists/*

# 验证 FFmpeg 支持 libmp3lame
RUN ffmpeg -encoders 2>/dev/null | grep -q libmp3lame || \
    (echo "ERROR: FFmpeg missing libmp3lame encoder" && exit 1)

WORKDIR /app

# 从 builder 复制 Python 虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制项目源码
COPY douyin_mcp_server/ douyin_mcp_server/
COPY douyin-video/ douyin-video/
COPY web/ web/
COPY pyproject.toml .

# API Key 通过环境变量或 docker-compose 传入
# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080

EXPOSE 8080

# 健康检查：用 $PORT 环境变量适配云平台动态端口（本地默认 8080）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/api/health || exit 1

# 启动 WebUI
CMD ["python", "web/app.py"]
