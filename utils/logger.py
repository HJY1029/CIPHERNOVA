import logging
import sys

try:
    from colorama import init, Fore, Style

    init(autoreset=True)
except ImportError:
    # 最小环境（如仅 pip install pyyaml 跑 related_work 评测）可不装 colorama
    def init(**_kwargs):
        pass

    class _Fore:
        CYAN = GREEN = YELLOW = RED = ""

    class _Style:
        RESET_ALL = BRIGHT = ""

    Fore = _Fore()
    Style = _Style()

def setup_logger(name: str = "CryptoAgent", level: int = logging.INFO):
    """设置彩色日志输出"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        
        class ColoredFormatter(logging.Formatter):
            COLORS = {
                'DEBUG': Fore.CYAN,
                'INFO': Fore.GREEN,
                'WARNING': Fore.YELLOW,
                'ERROR': Fore.RED,
                'CRITICAL': Fore.RED + Style.BRIGHT
            }
            
            def format(self, record):
                log_color = self.COLORS.get(record.levelname, '')
                record.levelname = f"{log_color}{record.levelname}{Style.RESET_ALL}"
                return super().format(record)
        
        formatter = ColoredFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger
