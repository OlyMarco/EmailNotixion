import asyncio
import os
import time
from typing import List, Optional, Dict, Set
import yaml

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from .xmail import EmailNotifier


def _load_metadata() -> dict:
    """📦 加载插件元数据"""
    try:
        metadata_path = os.path.join(os.path.dirname(__file__), "metadata.yaml")
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        return {"version": "v1.0.7"}


_metadata = _load_metadata()


@register(
    _metadata.get("name", "EmailNotixion"),
    _metadata.get("author", "Temmie"),
    _metadata.get("description", "📧 实时 IMAP 邮件推送插件"),
    _metadata.get("version", "v1.0.7"),
    _metadata.get("repo", "https://github.com/OlyMarco/EmailNotixion"),
)
class EmailNotixion(Star):
    """📧 实时 IMAP 邮件推送插件
    
    ✨ 功能特性:
    • 多账号并发监控
    • 异步非阻塞设计  
    • 持久化配置管理
    • 自动恢复推送状态
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 📋 初始化配置参数
        self._init_config()
        
        # 🔄 运行时状态
        self._targets: Set[str] = set()
        self._event_map: Dict[str, AstrMessageEvent] = {}
        self._notifiers: Dict[str, EmailNotifier] = {}
        self._is_running = False
        self._email_task: Optional[asyncio.Task] = None
        self._last_recreate_time = 0
        self._recreate_interval = 120  # 2分钟重建连接
        
        # 📊 启动状态日志
        saved_count = len(self.config.get("active_targets", []))
        if saved_count > 0:
            logger.info(f"[EmailNotixion] 🔄 检测到 {saved_count} 个保存的推送目标，等待自动恢复...")
        
        valid_accounts = len(self._get_valid_accounts())
        total_accounts = len(self.config.get("accounts", []))
        logger.info(f"[EmailNotixion] ✅ 插件初始化完成 (有效账号: {valid_accounts}/{total_accounts}, 间隔: {self._interval}s, 字符上限: {self._text_num})")

    def _init_config(self) -> None:
        """📋 初始化配置参数并设置默认值"""
        defaults = {
            "accounts": [], 
            "interval": 3, 
            "text_num": 50, 
            "active_targets": []
        }
        for key, default in defaults.items():
            self.config.setdefault(key, default)
        self.config.save_config()
        
        # 📊 应用配置（带保护下限）
        self._interval = max(float(self.config["interval"]), 0.5)
        self._text_num = max(int(self.config["text_num"]), 10)

    # ═══════════════════════ 配置管理 ═══════════════════════

    def _get_accounts(self) -> List[str]:
        """📧 获取配置的邮箱账号列表"""
        return list(self.config.get("accounts", []))

    def _get_valid_accounts(self) -> List[str]:
        """✅ 获取有效的邮箱账号列表（通过IMAP连接测试）"""
        accounts = self._get_accounts()
        valid_accounts = []
        
        for account in accounts:
            parts = account.split(',')
            if len(parts) == 3 and all(part.strip() for part in parts):
                try:
                    host, user, password = (part.strip() for part in parts)
                    # 创建临时通知器进行连接测试
                    test_notifier = EmailNotifier(host, user, password, logger)
                    if test_notifier.test_connection():
                        valid_accounts.append(account)
                except Exception:
                    # 连接测试失败，跳过此账号
                    continue
        
        return valid_accounts

    def _add_account(self, entry: str) -> bool:
        """➕ 添加邮箱账号: 'imap_server,email,password'"""
        if not (entry := entry.strip()):
            return False
            
        accounts = self._get_accounts()
        if entry not in accounts:
            accounts.append(entry)
            self._save_accounts(accounts)
            
            # 记录添加的账号（隐藏密码）
            if (parts := entry.split(',')) and len(parts) >= 2:
                logger.info(f"[EmailNotixion] ➕ 添加账号: {parts[1].strip()}")
            return True
        return False

    def _del_account(self, user: str) -> bool:
        """🗑️ 删除指定邮箱账号"""
        if not (user := user.strip()):
            return False
            
        accounts = self._get_accounts()
        original_count = len(accounts)
        
        accounts = [acc for acc in accounts 
                   if not (len(parts := acc.split(',')) >= 2 and parts[1].strip() == user)]
        
        if len(accounts) < original_count:
            self._save_accounts(accounts)
            logger.info(f"[EmailNotixion] 🗑️ 删除账号: {user}")
            return True
        return False

    def _save_accounts(self, accounts: List[str]) -> None:
        """💾 保存邮箱账号列表并重新初始化通知器"""
        self.config["accounts"] = accounts
        self.config.save_config()
        if self._is_running:
            self._init_notifiers()

    def _update_config(self, key: str, value, min_value=None) -> None:
        """⚙️ 更新配置项"""
        if min_value is not None:
            value = max(value, min_value)
        
        setattr(self, f"_{key}", value)
        self.config[key] = value
        self.config.save_config()
        
        if self._is_running:
            self._init_notifiers()

    def _save_active_targets(self) -> None:
        """💾 保存活跃目标"""
        self.config["active_targets"] = list(self._targets)
        self.config.save_config()

    def _register_event_and_start(self, event: AstrMessageEvent) -> None:
        """📝 注册事件并启动服务"""
        uid = event.unified_msg_origin
        
        if uid not in self._event_map:
            self._event_map[uid] = event
            self._targets.add(uid)
            self._save_active_targets()
            logger.info(f"[EmailNotixion] 📝 注册目标: {uid}")
        
        if not self._is_running and self._targets:
            self._start_email_service()

    # ═══════════════════════ 邮件监控 ═══════════════════════
    
    def _init_notifiers(self) -> None:
        """🔧 初始化邮件通知器"""
        self._notifiers.clear()
        valid_accounts = self._get_valid_accounts()
        
        for account in valid_accounts:
            try:
                parts = account.split(',')
                host, user, password = (part.strip() for part in parts)
                notifier = EmailNotifier(host, user, password, logger)
                notifier.text_num = self._text_num
                self._notifiers[user] = notifier
                
            except Exception as e:
                logger.error(f"[EmailNotixion] ❌ 初始化账号失败 {account}: {e}")

    async def _send_email_notification(self, target_event: AstrMessageEvent, user: str, 
                                     email_time, subject: str, mail_content: str) -> bool:
        """📤 发送邮件通知到指定目标"""
        try:
            message = f"📧 新邮件通知 ({user})\n"
            if email_time:
                message += f"⏰ 时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            message += f"📋 主题: {subject}\n📄 内容: {mail_content}"
            
            chain = MessageChain().message(message)
            await target_event.send(chain)
            return True
            
        except Exception as e:
            logger.error(f"[EmailNotixion] ❌ 发送邮件通知失败: {e}")
            return False

    async def _email_monitor_loop(self) -> None:
        """🔄 邮件监控循环"""
        while self._is_running:
            try:
                current_time = time.time()
                
                # 每2分钟重建所有邮箱连接
                if current_time - self._last_recreate_time > self._recreate_interval:
                    self._init_notifiers()
                    self._last_recreate_time = current_time
                
                # 检查邮件 - 30秒超时
                if self._notifiers:
                    check_tasks = [
                        asyncio.wait_for(
                            asyncio.to_thread(notifier.check_and_notify),
                            timeout=30
                        )
                        for notifier in self._notifiers.values()
                    ]
                    
                    results = await asyncio.gather(*check_tasks, return_exceptions=True)
                    
                    for (user, notifier), result in zip(self._notifiers.items(), results):
                        if isinstance(result, asyncio.TimeoutError):
                            logger.warning(f"[EmailNotixion] ⏰ {user} 检查超时")
                        elif isinstance(result, Exception):
                            logger.error(f"[EmailNotixion] ❌ {user} 检查错误: {result}")
                        elif result:
                            # 处理邮件
                            if isinstance(result, list):
                                logger.info(f"[EmailNotixion] 📧 {user} 收到 {len(result)} 封新邮件")
                                for email_time, subject, mail_content in result:
                                    await self._send_notifications_to_targets(user, email_time, subject, mail_content)
                            else:
                                email_time, subject, mail_content = result
                                logger.info(f"[EmailNotixion] 📧 {user} 收到新邮件")
                                await self._send_notifications_to_targets(user, email_time, subject, mail_content)
                
                await asyncio.sleep(self._interval)
                
            except Exception as e:
                logger.error(f"[EmailNotixion] ❌ 监控循环错误: {e}")
                await asyncio.sleep(self._interval)

    async def _send_notifications_to_targets(self, user: str, email_time, subject: str, mail_content: str) -> None:
        """发送邮件通知到所有目标"""
        if not self._targets:
            return
            
        logger.info(f"[EmailNotixion] 📤 准备发送邮件通知到 {len(self._targets)} 个目标")
        
        # 创建发送任务列表
        send_tasks = []
        for target in list(self._targets):
            if target_event := self._event_map.get(target):
                platform_name = target_event.get_platform_name()
                logger.debug(f"[EmailNotixion] 📤 向 {target} ({platform_name}) 发送通知")
                
                task = self._send_email_notification(target_event, user, email_time, subject, mail_content)
                send_tasks.append((target, task))
            else:
                logger.warning(f"[EmailNotixion] ⚠️ 目标 {target} 没有对应的事件实例")
        
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

        # ═══════════════════════ 指令处理 ═══════════════════════

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _auto_restore_targets(self, event: AstrMessageEvent):
        """🔄 自动恢复活跃推送目标的事件监听器"""
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
        """📧 邮件推送插件主指令处理器"""
        uid = event.unified_msg_origin
        action = (sub or "status").lower()

        # ⚙️ 配置设置指令
        if action == "interval":
            if arg is None:
                yield event.plain_result(f"📊 当前间隔: {self._interval} 秒")
            else:
                try:
                    sec = float(arg)
                    if sec <= 0:
                        raise ValueError("间隔必须大于0")
                    self._update_config("interval", sec, 0.5)
                    logger.info(f"[EmailNotixion] ⚙️ 推送间隔: {self._interval}s")
                    yield event.plain_result(f"✅ 间隔已设置为 {sec} 秒")
                except ValueError:
                    yield event.plain_result("❌ 请提供有效的正数秒数，如: /email interval 5")
            return

        if action in {"text", "textnum", "limit"}:
            if arg is None:
                yield event.plain_result(f"📊 当前字符上限: {self._text_num} 字符")
            else:
                try:
                    num = int(arg)
                    if num < 10:
                        raise ValueError("字符上限不能小于10")
                    self._update_config("text_num", num, 10)
                    logger.info(f"[EmailNotixion] ⚙️ 字符上限: {self._text_num}")
                    yield event.plain_result(f"✅ 字符上限已设置为 {num} 字符")
                except ValueError:
                    yield event.plain_result("❌ 请提供有效的整数（≥10），如: /email text 100")
            return

        # 📧 账号管理指令
        if action in {"add", "a"}:
            if not arg:
                yield event.plain_result("📝 用法: /email add imap_server,user@domain,password")
                return
                
            if self._add_account(arg):
                yield event.plain_result("✅ 已添加邮箱账号")
            else:
                yield event.plain_result("❌ 账号已存在或格式错误")
            return

        if action in {"del", "remove", "rm"}:
            if not arg:
                yield event.plain_result("📝 用法: /email del user@domain.com")
                return
                
            if self._del_account(arg):
                yield event.plain_result("✅ 已删除邮箱账号")
            else:
                yield event.plain_result("❌ 未找到指定账号（需要完整邮箱地址）")
            return

        if action == "list":
            accounts = self._get_accounts()
            
            if accounts:
                account_list = []
                valid_accounts = self._get_valid_accounts()
                
                for acc in accounts:
                    parts = acc.split(',')
                    if len(parts) >= 2:
                        email = parts[1].strip()
                        if acc in valid_accounts:
                            status = "✅ 连接正常"
                        elif len(parts) != 3 or not all(part.strip() for part in parts):
                            status = "❌ 格式错误"
                        else:
                            status = "❌ 连接失败"
                        account_list.append(f"  {email} - {status}")
                    else:
                        account_list.append(f"  {acc} - ❌ 格式错误")
                
                text = f"📧 账号列表 ({len(valid_accounts)}/{len(accounts)} 有效):\n" + "\n".join(account_list)
            else:
                text = "📧 账号列表: 无配置账号"
            yield event.plain_result(text)
            return

        # 📚 帮助和调试指令
        if action == "help":
            current_version = _metadata.get("version", "v1.0.7")
            help_text = f"""📧 EmailNotixion 邮件推送插件 {current_version}

