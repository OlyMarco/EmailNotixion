import asyncio
from typing import List, Optional

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from .xmail import EmailNotifier


@register(
    "EmailNotixion",
    "Temmie",
    "实时 IMAP 邮件推送插件",
    "v1.0.4",
    "https://github.com/OlyMarco/EmailNotixion",
)
class EmailNotixion(Star):
    """EmailNotixion – 实时IMAP邮件推送

    ### 指令 `/email`（`/mail` 别名）
    | 用法 | 说明 |
    |------|------|
    | `/email` | 显示当前状态 |
    | `/email on` / `off` | 显式开/关 |
    | `/email add imap,user@domain,password` | 添加账号 |
    | `/email del user@domain.com` | 删除账号（需要完整邮箱地址，精确匹配） |
    | `/email list` | 查看账号列表 |
    | `/email interval <秒>` | 设置推送间隔；不带参数查看当前值 |
    | `/email help` | 查看详细帮助信息 |
    | `/email debug` | 查看调试信息 |
    
    特点：
    - 统一使用 event.send() 方法发送消息到所有平台
    - 支持多账号同时监控
    - 自动管理IMAP连接和清理
    """

    # ─────────────────────────── 初始化 ───────────────────────────

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config: AstrBotConfig = config

        # 确保配置键存在并设置默认值
        self.config.setdefault("accounts", [])
        self.config.setdefault("interval", 3)  # 默认 3 秒
        self.config.setdefault("text_num", 50)  # 默认 50 字符
        self.config.save_config()

        # 配置参数（带下限保护）
        self._interval: float = max(float(self.config["interval"]), 0.5)  # 下限 0.5s
        self._text_num: int = max(int(self.config["text_num"]), 10)  # 下限 10 字符
        
        # 运行时状态
        self._targets: set[str] = set()  # 活跃推送目标
        self._event_map: dict[str, AstrMessageEvent] = {}  # UID -> Event 映射表
        self._notifiers: dict[str, EmailNotifier] = {}  # 邮件通知器实例
        self._is_running: bool = False  # 服务运行状态
        self._email_task: Optional[asyncio.Task] = None  # 邮件监控任务

        logger.info(f"[EmailNotixion] ⏳ 邮件推送服务已初始化 (interval={self._interval}s)")

    # ──────────────────────── 配置管理 ────────────────────────

    def _get_accounts(self) -> List[str]:
        """获取当前配置的邮箱账号列表"""
        return list(self.config.get("accounts", []))

    def _set_accounts(self, accounts: List[str]) -> None:
        """保存邮箱账号列表到配置"""
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
            
            # 记录添加的账号（不记录密码）
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
        if not user:
            return False
            
        accounts = self._get_accounts()
        new_accounts = []
        found = False
        
        for account in accounts:
            parts = account.split(',')
            if len(parts) >= 2 and parts[1].strip() == user:
                found = True
                logger.info(f"[EmailNotixion] 删除账号: {user}")
            else:
                new_accounts.append(account)
        
        if found:
            self._set_accounts(new_accounts)
        return found

    def _set_interval(self, seconds: float) -> None:
        """设置推送间隔并保存到配置"""
        self._interval = max(seconds, 0.5)
        self.config["interval"] = self._interval
        self.config.save_config()
        logger.info(f"[EmailNotixion] ⏱ 推送间隔更新为 {self._interval}s")

    # ──────────────────────── 邮件处理 ────────────────────────
    def _init_notifiers(self) -> None:
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
                
                host, user, password = (part.strip() for part in parts)
                notifier = EmailNotifier(host, user, password, logger)
                notifier.text_num = self._text_num  # 设置文本长度限制
                self._notifiers[user] = notifier
                logger.info(f"[EmailNotixion] 已初始化账号: {user}")
                
            except Exception as e:
                logger.error(f"[EmailNotixion] 初始化账号失败 {account}: {e}")

    async def _send_email_notification(self, target_event: AstrMessageEvent, user: str, email_time, subject: str, mail_content: str) -> bool:
        """
        发送邮件通知到指定目标
        
        使用 event.send() 方法统一发送消息到各个平台
        
        Args:
            target_event: 目标会话的事件实例
            user: 邮箱地址
            email_time: 邮件时间
            subject: 邮件主题
            mail_content: 邮件内容
            
        Returns:
            bool: 发送成功返回 True，失败返回 False
        """
        try:
            # 构建邮件通知消息
            message = f"📧 新邮件通知 ({user})\n"
            if email_time:
                message += f" | 时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            message += f" | 主题: {subject}\n"
            message += f" | 内容: {mail_content}"
            
            # 发送消息
            chain = MessageChain().message(message)
            await target_event.send(chain)
            return True
            
        except Exception as e:
            logger.error(f"[EmailNotixion] 发送邮件通知失败: {e}")
            return False

    async def _email_monitor_loop(self) -> None:
        """
        邮件监控循环 - 异步非阻塞设计
        
        采用 asyncio.to_thread() 将同步的邮件检查操作在线程池中执行，
        确保不会阻塞主事件循环
        """
        while self._is_running:
            try:
                # 并发检查所有账号的新邮件
                check_tasks = []
                for user, notifier in self._notifiers.items():
                    # 将同步的邮件检查操作包装为异步任务
                    task = asyncio.to_thread(notifier.check_and_notify)
                    check_tasks.append((user, task))
                
                # 等待所有邮件检查完成（并发执行）
                for user, task in check_tasks:
                    try:
                        notification = await task
                        if notification:
                            email_time, subject, mail_content = notification
                            logger.info(f"[EmailNotixion] � 检测到 {user} 的新邮件")
                            
                            # 异步发送到所有目标
                            await self._send_notifications_to_targets(user, email_time, subject, mail_content)
                            
                    except Exception as e:
                        logger.error(f"[EmailNotixion] 检查 {user} 邮件时发生错误: {e}")
                
                # 异步等待，不阻塞事件循环
                await asyncio.sleep(self._interval)
                
            except Exception as e:
                logger.error(f"[EmailNotixion] 邮件监控循环错误: {e}")
                await asyncio.sleep(self._interval)

    async def _send_notifications_to_targets(self, user: str, email_time, subject: str, mail_content: str) -> None:
        """
        异步发送邮件通知到所有目标
        
        Args:
            user: 邮箱地址
            email_time: 邮件时间
            subject: 邮件主题
            mail_content: 邮件内容
        """
        if not self._targets:
            return
            
        logger.info(f"[EmailNotixion] 📤 准备发送到 {len(self._targets)} 个目标")
        
        # 创建发送任务列表
        send_tasks = []
        for target in list(self._targets):
            target_event = self._event_map.get(target)
            if target_event:
                platform_name = target_event.get_platform_name()
                logger.debug(f"[EmailNotixion] 📤 向 {target} ({platform_name}) 发送通知")
                
                # 创建异步发送任务
                task = self._send_email_notification(target_event, user, email_time, subject, mail_content)
                send_tasks.append((target, task))
            else:
                logger.warning(f"[EmailNotixion] ⚠ 目标 {target} 没有对应的事件实例")
        
        # 并发发送所有通知
        for target, task in send_tasks:
            try:
                success = await task
                if success:
                    logger.debug(f"[EmailNotixion] ✅ 邮件通知已发送到 {target}")
                else:
                    logger.error(f"[EmailNotixion] ❌ 向 {target} 发送通知失败")
            except Exception as e:
                logger.error(f"[EmailNotixion] 向 {target} 发送通知时发生异常: {e}")

    # ──────────────────────── 指令处理 ────────────────────────

    @filter.command("email", alias={"mail"})
    async def cmd_email(self, event: AstrMessageEvent, sub: str | None = None, arg: str | None = None):
        """邮件推送插件主指令处理器"""
        uid = event.unified_msg_origin
        action = (sub or "status").lower()

        # ── 推送间隔设置 ──
        if action == "interval":
            if arg is None:
                yield event.plain_result(f"[EmailNotixion] 当前间隔: {self._interval} 秒")
            else:
                try:
                    sec = float(arg)
                    if sec <= 0:
                        raise ValueError("间隔必须大于0")
                    self._set_interval(sec)
                    yield event.plain_result(f"[EmailNotixion] ✅ 间隔已设置为 {sec} 秒")
                except ValueError:
                    yield event.plain_result("请提供有效的正数秒数，如: /email interval 5")
            return

        # ── 账号管理 ──
        if action in {"add", "a"}:
            if not arg:
                yield event.plain_result("用法: /email add imap_server,user@domain,password")
                return
                
            if self._add_account(arg):
                # 如果服务正在运行，重新初始化通知器
                if self._is_running:
                    self._init_notifiers()
                yield event.plain_result("[EmailNotixion] ✅ 已添加账号")
            else:
                yield event.plain_result("[EmailNotixion] ❌ 账号已存在或格式错误")
            return

        if action in {"del", "remove", "rm"}:
            if not arg:
                yield event.plain_result("用法: /email del user@domain.com")
                return
                
            if self._del_account(arg):
                # 如果服务正在运行，重新初始化通知器
                if self._is_running:
                    self._init_notifiers()
                yield event.plain_result("[EmailNotixion] ✅ 已删除账号")
            else:
                yield event.plain_result("[EmailNotixion] ❌ 未找到指定账号（需要完整邮箱地址）")
            return

        if action == "list":
            accounts = self._get_accounts()
            if accounts:
                # 隐藏密码信息
                safe_accounts = []
                for account in accounts:
                    parts = account.split(',')
                    if len(parts) >= 2:
                        safe_accounts.append(f"{parts[0]},{parts[1]},***")
                text = "当前账号列表:\n" + "\n".join(safe_accounts)
            else:
                text = "当前账号列表:\n<空>"
            yield event.plain_result(text)
            return

        if action == "help":
            help_text = """[EmailNotixion] 邮件推送插件指令帮助

📧 基本指令：
  /email             查看当前状态
  /email on          开启邮件推送
  /email off         关闭邮件推送
  /email list        查看账号列表

⚙️ 账号管理：
  /email add <配置>   添加邮箱账号
    格式: imap_server,email,password
    示例: /email add imap.gmail.com,test@gmail.com,app_password
  /email del <邮箱>   删除邮箱账号
    示例: /email del test@gmail.com

🔧 设置选项：
  /email interval <秒>  设置推送间隔
    示例: /email interval 5
  /email interval      查看当前间隔

💡 优化特性：
  - 异步非阻塞设计，不影响机器人性能
  - 并发处理多账号邮件检查
  - 统一使用 event.send() 发送消息
  - 智能错误处理和自动重连
  - 当前版本: v1.0.4"""
            yield event.plain_result(help_text)
            return
        
        if action == "debug":
            debug_info = f"""[EmailNotixion] 调试信息

🎯 目标信息：
  活跃目标数量: {len(self._targets)}
  目标列表: {list(self._targets)}

📱 事件映射：
  映射表大小: {len(self._event_map)}"""
            
            for target_uid, event_obj in self._event_map.items():
                platform_name = event_obj.get_platform_name()
                debug_info += f"\n  {target_uid}: {platform_name}"
            
            debug_info += f"""

⚡ 运行状态：
  服务运行: {self._is_running}
  账号数量: {len(self._notifiers)}
  监控任务: {'运行中' if self._email_task and not self._email_task.done() else '已停止'}"""
            
            yield event.plain_result(debug_info)
            return

        # ── 开关控制 ──
        if action in {"on", "start", "enable"}:
            self._targets.add(uid)
            self._event_map[uid] = event  # 记录事件实例
            
            if not self._is_running:
                self._start_email_service()
            yield event.plain_result(f"[EmailNotixion] ⏳ 邮件推送已开启 (每 {self._interval}s)")
            return

        if action in {"off", "stop", "disable"}:
            if uid in self._targets:
                self._targets.discard(uid)
                self._event_map.pop(uid, None)  # 清理事件映射
                
                if not self._targets:  # 如果没有活跃目标，停止服务
                    await self._stop_email_service()
                yield event.plain_result("[EmailNotixion] ✅ 已关闭邮件推送")
            else:
                yield event.plain_result("[EmailNotixion] ❌ 当前未开启推送")
            return

        # ── 默认状态显示 ──
        status = "启用" if self._is_running else "禁用"
        active_targets = len(self._targets)
        accounts_count = len(self._get_accounts())
        task_status = "运行中" if self._email_task and not self._email_task.done() else "已停止"
        
        status_text = f"""[EmailNotixion] 当前状态

📊 运行状态: {status}
👥 活跃目标: {active_targets} 个
📧 配置账号: {accounts_count} 个
⏱️ 检查间隔: {self._interval} 秒
📝 字符上限: {self._text_num} 字符
🔄 监控任务: {task_status}

💡 快速指令:
  /email on/off      开启/关闭推送
  /email add <配置>   添加账号
  /email help        查看所有指令"""
        yield event.plain_result(status_text)

    # ──────────────────────── 服务管理 ────────────────────────

    def _start_email_service(self) -> None:
        """启动邮件推送服务"""
        if self._is_running:
            return
        
        self._is_running = True
        self._init_notifiers()
        
        # 启动异步邮件监控任务
        self._email_task = asyncio.create_task(self._email_monitor_loop())
        logger.info("[EmailNotixion] 🚀 邮件推送服务已启动")

    async def _stop_email_service(self) -> None:
        """停止邮件推送服务并清理资源"""
        if not self._is_running:
            return
        
        self._is_running = False
        
        # 取消并等待邮件监控任务完成
        if self._email_task and not self._email_task.done():
            self._email_task.cancel()
            try:
                await self._email_task
            except asyncio.CancelledError:
                pass  # 正常取消
            self._email_task = None
        
        # 异步清理邮件通知器连接
        if self._notifiers:
            logger.info("[EmailNotixion] 🧹 正在清理邮件连接...")
            cleanup_tasks = []
            
            for user, notifier in self._notifiers.items():
                if notifier.mail:
                    # 使用 asyncio.to_thread 异步执行同步的注销操作
                    task = asyncio.to_thread(self._safe_logout, notifier)
                    cleanup_tasks.append(task)
            
            # 并发执行所有清理任务
            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        
        self._notifiers.clear()
        logger.info("[EmailNotixion] ✅ 邮件推送服务已停止")

    def _safe_logout(self, notifier: EmailNotifier) -> None:
        """安全地注销邮件连接（同步方法，用于在线程中执行）"""
        try:
            if notifier.mail:
                notifier.mail.logout()
        except Exception as e:
            logger.debug(f"[EmailNotixion] 注销邮件连接时出现异常（可忽略）: {e}")

    # ──────────────────────── 生命周期管理 ────────────────────────

    async def terminate(self) -> None:
        """插件卸载时的清理工作"""
        logger.info("[EmailNotixion] 🔄 正在卸载插件...")
        await self._stop_email_service()
        logger.info("[EmailNotixion] ✅ 插件已安全卸载")
