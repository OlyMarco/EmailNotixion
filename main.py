import asyncio
import os
from typing import List, Optional
import yaml

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from .xmail import EmailNotifier


def _load_metadata() -> dict:
    """从metadata.yaml加载插件元数据"""
    try:
        metadata_path = os.path.join(os.path.dirname(__file__), "metadata.yaml")
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        return {"version": "v1.0.5"}  # fallback


_metadata = _load_metadata()


@register(
    _metadata.get("name", "EmailNotixion"),
    _metadata.get("author", "Temmie"),
    _metadata.get("description", "实时 IMAP 邮件推送插件"),
    _metadata.get("version", "v1.0.5"),
    _metadata.get("repo", "https://github.com/OlyMarco/EmailNotixion"),
)
class EmailNotixion(Star):
    """实时IMAP邮件推送插件
    
    支持多账号监控、持久化配置、自动恢复推送状态
    使用异步非阻塞设计，确保不影响机器人性能
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 初始化配置参数
        self._init_config()
        
        # 运行时状态
        self._targets: set[str] = set()
        self._event_map: dict[str, AstrMessageEvent] = {}
        self._notifiers: dict[str, EmailNotifier] = {}
        self._is_running = False
        self._email_task: Optional[asyncio.Task] = None
        
        # 日志初始化信息
        saved_count = len(self.config.get("active_targets", []))
        if saved_count > 0:
            logger.info(f"[EmailNotixion] 检测到 {saved_count} 个保存的目标，等待自动恢复...")
        
        logger.info(f"[EmailNotixion] 初始化完成 (间隔:{self._interval}s 字符上限:{self._text_num})")

    def _init_config(self) -> None:
        """初始化配置参数"""
        # 设置默认值
        for key, default in [("accounts", []), ("interval", 3), ("text_num", 50), ("active_targets", [])]:
            self.config.setdefault(key, default)
        self.config.save_config()
        
        # 应用参数（带保护下限）
        self._interval = max(float(self.config["interval"]), 0.5)
        self._text_num = max(int(self.config["text_num"]), 10)

    # ═══════════════════════ 配置管理 ═══════════════════════

    def _get_accounts(self) -> List[str]:
        """获取配置的邮箱账号列表"""
        return list(self.config.get("accounts", []))

    def _add_account(self, entry: str) -> bool:
        """添加邮箱账号: 'imap_server,email,password'"""
        if not (entry := entry.strip()):
            return False
            
        accounts = self._get_accounts()
        if entry not in accounts:
            accounts.append(entry)
            self._save_accounts(accounts)
            
            # 记录添加的账号（隐藏密码）
            if (parts := entry.split(',')) and len(parts) >= 2:
                logger.info(f"[EmailNotixion] 添加账号: {parts[1].strip()}")
            return True
        return False

    def _del_account(self, user: str) -> bool:
        """删除指定邮箱账号（精确匹配）"""
        if not (user := user.strip()):
            return False
            
        accounts = self._get_accounts()
        original_count = len(accounts)
        
        # 过滤掉匹配的账号
        accounts = [acc for acc in accounts 
                   if not (len(parts := acc.split(',')) >= 2 and parts[1].strip() == user)]
        
        if len(accounts) < original_count:
            self._save_accounts(accounts)
            logger.info(f"[EmailNotixion] 删除账号: {user}")
            return True
        return False

    def _save_accounts(self, accounts: List[str]) -> None:
        """保存邮箱账号列表"""
        self.config["accounts"] = accounts
        self.config.save_config()
        # 如果服务正在运行，重新初始化通知器
        if self._is_running:
            self._init_notifiers()

    def _update_config(self, key: str, value, min_value=None) -> None:
        """统一的配置更新方法"""
        if min_value is not None:
            value = max(value, min_value)
        
        setattr(self, f"_{key}", value)
        self.config[key] = value
        self.config.save_config()
        
        # 如果服务正在运行，重新初始化通知器
        if self._is_running:
            self._init_notifiers()

    def _save_active_targets(self) -> None:
        """保存活跃目标"""
        self.config["active_targets"] = list(self._targets)
        self.config.save_config()

    def _register_event_and_start(self, event: AstrMessageEvent) -> None:
        """注册事件并启动服务"""
        uid = event.unified_msg_origin
        
        if uid not in self._event_map:
            self._event_map[uid] = event
            self._targets.add(uid)
            self._save_active_targets()
            logger.info(f"[EmailNotixion] 注册目标: {uid}")
        
        if not self._is_running and self._targets:
            self._start_email_service()

    # ═══════════════════════ 邮件监控 ═══════════════════════
    
    def _init_notifiers(self) -> None:
        """初始化邮件通知器"""
        self._notifiers.clear()
        accounts = self._get_accounts()
        
        for account in accounts:
            try:
                parts = account.split(',')
                if len(parts) != 3:
                    logger.warning(f"[EmailNotixion] 账号格式错误: {account}")
                    continue
                
                host, user, password = (part.strip() for part in parts)
                notifier = EmailNotifier(host, user, password, logger)
                notifier.text_num = self._text_num
                self._notifiers[user] = notifier
                logger.info(f"[EmailNotixion] 初始化账号: {user}")
                
            except Exception as e:
                logger.error(f"[EmailNotixion] 初始化账号失败 {account}: {e}")

    async def _send_email_notification(self, target_event: AstrMessageEvent, user: str, email_time, subject: str, mail_content: str) -> bool:
        """发送邮件通知到指定目标"""
        try:
            message = f"📧 新邮件通知 ({user})\n"
            if email_time:
                message += f" | 时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            message += f" | 主题: {subject}\n"
            message += f" | 内容: {mail_content}"
            
            chain = MessageChain().message(message)
            await target_event.send(chain)
            return True
            
        except Exception as e:
            logger.error(f"[EmailNotixion] 发送邮件通知失败: {e}")
            return False

    async def _email_monitor_loop(self) -> None:
        """邮件监控循环 - 异步非阻塞设计"""
        while self._is_running:
            try:
                # 并发检查所有账号的新邮件
                check_tasks = [
                    asyncio.to_thread(notifier.check_and_notify)
                    for notifier in self._notifiers.values()
                ]
                
                if check_tasks:
                    # 等待所有邮件检查完成
                    results = await asyncio.gather(*check_tasks, return_exceptions=True)
                    
                    for (user, notifier), result in zip(self._notifiers.items(), results):
                        try:
                            if isinstance(result, Exception):
                                logger.error(f"[EmailNotixion] 检查 {user} 邮件时发生错误: {result}")
                            elif result:
                                email_time, subject, mail_content = result
                                logger.info(f"[EmailNotixion] 检测到 {user} 的新邮件")
                                await self._send_notifications_to_targets(user, email_time, subject, mail_content)
                        except Exception as e:
                            logger.error(f"[EmailNotixion] 处理 {user} 邮件结果时发生错误: {e}")
                
                await asyncio.sleep(self._interval)
                
            except Exception as e:
                logger.error(f"[EmailNotixion] 邮件监控循环错误: {e}")
                await asyncio.sleep(self._interval)

    async def _send_notifications_to_targets(self, user: str, email_time, subject: str, mail_content: str) -> None:
        """异步发送邮件通知到所有目标"""
        if not self._targets:
            return
            
        logger.info(f"[EmailNotixion] 📤 准备发送到 {len(self._targets)} 个目标")
        
        # 创建发送任务列表
        send_tasks = []
        for target in list(self._targets):
            if target_event := self._event_map.get(target):
                platform_name = target_event.get_platform_name()
                logger.debug(f"[EmailNotixion] 📤 向 {target} ({platform_name}) 发送通知")
                
                task = self._send_email_notification(target_event, user, email_time, subject, mail_content)
                send_tasks.append((target, task))
            else:
                logger.warning(f"[EmailNotixion] ⚠ 目标 {target} 没有对应的事件实例")
        
        # 并发发送所有通知
        if send_tasks:
            results = await asyncio.gather(*[task for _, task in send_tasks], return_exceptions=True)
            for (target, _), result in zip(send_tasks, results):
                if isinstance(result, Exception):
                    logger.error(f"[EmailNotixion] 向 {target} 发送通知时发生异常: {result}")
                elif result:
                    logger.debug(f"[EmailNotixion] ✅ 邮件通知已发送到 {target}")
                else:
                    logger.error(f"[EmailNotixion] ❌ 向 {target} 发送通知失败")

    # ═══════════════════════ 指令处理 ═══════════════════════

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _auto_restore_targets(self, event: AstrMessageEvent):
        """自动恢复活跃推送目标的事件监听器"""
        uid = event.unified_msg_origin
        saved_targets = self.config.get("active_targets", [])
        
        # 如果当前用户在保存的目标中，但还没有注册event对象，则自动注册
        if uid in saved_targets and uid not in self._event_map:
            self._event_map[uid] = event
            self._targets.add(uid)
            logger.info(f"[EmailNotixion] 🔄 自动恢复推送目标: {uid}")
            
            # 如果服务还没启动，启动服务
            if not self._is_running and self._targets:
                self._start_email_service()

    @filter.command("email", alias={"mail"})
    async def cmd_email(self, event: AstrMessageEvent, sub: str | None = None, arg: str | None = None):
        """邮件推送插件主指令处理器"""
        uid = event.unified_msg_origin
        action = (sub or "status").lower()

        # 推送间隔设置
        if action == "interval":
            if arg is None:
                yield event.plain_result(f"[EmailNotixion] 当前间隔: {self._interval} 秒")
            else:
                try:
                    sec = float(arg)
                    if sec <= 0:
                        raise ValueError("间隔必须大于0")
                    self._update_config("interval", sec, 0.5)
                    logger.info(f"[EmailNotixion] 推送间隔: {self._interval}s")
                    yield event.plain_result(f"[EmailNotixion] ✅ 间隔已设置为 {sec} 秒")
                except ValueError:
                    yield event.plain_result("请提供有效的正数秒数，如: /email interval 5")
            return

        # 字符上限设置
        if action in {"text", "textnum", "limit"}:
            if arg is None:
                yield event.plain_result(f"[EmailNotixion] 当前字符上限: {self._text_num} 字符")
            else:
                try:
                    num = int(arg)
                    if num < 10:
                        raise ValueError("字符上限不能小于10")
                    self._update_config("text_num", num, 10)
                    logger.info(f"[EmailNotixion] 字符上限: {self._text_num}")
                    yield event.plain_result(f"[EmailNotixion] ✅ 字符上限已设置为 {num} 字符")
                except ValueError:
                    yield event.plain_result("请提供有效的整数（≥10），如: /email text 100")
            return

        # 账号管理
        if action in {"add", "a"}:
            if not arg:
                yield event.plain_result("用法: /email add imap_server,user@domain,password")
                return
                
            if self._add_account(arg):
                yield event.plain_result("[EmailNotixion] ✅ 已添加账号")
            else:
                yield event.plain_result("[EmailNotixion] ❌ 账号已存在或格式错误")
            return

        if action in {"del", "remove", "rm"}:
            if not arg:
                yield event.plain_result("用法: /email del user@domain.com")
                return
                
            if self._del_account(arg):
                yield event.plain_result("[EmailNotixion] ✅ 已删除账号")
            else:
                yield event.plain_result("[EmailNotixion] ❌ 未找到指定账号（需要完整邮箱地址）")
            return

        if action == "list":
            accounts = self._get_accounts()
            if accounts:
                safe_accounts = [f"{acc.split(',')[0]},{acc.split(',')[1]},***" 
                               for acc in accounts if len(acc.split(',')) >= 2]
                text = "当前账号列表:\n" + "\n".join(safe_accounts)
            else:
                text = "当前账号列表:\n<空>"
            yield event.plain_result(text)
            return

        if action == "help":
            current_version = _metadata.get("version", "v1.0.5")
            help_text = f"""[EmailNotixion] 邮件推送插件指令帮助

📧 基本指令：
  /email             查看当前状态
  /email on          开启邮件推送
  /email off         关闭邮件推送
  /email list        查看账号列表
  /email debug       查看调试信息

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
  /email text <字符数>  设置字符上限
    示例: /email text 100
  /email text          查看当前字符上限

💡 优化特性：
  - 异步非阻塞设计，不影响机器人性能
  - 并发处理多账号邮件检查
  - 统一使用 event.send() 发送消息
  - 智能错误处理和自动重连
  - 支持重载插件后自动恢复推送状态
  - 当前版本: {current_version}"""
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
  监控任务: {'运行中' if self._email_task and not self._email_task.done() else '已停止'}
  保存的目标: {self.config.get("active_targets", [])}"""
            
            yield event.plain_result(debug_info)
            return

        # 开关控制
        if action in {"on", "start", "enable"}:
            self._register_event_and_start(event)
            yield event.plain_result(f"[EmailNotixion] ⏳ 邮件推送已开启 (每 {self._interval}s)")
            return

        if action in {"off", "stop", "disable"}:
            if uid in self._targets:
                self._targets.discard(uid)
                self._event_map.pop(uid, None)
                self._save_active_targets()
                
                if not self._targets:
                    await self._stop_email_service()
                yield event.plain_result("[EmailNotixion] ✅ 已关闭邮件推送")
            else:
                yield event.plain_result("[EmailNotixion] ❌ 当前未开启推送")
            return

        # 默认状态显示
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
  /email text <数值>  设置字符上限
  /email interval <秒> 设置推送间隔  
  /email help        查看所有指令"""
        yield event.plain_result(status_text)

    # ═══════════════════════ 服务管理 ═══════════════════════

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

    # ═══════════════════════ 生命周期管理 ═══════════════════════

    async def terminate(self) -> None:
        """插件卸载时的清理工作"""
        logger.info("[EmailNotixion] 🔄 正在卸载插件...")
        await self._stop_email_service()
        logger.info("[EmailNotixion] ✅ 插件已安全卸载")