🖥️ 基本指令:
  /email             查看当前状态
  /email on          开启当前会话推送
  /email off         关闭当前会话推送
  /email list        查看邮箱账号状态
  /email debug       查看详细调试信息
  /email reinit      手动重建所有连接

⚙️ 账号管理:
  /email add <配置>   添加邮箱账号
    格式: imap服务器,邮箱地址,应用密码
    示例: /email add imap.gmail.com,test@gmail.com,app_password
  /email del <邮箱>   删除指定邮箱账号
    示例: /email del test@gmail.com

🔧 参数设置:
  /email interval <秒>  设置邮件检查间隔 (最小0.5秒)
    示例: /email interval 5
  /email text <字符数>  设置邮件内容字符上限 (最小10字符)
    示例: /email text 100

✨ 功能特性:
  • 异步非阻塞设计，不影响机器人性能
  • 多账号并发监控，30秒超时保护
  • 智能未读邮件检测，避免邮件丢失
  • 每2分钟自动重建连接，确保稳定性
  • 会话级推送控制，支持多平台同时使用
  • 插件重载后自动恢复推送状态
  • 智能HTML转文本，支持多种邮件格式"""
            yield event.plain_result(help_text)
            return
        
        if action == "debug":
            valid_accounts = self._get_valid_accounts()
            total_accounts = len(self._get_accounts())
            
            debug_info = f"""📊 EmailNotixion 调试信息

