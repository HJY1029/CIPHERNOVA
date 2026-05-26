"""
启动Web服务器
"""
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 直接导入app对象
from web.server import app
import uvicorn

if __name__ == "__main__":
    import multiprocessing
    
    # 获取CPU核心数，用于设置worker数量
    cpu_count = multiprocessing.cpu_count()
    workers = max(1, min(cpu_count, 4))  # 最多4个worker，至少1个
    
    print(f"启动服务器，使用 {workers} 个worker进程（CPU核心数: {cpu_count}）")
    print("支持多页面并发运行")
    
    uvicorn.run(
        app,
        host="0.0.0.0",  # 允许外部访问
        port=8000,
        workers=workers,  # 多进程支持
        reload=False,  # 生产环境关闭reload
        log_level="info"
    )