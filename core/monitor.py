import asyncio
import time
from typing import Dict, Set, Optional, Callable, List, Tuple, Any

from .config import Config, LogLevel
from .account import AccountManager
from .email_client import EmailNotifier, DedupCache


class EmailMonitor:
    """邮件监控服务"""

    def __init__(
        self,
        account_manager: AccountManager,
        log_func: Callable,
        send_func: Callable,
        text_num: int,
        logger: Optional[object] = None
    ):
        self._account_manager: AccountManager = account_manager
        self._log: Callable = log_func
        self._send_notification: Callable = send_func
        self._text_num: int = text_num
        self._logger: Optional[object] = logger

        self._notifiers: Dict[str, EmailNotifier] = {}
        self._is_running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._last_recreate_time: float = 0.0
        self._interval: float = Config.DEFAULT_INTERVAL

        self._dedup_cache: DedupCache = DedupCache()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def notifiers(self) -> Dict[str, EmailNotifier]:
        return self._notifiers

    @property
    def interval(self) -> float:
        return self._interval

    @interval.setter
    def interval(self, value: float) -> None:
        self._interval = max(value, Config.MIN_INTERVAL)

    @property
    def text_num(self) -> int:
        return self._text_num

    @text_num.setter
    def text_num(self, value: int) -> None:
        self._text_num = max(value, Config.MIN_TEXT_NUM)
        for notifier in self._notifiers.values():
            notifier.text_num = self._text_num

    @property
    def last_recreate_time(self) -> float:
        return self._last_recreate_time

    def init_notifiers(self) -> None:
        for notifier in self._notifiers.values():
            notifier.cleanup()

        self._notifiers.clear()
        valid_accounts = self._account_manager.get_valid_accounts(logger=self._logger)

        for account in valid_accounts:
            parsed = self._account_manager.parse_account(account)
            if not parsed:
                continue

            host, user, password = parsed
            try:
                notifier = EmailNotifier(host, user, password, self._logger)
                notifier.text_num = self._text_num
                self._notifiers[user] = notifier
            except Exception as e:
                self._log(f"!! 初始化账号失败 {user}: {e}", LogLevel.ERROR)

    def _get_dedup_key(
        self,
        user: str,
        subject: str,
        email_time: Optional[Any]
    ) -> str:
        time_str = email_time.strftime('%Y%m%d%H%M') if email_time else "unknown"
        return f"{user}|{subject}|{time_str}"

    def _is_duplicate(
        self,
        user: str,
        subject: str,
        email_time: Optional[Any]
    ) -> bool:
        key = self._get_dedup_key(user, subject, email_time)
        if key in self._dedup_cache:
            return True
        self._dedup_cache.add(key)
        return False

    async def _monitor_loop(
        self,
        targets: Set[str],
        event_map: Dict[str, Any]
    ) -> None:
        while self._is_running:
            try:
                current_time = time.time()

                if current_time - self._last_recreate_time > Config.RECREATE_INTERVAL:
                    self.init_notifiers()
                    self._last_recreate_time = current_time

                if self._notifiers:
                    check_tasks = [
                        asyncio.wait_for(
                            asyncio.to_thread(notifier.check_and_notify),
                            timeout=Config.CHECK_TIMEOUT
                        )
                        for notifier in self._notifiers.values()
                    ]

                    results = await asyncio.gather(*check_tasks, return_exceptions=True)

                    for (user, notifier), result in zip(self._notifiers.items(), results):
                        if isinstance(result, asyncio.TimeoutError):
                            self._log(f"!! {user} 检查超时", LogLevel.WARNING)
                        elif isinstance(result, Exception):
                            self._log(f"!! {user} 检查错误: {result}", LogLevel.ERROR)
                        elif result and isinstance(result, list) and len(result) > 0:
                            self._log(f"= {user} 收到 {len(result)} 封新邮件")
                            for email_info in result:
                                if isinstance(email_info, tuple) and len(email_info) == 3:
                                    email_time, subject, mail_content = email_info
                                    if not self._is_duplicate(user, subject, email_time):
                                        await self._send_to_targets(
                                            targets, event_map, user,
                                            email_time, subject, mail_content
                                        )

                await asyncio.sleep(self._interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log(f"!! 监控循环错误: {e}", LogLevel.ERROR)
                await asyncio.sleep(self._interval)

    async def _send_to_targets(
        self,
        targets: Set[str],
        event_map: Dict[str, Any],
        user: str,
        email_time: Optional[Any],
        subject: str,
        mail_content: str
    ) -> None:
        if not targets:
            return

        self._log(f"-> 准备发送通知到 {len(targets)} 个目标")

        send_tasks: List[Tuple[str, Callable]] = []
        for target in list(targets):
            target_event = event_map.get(target)
            if target_event:
                task = self._send_notification(target_event, user, email_time, subject, mail_content)
                send_tasks.append((target, task))

        if send_tasks:
            results = await asyncio.gather(*[task for _, task in send_tasks], return_exceptions=True)
            for (target, _), result in zip(send_tasks, results):
                if isinstance(result, Exception):
                    self._log(f"!! 向 {target} 发送通知失败: {result}", LogLevel.ERROR)

    def start(self, targets: Set[str], event_map: Dict[str, Any]) -> None:
        if self._is_running:
            return

        self._is_running = True
        self.init_notifiers()
        self._last_recreate_time = time.time()
        self._dedup_cache.clear()
        self._task = asyncio.create_task(self._monitor_loop(targets, event_map))
        self._log(f">> 邮件监控服务已启动 (账号: {len(self._notifiers)})")

    async def stop(self) -> None:
        if not self._is_running:
            return

        self._log("<< 正在停止邮件监控服务...")
        self._is_running = False

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=Config.STOP_TIMEOUT)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            finally:
                self._task = None

        for notifier in self._notifiers.values():
            notifier.cleanup()
        self._notifiers.clear()
        self._dedup_cache.clear()
        self._log("<< 邮件监控服务已停止")