🎯 会话目标信息:
  活跃推送目标: {len(self._targets)} 个
  目标列表: {list(self._targets)}

📱 事件映射表:
  映射表大小: {len(self._event_map)} 个"""
            
            for target_uid, event_obj in self._event_map.items():
                platform_name = event_obj.get_platform_name()
                debug_info += f"\n  {target_uid}: {platform_name}"
            
            debug_info += f"""

⚡ 服务运行状态:
  邮件监控服务: {'🟢 运行中' if self._is_running else '🔴 已停止'}
  有效邮箱账号: {len(valid_accounts)}/{total_accounts} 个
  初始化通知器: {len(self._notifiers)} 个
  保存的目标: {self.config.get("active_targets", [])}
  上次重建连接: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._last_recreate_time)) if self._last_recreate_time else '未执行'}
  重建间隔: {self._recreate_interval//60} 分钟

📊 配置参数:
  检查间隔: {self._interval} 秒
  字符上限: {self._text_num} 字符"""
            
            yield event.plain_result(debug_info)
            return

        # 🔄 服务管理指令
        if action in {"reinit", "reset", "reconnect"}:
            if not self._is_running:
                yield event.plain_result("❌ 邮件监控服务未运行")
                return
                
            try:
                logger.info("[EmailNotixion] 🔄 手动重建连接")
                self._init_notifiers()
                self._last_recreate_time = time.time()
                yield event.plain_result("✅ 所有邮箱连接已重建")
            except Exception as e:
                logger.error(f"[EmailNotixion] 重建失败: {e}")
                yield event.plain_result("❌ 重建失败，请查看日志")
            return

        # 🔄 开关控制指令
        if action in {"on", "start", "enable"}:
            self._register_event_and_start(event)
            yield event.plain_result(f"✅ 当前会话邮件推送已开启 (间隔: {self._interval}s)")
            return

        if action in {"off", "stop", "disable"}:
            if uid in self._targets:
                self._targets.discard(uid)
                self._event_map.pop(uid, None)
                self._save_active_targets()
                
                if not self._targets:
                    await self._stop_email_service()
                yield event.plain_result("✅ 当前会话邮件推送已关闭")
            else:
                yield event.plain_result("❌ 当前会话未开启推送")
            return

        # 📊 默认状态显示
        session_status = "✅ 启用" if uid in self._targets else "❌ 禁用"
        service_status = "🟢 运行中" if self._is_running else "🔴 已停止"
        active_targets = len(self._targets)
        total_accounts = len(self._get_accounts())
        valid_accounts = len(self._get_valid_accounts())
        
        status_text = f"""📧 EmailNotixion 当前会话状态

📊 推送状态: {session_status}
👥 活跃目标: {active_targets} 个
📧 邮箱账号: {valid_accounts}/{total_accounts} 有效
⏱️ 检查间隔: {self._interval} 秒
📝 字符上限: {self._text_num} 字符
⚡ 监控服务: {service_status}
🔄 自动重建: 每{self._recreate_interval//60}分钟

💡 快速指令:
  /email on/off      开启/关闭当前会话推送
  /email add <配置>   添加邮箱账号
  /email list        查看账号详情
  /email text <数值>  设置字符上限
  /email interval <秒> 设置检查间隔  
  /email help        查看完整帮助"""
        yield event.plain_result(status_text)

    # ═══════════════════════ 服务管理 ═══════════════════════

    def _start_email_service(self) -> None:
        """启动邮件推送服务"""
        if self._is_running:
            return
        
        self._is_running = True
        self._init_notifiers()
        self._last_recreate_time = time.time()
        
        # 启动异步邮件监控任务
        self._email_task = asyncio.create_task(self._email_monitor_loop())
        logger.info(f"[EmailNotixion] 🚀 邮件监控服务已启动 (监控 {len(self._notifiers)} 个账号, 重建间隔: {self._recreate_interval//60}分钟)")

    async def _stop_email_service(self) -> None:
        """停止邮件推送服务并清理资源"""
        if not self._is_running:
            return
        
        logger.info("[EmailNotixion] 🛑 正在停止邮件监控服务...")
        self._is_running = False
        
        # 取消邮件监控任务
        if self._email_task and not self._email_task.done():
            self._email_task.cancel()
            try:
                await asyncio.wait_for(self._email_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            finally:
                self._email_task = None
        
        # 清理连接
        for notifier in self._notifiers.values():
            try:
                if notifier.mail:
                    notifier.mail.logout()
            except Exception:
                pass
        
        self._notifiers.clear()
        logger.info("[EmailNotixion] ✅ 邮件监控服务已停止")

    # ═══════════════════════ 生命周期管理 ═══════════════════════

    async def terminate(self) -> None:
        """🔄 插件卸载时的清理工作"""
        logger.info("[EmailNotixion] 🔄 正在卸载插件...")
        await self._stop_email_service()
        logger.info("[EmailNotixion] ✅ 插件已安全卸载")
