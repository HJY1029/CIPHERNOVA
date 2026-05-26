#!/bin/bash

# 部署脚本
set -e

echo "=========================================="
echo "开始部署 AI密码学代码生成助手"
echo "=========================================="

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: 未找到Docker，请先安装Docker${NC}"
    exit 1
fi

# 检查Docker Compose是否安装
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}错误: 未找到Docker Compose，请先安装Docker Compose${NC}"
    exit 1
fi

# 1. 拉取最新代码（如果使用Git）
if [ -d ".git" ]; then
    echo -e "${YELLOW}正在拉取最新代码...${NC}"
    git pull origin main || echo "警告: Git拉取失败，继续使用当前代码"
fi

# 2. 检查环境变量文件
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}未找到.env文件，从.env.example创建...${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${YELLOW}请编辑.env文件，填入API密钥${NC}"
    else
        echo -e "${RED}错误: 未找到.env.example文件${NC}"
        exit 1
    fi
fi

# 3. 创建必要的目录
echo -e "${YELLOW}创建必要的目录...${NC}"
mkdir -p generated_code logs static ssl

# 4. 检查SSL证书
if [ ! -f "ssl/cert.pem" ] || [ ! -f "ssl/key.pem" ]; then
    echo -e "${YELLOW}警告: 未找到SSL证书文件${NC}"
    echo -e "${YELLOW}请将SSL证书放置到 ssl/cert.pem 和 ssl/key.pem${NC}"
    echo -e "${YELLOW}或使用Let's Encrypt生成证书${NC}"
    read -p "是否继续部署？(y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 5. 构建Docker镜像
echo -e "${YELLOW}正在构建Docker镜像...${NC}"
docker-compose build || docker compose build

# 6. 停止旧容器
echo -e "${YELLOW}停止旧容器...${NC}"
docker-compose down || docker compose down

# 7. 启动新容器
echo -e "${YELLOW}启动新容器...${NC}"
docker-compose up -d || docker compose up -d

# 8. 等待服务启动
echo -e "${YELLOW}等待服务启动...${NC}"
sleep 10

# 9. 检查健康状态
echo -e "${YELLOW}检查服务健康状态...${NC}"
for i in {1..30}; do
    if curl -f http://localhost:8000/api/providers > /dev/null 2>&1; then
        echo -e "${GREEN}✓ 服务启动成功！${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}✗ 服务启动失败，请检查日志${NC}"
        docker-compose logs web || docker compose logs web
        exit 1
    fi
    sleep 2
done

# 10. 显示服务状态
echo -e "${YELLOW}服务状态:${NC}"
docker-compose ps || docker compose ps

echo ""
echo -e "${GREEN}=========================================="
echo "部署完成！"
echo "=========================================="
echo -e "Web服务: ${NC}http://localhost:8000"
echo -e "${GREEN}API接口: ${NC}http://localhost:8000/api/providers"
echo ""
echo -e "${YELLOW}查看日志:${NC} docker-compose logs -f"
echo -e "${YELLOW}停止服务:${NC} docker-compose down"
echo -e "${YELLOW}重启服务:${NC} docker-compose restart"
echo -e "${GREEN}==========================================${NC}"

