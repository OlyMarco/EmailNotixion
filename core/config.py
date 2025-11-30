"""配置常量和数据类型定义"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Config:
    """插件配置常量"""
    RECREATE_INTERVAL = 120
    CHECK_TIMEOUT = 30
    STOP_TIMEOUT = 5
    MIN_INTERVAL = 0.5
    DEFAULT_INTERVAL = 3
    MIN_TEXT_NUM = 10
    DEFAULT_TEXT_NUM = 50
    ACCOUNT_CACHE_TTL = 300
    DEDUP_CLEANUP_INTERVAL = 300


class LogLevel(Enum):
    """日志级别"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class AccountCache:
    """账号有效性缓存"""
    is_valid: bool
    checked_at: float
    error_message: Optional[str] = None
