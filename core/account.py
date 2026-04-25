import time
from typing import List, Optional, Tuple, Dict, Callable

from .config import Config, AccountCache
from .email_client import EmailNotifier


class AccountManager:
    """邮箱账号管理器"""

    def __init__(
        self,
        config_getter: Callable,
        config_setter: Callable,
        save_config: Callable,
        logger_func: Callable
    ):
        self._get_config: Callable = config_getter
        self._set_config: Callable = config_setter
        self._save_config: Callable = save_config
        self._log: Callable = logger_func
        self._cache: Dict[str, AccountCache] = {}

    @property
    def cache(self) -> Dict[str, AccountCache]:
        return self._cache

    def clear_cache(self) -> None:
        self._cache.clear()

    def get_accounts(self) -> List[str]:
        return list(self._get_config("accounts", []))

    @staticmethod
    def parse_account(account: str) -> Optional[Tuple[str, str, str]]:
        parts = account.split(',')
        if len(parts) != 3:
            return None
        host, user, password = (part.strip() for part in parts)
        if not all([host, user, password]):
            return None
        return host, user, password

    def _is_cache_valid(self, account: str) -> bool:
        if account not in self._cache:
            return False
        return (time.time() - self._cache[account].checked_at) < Config.ACCOUNT_CACHE_TTL

    def get_valid_accounts(
        self,
        force_refresh: bool = False,
        logger: Optional[object] = None
    ) -> List[str]:
        accounts = self.get_accounts()
        valid_accounts: List[str] = []

        for account in accounts:
            parsed = self.parse_account(account)
            if not parsed:
                continue

            if not force_refresh and self._is_cache_valid(account):
                if self._cache[account].is_valid:
                    valid_accounts.append(account)
                continue

            host, user, password = parsed
            test_notifier: Optional[EmailNotifier] = None
            try:
                test_notifier = EmailNotifier(host, user, password, logger)
                is_valid = test_notifier.test_connection()
                self._cache[account] = AccountCache(
                    is_valid=is_valid,
                    checked_at=time.time(),
                    error_message=None if is_valid else "连接测试失败"
                )
                if is_valid:
                    valid_accounts.append(account)
            except Exception as e:
                self._cache[account] = AccountCache(
                    is_valid=False,
                    checked_at=time.time(),
                    error_message=str(e)
                )
            finally:
                if test_notifier:
                    test_notifier.cleanup()

        return valid_accounts

    def add_account(self, entry: str, logger: Optional[object] = None) -> Tuple[bool, str]:
        entry = entry.strip()
        if not entry:
            return False, "账号配置不能为空"

        parsed = self.parse_account(entry)
        if not parsed:
            return False, "格式错误，正确格式: IMAP服务器,邮箱,密码"

        accounts = self.get_accounts()
        if entry in accounts:
            return False, "账号已存在"

        host, user, password = parsed
        test_notifier: Optional[EmailNotifier] = None
        try:
            test_notifier = EmailNotifier(host, user, password, logger)
            if not test_notifier.test_connection():
                return False, "连接测试失败，请检查配置是否正确"
        except Exception as e:
            return False, f"连接测试失败: {e}"
        finally:
            if test_notifier:
                test_notifier.cleanup()

        accounts.append(entry)
        self._set_config("accounts", accounts)
        self._save_config()
        self._cache[entry] = AccountCache(is_valid=True, checked_at=time.time())
        self._log(f"++ 添加账号: {user}")
        return True, f"成功添加账号: {user}"

    def del_account(self, user: str) -> Tuple[bool, str]:
        user = user.strip()
        if not user:
            return False, "邮箱地址不能为空"

        accounts = self.get_accounts()
        original_count = len(accounts)

        new_accounts: List[str] = []
        deleted_entry: Optional[str] = None
        for acc in accounts:
            parsed = self.parse_account(acc)
            if parsed and parsed[1] == user:
                deleted_entry = acc
            else:
                new_accounts.append(acc)

        if len(new_accounts) < original_count:
            self._set_config("accounts", new_accounts)
            self._save_config()
            if deleted_entry and deleted_entry in self._cache:
                del self._cache[deleted_entry]
            self._log(f"-- 删除账号: {user}")
            return True, f"成功删除账号: {user}"

        return False, f"未找到账号: {user}"
