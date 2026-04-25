"""
EmailNotixion Core Module
"""
from .config import Config, LogLevel, AccountCache
from .account import AccountManager
from .monitor import EmailMonitor
from .email_client import EmailNotifier, EmailConfig, truncate_text, DedupCache

__all__ = [
    'Config', 'LogLevel', 'AccountCache',
    'AccountManager', 'EmailMonitor',
    'EmailNotifier', 'EmailConfig', 'truncate_text', 'DedupCache'
]
