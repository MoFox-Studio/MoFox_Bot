FROM python:3.13.5-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 工作目录
WORKDIR /app

# 复制依赖列表
COPY pyproject.toml .

# 编译器
RUN apt-get update && apt-get install -y build-essential


# 安装依赖
RUN uv sync
COPY . .

EXPOSE 8000

ENTRYPOINT [ "uv","run","bot.py" ]