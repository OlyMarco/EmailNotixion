import asyncio
from typing import List

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from .xmail import EmailNotifier


@register(
    "EmailNotixion",
    "Temmie",
    "实时 IMAP 邮件推送插件",
    "v1.0.0",
    "https://github.com/OlyMarco/EmailNotixion",
)
class EmailNotixion(Star):
    """EmailNotixion – 实时IMAP邮件推送

    ### 指令 `/email`（`/mail` 别名）
    | 用法 | 说明 |
    |------|------|
    | `/email` | 开/关切换 |
    | `/email on` / `off` | 显式开/关 |
    | `/email add imap,user@domain,password` | 添加账号 |
    | `/email del user@domain.com` | 删除账号（需要完整邮箱地址，精确匹配） |
    | `/email list` | 查看账号列表 |
    | `/email interval <秒>` | 设置推送间隔；不带参数查看当前值 |
    """

    # ─────────────────────────── 初始化 ───────────────────────────

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config: AstrBotConfig = config

        # 确保配置键存在
        self.config.setdefault("accounts", [])
        self.config.setdefault("interval", 3)  # 默认 3 秒
        self.config.save_config()

        self._interval: float = max(float(self.config["interval"]), 0.5)  # 下限 0.5s
        self._targets: set[str] = set()
        self._notifiers: dict[str, EmailNotifier] = {}
        self._is_running: bool = False

        self._email_task = None
        logger.info(f"[EmailNotixion] ⏳ 邮件推送服务已初始化 (interval={self._interval}s)")

    # ──────────────────────── 配置助手 ────────────────────────

    def _get_accounts(self) -> List[str]:
        return list(self.config.get("accounts", []))

    def _set_accounts(self, accounts: List[str]):
        self.config["accounts"] = accounts
        self.config.save_config()

    def _add_account(self, entry: str) -> bool:
        """
        添加邮箱账号配置
        
        Args:
            entry: 账号配置字符串，格式为 "imap_server,email,password"
            
        Returns:
            bool: 添加成功返回 True，账号已存在返回 False
            
        Note:
            ⚠️ 安全警告：密码将以明文形式存储在配置文件中
        """
        entry = entry.strip()
        if not entry:
            return False
        accounts = self._get_accounts()
        if entry not in accounts:
            accounts.append(entry)
            self._set_accounts(accounts)
            # 解析并记录添加的账号（不记录密码）
            parts = entry.split(',')
            if len(parts) >= 2:
                logger.info(f"[EmailNotixion] 添加账号: {parts[1].strip()}")
            return True
        return False

    def _del_account(self, user: str) -> bool:
        """
        删除指定的邮箱账号
        
        Args:
            user: 完整的邮箱地址（如 user@domain.com）
            
        Returns:
            bool: 删除成功返回 True，未找到账号返回 False
            
        Note:
            使用精确匹配，只会删除完全匹配的邮箱账号
        """
        user = user.strip()
        accounts = self._get_accounts()
        # 精确匹配：检查账号配置中的用户部分是否完全匹配
        new_accounts = []
        found = False
        for account in accounts:
            parts = account.split(',')
            if len(parts) >= 2 and parts[1].strip() == user:
                found = True  # 找到匹配的账号，跳过它（即删除）
                logger.info(f"[EmailNotixion] 删除账号: {user}")
            else:
                new_accounts.append(account)  # 保留不匹配的账号
        
        if found:
            self._set_accounts(new_accounts)
            return True
        return False

    def _set_interval(self, seconds: float):
        self._interval = max(seconds, 0.5)
        self.config["interval"] = self._interval
        self.config.save_config()
        logger.info(f"[EmailNotixion] ⏱ 推送间隔更新为 {self._interval}s")

    async def _send_email_notification(self, target: str, user: str, email_time, subject: str, first_line: str):
        """
        发送邮件通知到指定目标
        
        Args:
            target: 目标群组或用户ID
            user: 邮箱地址
            email_time: 邮件时间
            subject: 邮件主题
            first_line: 邮件内容第一行
        """
        message = f"📧 新邮件通知 ({user})\n"
        if email_time:
            message += f"时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        message += f"主题: {subject}\n"
        message += f"内容: {first_line}"
        
        chain = MessageChain().message(message)
        await self.context.send_message(target, chain)

    def _init_notifiers(self):
        """
        初始化邮件通知器
        
        从配置中读取账号信息并创建对应的 EmailNotifier 实例
        """
        self._notifiers.clear()
        accounts = self._get_accounts()
        
        for account in accounts:
            try:
                parts = account.split(',')
                if len(parts) != 3:
                    logger.warning(f"[EmailNotixion] 账号格式错误，应为 'imap,user@domain,password': {account}")
                    continue
                
                host, user, password = parts
                notifier = EmailNotifier(host.strip(), user.strip(), password.strip(), logger)
                self._notifiers[user.strip()] = notifier
                logger.info(f"[EmailNotixion] 已初始化账号: {user.strip()}")
            except Exception as e:
                logger.error(f"[EmailNotixion] 初始化账号失败 {account}: {e}")

    async def _email_monitor_loop(self):
        """邮件监控循环"""
        while self._is_running:
            try:
                # 检查所有账号的新邮件
                for user, notifier in self._notifiers.items():
                    # 使用 asyncio.to_thread 避免阻塞事件循环
                    notification = await asyncio.to_thread(notifier.check_and_notify)
                    if notification:
                        email_time, subject, first_line = notification
                        
                        # 发送到所有目标群组/用户
                        for target in list(self._targets):
                            try:
                                await self._send_email_notification(target, user, email_time, subject, first_line)
                                logger.debug(f"[EmailNotixion] ▶ 邮件通知已发送到 {target}")
                            except Exception as e:
                                logger.error(f"[EmailNotixion] 发送消息失败到 {target}: {e}")
                
                await asyncio.sleep(self._interval)
                
            except Exception as e:
                logger.error(f"[EmailNotixion] 邮件监控循环错误: {e}")
                await asyncio.sleep(self._interval)

    # ───────────────────────── `/email` 指令 ─────────────────────────

    @filter.command("email", alias={"mail"})
    async def cmd_email(self, event: AstrMessageEvent, sub: str | None = None, arg: str | None = None):
        uid = event.unified_msg_origin
        action = (sub or "toggle").lower()

        # ── interval 设置 ──
        if action == "interval":
            if arg is None:
                yield event.plain_result(f"[EmailNotixion] 当前间隔: {self._interval} 秒")
            else:
                try:
                    sec = float(arg)
                    if sec <= 0:
                        raise ValueError
                    self._set_interval(sec)
                    yield event.plain_result(f"[EmailNotixion] ✅ 间隔已设置为 {sec} 秒")
                except ValueError:
                    yield event.plain_result("请提供正数秒数，如 /email interval 5")
            return

        # ── 账号管理 ──
        if action in {"add", "a"}:
            if arg and self._add_account(arg):
                # 如果服务正在运行，重新加载通知器
                if self._is_running:
                    self._init_notifiers()
                yield event.plain_result("[EmailNotixion] 已添加账号 ✅")
            else:
                yield event.plain_result("用法: /email add imap,user@domain,password (或账号已存在)")
            return

        if action in {"del", "remove"}:
            if arg and self._del_account(arg):
                # 如果服务正在运行，重新加载通知器
                if self._is_running:
                    self._init_notifiers()
                yield event.plain_result("[EmailNotixion] 已删除账号 ✅")
            else:
                yield event.plain_result("用法: /email del user@domain.com (需要完整邮箱地址，或未找到账号)")
            return

        if action == "list":
            accounts = self._get_accounts()
            text = "当前账号列表:\n" + ("\n".join(accounts) if accounts else "<空>")
            yield event.plain_result(text)
            return

        # ── 开关控制 ──
        if action in {"on", "start", "enable"}:
            self._targets.add(uid)
            if not self._is_running:
                self._start_email_service()
            yield event.plain_result(f"[EmailNotixion] ⏳ 邮件推送已开启 (每 {self._interval}s)")
            return

        if action in {"off", "stop", "disable"}:
            if uid in self._targets:
                self._targets.discard(uid)
                if not self._targets:
                    await self._stop_email_service()
                yield event.plain_result("[EmailNotixion] ✅ 已关闭邮件推送")
            else:
                yield event.plain_result("[EmailNotixion] 未开启，无需关闭")
            return

        # toggle (默认)
        if uid in self._targets:
            self._targets.discard(uid)
            if not self._targets:
                await self._stop_email_service()
            yield event.plain_result("[EmailNotixion] ✅ 已关闭邮件推送")
        else:
            self._targets.add(uid)
            if not self._is_running:
                self._start_email_service()
            yield event.plain_result(f"[EmailNotixion] ⏳ 已开启邮件推送 (每 {self._interval}s)")

    # ───────────────────────── 服务管理 ─────────────────────────

    def _start_email_service(self):
        """启动邮件推送服务"""
        if self._is_running:
            return
        
        self._is_running = True
        self._init_notifiers()
        
        # 启动邮件监控任务
        self._email_task = asyncio.create_task(self._email_monitor_loop())
        logger.info("[EmailNotixion] 邮件推送服务已启动")

    async def _stop_email_service(self):
        """停止邮件推送服务"""
        if not self._is_running:
            return
        
        self._is_running = False
        
        # 取消并等待邮件监控任务完成
        if self._email_task:
            self._email_task.cancel()
            try:
                await self._email_task
            except asyncio.CancelledError:
                pass  # 任务被正常取消
            self._email_task = None
        
        # 清理邮件通知器 - 使用 asyncio.to_thread 避免阻塞
        async def cleanup_notifier(notifier):
            if notifier.mail:
                try:
                    await asyncio.to_thread(notifier.mail.logout)
                except Exception:
                    pass
        
        # 并发清理所有通知器
        cleanup_tasks = [cleanup_notifier(notifier) for notifier in self._notifiers.values()]
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        
        self._notifiers.clear()
        logger.info("[EmailNotixion] 邮件推送服务已停止")

    # ───────────────────────── 卸载清理 ─────────────────────────

    async def terminate(self):
        """插件卸载时的清理工作"""
        await self._stop_email_service()
