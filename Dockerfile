FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（用于C/C++代码验证）
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libssl-dev \
    libcrypto++-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p generated_code logs static

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/providers')" || exit 1

# 启动命令（使用多个worker提高性能）
CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--log-level", "info"]

