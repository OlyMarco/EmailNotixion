"""EmailNotixion - 实时 IMAP 邮件推送插件"""
import os
import time
from typing import Dict, Set, Optional
import yaml

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .core import Config, LogLevel, AccountManager, EmailMonitor
from .xmail import EmailNotifier


def _load_metadata() -> dict:
    try:
        path = os.path.join(os.path.dirname(__file__), "metadata.yaml")
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        return {"version": "v1.1.1"}


_metadata = _load_metadata()


@register(
    _metadata.get("name", "EmailNotixion"),
    _metadata.get("author", "Temmie"),
    _metadata.get("description", "📧 实时 IMAP 邮件推送插件"),
    _metadata.get("version", "v1.1.1"),
    _metadata.get("repo", "https://github.com/OlyMarco/EmailNotixion"),
)
class EmailNotixion(Star):
    """📧 实时 IMAP 邮件推送插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._init_config()
        
        self._targets: Set[str] = set()
        self._event_map: Dict[str, AstrMessageEvent] = {}
        
        self._account_manager = AccountManager(
            config_getter=self.config.get,
            config_setter=self.config.__setitem__,
            save_config=self.config.save_config,
            logger_func=self._log
        )
        
        self._monitor = EmailMonitor(
            account_manager=self._account_manager,
            log_func=self._log,
            send_func=self._send_email_notification,
            text_num=self._text_num,
            logger=logger
        )
        self._monitor.interval = self._interval
        
        saved_count = len(self.config.get("active_targets", []))
        if saved_count > 0:
            self._log(f"🔄 检测到 {saved_count} 个保存的推送目标")
        
        valid = len(self._account_manager.get_valid_accounts(logger=logger))
        total = len(self._account_manager.get_accounts())
        self._log(f"✅ 插件初始化完成 (账号: {valid}/{total})")

    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        getattr(logger, level.value)(f"[EmailNotixion] {message}")

    def _init_config(self) -> None:
        defaults = {
            "accounts": [],
            "interval": Config.DEFAULT_INTERVAL,
            "text_num": Config.DEFAULT_TEXT_NUM,
            "active_targets": []
        }
        for key, default in defaults.items():
            self.config.setdefault(key, default)
        self.config.save_config()
        
        self._interval = max(float(self.config["interval"]), Config.MIN_INTERVAL)
        self._text_num = max(int(self.config["text_num"]), Config.MIN_TEXT_NUM)

    def _save_active_targets(self) -> None:
        self.config["active_targets"] = list(self._targets)
        self.config.save_config()

    def _update_config(self, key: str, value, min_value=None) -> None:
        if min_value is not None:
            value = max(value, min_value)
        setattr(self, f"_{key}", value)
        self.config[key] = value
        self.config.save_config()
        
        if key == "interval":
            self._monitor.interval = value
        elif key == "text_num":
            self._monitor.text_num = value

    async def _send_email_notification(self, target_event: AstrMessageEvent, 
                                       user: str, email_time, subject: str, 
                                       mail_content: str) -> bool:
        try:
            message = f"📧 新邮件通知 ({user})\n"
            if email_time:
                message += f"⏰ 时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            message += f"📋 主题: {subject}\n📄 内容: {mail_content}"
            await target_event.send(MessageChain().message(message))
            return True
        except Exception as e:
            self._log(f"❌ 发送失败: {e}", LogLevel.ERROR)
            return False

    def _register_and_start(self, event: AstrMessageEvent) -> None:
        uid = event.unified_msg_origin
        if uid not in self._event_map:
            self._event_map[uid] = event
            self._targets.add(uid)
            self._save_active_targets()
        
        if not self._monitor.is_running and self._targets:
            self._monitor.start(self._targets, self._event_map)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _auto_restore(self, event: AstrMessageEvent):
        uid = event.unified_msg_origin
        saved = self.config.get("active_targets", [])
        
        if uid in saved and uid not in self._event_map:
            self._event_map[uid] = event
            self._targets.add(uid)
            self._log(f"🔄 自动恢复: {uid}")
            
            if not self._monitor.is_running and self._targets:
                self._monitor.start(self._targets, self._event_map)

    @filter.command("email", alias={"mail"})
    async def cmd_email(self, event: AstrMessageEvent, sub: str = None, arg: str = None):
        uid = event.unified_msg_origin
        action = (sub or "status").lower()

        if action == "interval":
            if arg is None:
                yield event.plain_result(f"📊 当前间隔: {self._interval} 秒")
            else:
                try:
                    sec = float(arg)
                    if sec <= 0:
                        raise ValueError()
                    self._update_config("interval", sec, Config.MIN_INTERVAL)
                    yield event.plain_result(f"✅ 间隔已设置为 {self._interval} 秒")
                except ValueError:
                    yield event.plain_result("❌ 请提供有效的正数秒数")
            return

        if action in {"text", "textnum", "limit"}:
            if arg is None:
                yield event.plain_result(f"📊 当前字符上限: {self._text_num} 字符")
            else:
                try:
                    num = int(arg)
                    if num < Config.MIN_TEXT_NUM:
                        raise ValueError()
                    self._update_config("text_num", num, Config.MIN_TEXT_NUM)
                    yield event.plain_result(f"✅ 字符上限已设置为 {self._text_num} 字符")
                except ValueError:
                    yield event.plain_result(f"❌ 请提供有效的整数（≥{Config.MIN_TEXT_NUM}）")
            return

        if action in {"add", "a"}:
            if not arg:
                yield event.plain_result(
                    "📝 添加邮箱账号\n\n"
                    "格式: /email add imap服务器,邮箱,应用密码\n\n"
                    "示例:\n"
                    "• /email add imap.qq.com,123456@qq.com,授权码\n"
                    "• /email add imap.gmail.com,xxx@gmail.com,应用密码"
                )
                return
            success, msg = self._account_manager.add_account(arg, logger)
            if success and self._monitor.is_running:
                self._monitor.init_notifiers()
            yield event.plain_result(f"{'✅' if success else '❌'} {msg}")
            return

        if action in {"del", "remove", "rm"}:
            if not arg:
                yield event.plain_result("📝 用法: /email del 邮箱地址")
                return
            success, msg = self._account_manager.del_account(arg)
            if success and self._monitor.is_running:
                self._monitor.init_notifiers()
            yield event.plain_result(f"{'✅' if success else '❌'} {msg}")
            return

        if action == "list":
            accounts = self._account_manager.get_accounts()
            if accounts:
                valid = self._account_manager.get_valid_accounts(logger=logger)
                lines = []
                for acc in accounts:
                    parsed = self._account_manager.parse_account(acc)
                    if parsed:
                        email = parsed[1]
                        cache = self._account_manager.cache.get(acc)
                        if acc in valid:
                            status = "✅ 正常"
                        elif cache and cache.error_message:
                            status = f"❌ {cache.error_message}"
                        else:
                            status = "❌ 失败"
                        lines.append(f"  • {email} - {status}")
                text = f"📧 账号列表 ({len(valid)}/{len(accounts)} 有效)\n\n" + "\n".join(lines)
            else:
                text = "📧 暂无配置账号\n\n使用 /email add 添加"
            yield event.plain_result(text)
            return

        if action == "help":
            yield event.plain_result(f"""📧 EmailNotixion {_metadata.get("version")}

━━━ 基本指令 ━━━
/email            查看状态
/email on         开启推送
/email off        关闭推送
/email list       账号列表

━━━ 账号管理 ━━━
/email add <配置>  添加账号
/email del <邮箱>  删除账号

━━━ 参数设置 ━━━
/email interval [秒]   检查间隔
/email text [字符数]   字符上限

━━━ 其他 ━━━
/email reinit     重建连接
/email refresh    刷新缓存""")
            return

        if action == "debug":
            valid = self._account_manager.get_valid_accounts(logger=logger)
            yield event.plain_result(f"""📊 调试信息

活跃目标: {len(self._targets)}
监控服务: {'🟢 运行中' if self._monitor.is_running else '🔴 已停止'}
有效账号: {len(valid)}/{len(self._account_manager.get_accounts())}
通知器数: {len(self._monitor.notifiers)}
检查间隔: {self._interval}s
上次重建: {time.strftime('%H:%M:%S', time.localtime(self._monitor.last_recreate_time)) if self._monitor.last_recreate_time else '未执行'}""")
            return

        if action == "refresh":
            self._account_manager.clear_cache()
            valid = len(self._account_manager.get_valid_accounts(force_refresh=True, logger=logger))
            total = len(self._account_manager.get_accounts())
            yield event.plain_result(f"✅ 缓存已刷新\n有效账号: {valid}/{total}")
            return

        if action in {"reinit", "reset", "reconnect"}:
            if not self._monitor.is_running:
                yield event.plain_result("❌ 服务未运行")
                return
            self._monitor.init_notifiers()
            yield event.plain_result(f"✅ 连接已重建 (账号: {len(self._monitor.notifiers)})")
            return

        if action in {"on", "start", "enable"}:
            self._register_and_start(event)
            yield event.plain_result(
                f"✅ 邮件推送已开启\n\n"
                f"📊 监控账号: {len(self._monitor.notifiers)}\n"
                f"⏱️ 检查间隔: {self._interval}s"
            )
            return

        if action in {"off", "stop", "disable"}:
            if uid in self._targets:
                self._targets.discard(uid)
                self._event_map.pop(uid, None)
                self._save_active_targets()
                if not self._targets:
                    await self._monitor.stop()
                yield event.plain_result("✅ 推送已关闭")
            else:
                yield event.plain_result("❌ 当前会话未开启推送")
            return

        session = "✅ 已开启" if uid in self._targets else "❌ 未开启"
        service = "🟢 运行中" if self._monitor.is_running else "🔴 已停止"
        valid = len(self._account_manager.get_valid_accounts(logger=logger))
        total = len(self._account_manager.get_accounts())
        
        yield event.plain_result(f"""📧 EmailNotixion 状态

推送状态: {session}
监控服务: {service}
活跃目标: {len(self._targets)}
邮箱账号: {valid}/{total} 有效
检查间隔: {self._interval}s

/email on   开启  |  /email off   关闭
/email list 账号  |  /email help  帮助""")

    async def terminate(self) -> None:
        self._log("🔄 正在卸载插件...")
        await self._monitor.stop()
        self._account_manager.clear_cache()
        self._log("✅ 插件已卸载")
