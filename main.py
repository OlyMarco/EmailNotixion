import os
import time
from typing import Dict, Set, Optional, List, Tuple, Any, Callable
import yaml

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .core import (
    Config, LogLevel, AccountManager, EmailMonitor,
    EmailNotifier, DedupCache
)


def _load_metadata() -> Dict[str, Any]:
    try:
        path = os.path.join(os.path.dirname(__file__), "metadata.yaml")
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {"version": "v1.1.1"}


_metadata = _load_metadata()


class CommandHandlers:
    """命令处理器集合"""

    def __init__(self, plugin: 'EmailNotixion'):
        self._plugin = plugin

    async def handle_interval(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 interval 命令"""
        if arg is None:
            yield event.plain_result(f"-- 当前间隔: {self._plugin._interval}s")
        else:
            try:
                sec = float(arg)
                if sec <= 0:
                    raise ValueError()
                self._plugin._update_config("interval", sec, Config.MIN_INTERVAL)
                yield event.plain_result(f">> 间隔已设置为 {self._plugin._interval}s")
            except ValueError:
                yield event.plain_result("!! 请提供有效的正数秒数")

    async def handle_text(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 text 命令"""
        if arg is None:
            yield event.plain_result(f"-- 当前字符上限: {self._plugin._text_num}")
        else:
            try:
                num = int(arg)
                if num < Config.MIN_TEXT_NUM:
                    raise ValueError()
                self._plugin._update_config("text_num", num, Config.MIN_TEXT_NUM)
                yield event.plain_result(f">> 字符上限已设置为 {self._plugin._text_num}")
            except ValueError:
                yield event.plain_result(f"!! 请提供有效的整数（>={Config.MIN_TEXT_NUM}）")

    async def handle_add(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 add 命令"""
        if not arg:
            yield event.plain_result(
                "?? 添加邮箱账号\n\n"
                "格式: /email add IMAP服务器,邮箱,应用密码\n\n"
                "示例:\n"
                "- /email add imap.qq.com,123456@qq.com,授权码\n"
                "- /email add imap.gmail.com,xxx@gmail.com,应用密码"
            )
            return
        success, msg = self._plugin._account_manager.add_account(arg, logger)
        if success and self._plugin._monitor.is_running:
            self._plugin._monitor.init_notifiers()
        yield event.plain_result(f"{'>>' if success else '!!'} {msg}")

    async def handle_del(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 del 命令"""
        if not arg:
            yield event.plain_result("?? 用法: /email del <邮箱>")
            return
        success, msg = self._plugin._account_manager.del_account(arg)
        if success and self._plugin._monitor.is_running:
            self._plugin._monitor.init_notifiers()
        yield event.plain_result(f"{'>>' if success else '!!'} {msg}")

    async def handle_list(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 list 命令"""
        accounts = self._plugin._account_manager.get_accounts()
        if accounts:
            valid = self._plugin._account_manager.get_valid_accounts(logger=logger)
            lines: List[str] = []
            for acc in accounts:
                parsed = self._plugin._account_manager.parse_account(acc)
                if parsed:
                    email = parsed[1]
                    cache = self._plugin._account_manager.cache.get(acc)
                    if acc in valid:
                        status = ">> OK"
                    elif cache and cache.error_message:
                        status = f"!! {cache.error_message}"
                    else:
                        status = "!! Failed"
                    lines.append(f"  * {email} - {status}")
            text = f"-- 账号列表 ({len(valid)}/{len(accounts)} 有效)\n\n" + "\n".join(lines)
        else:
            text = "-- 暂无配置账号\n\n使用 /email add 添加"
        yield event.plain_result(text)

    async def handle_help(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 help 命令"""
        yield event.plain_result(f"""-- EmailNotixion {_metadata.get("version")}

== 基本指令 ==
/email            查看状态
/email on         开启推送
/email off        关闭推送
/email list       账号列表

== 账号管理 ==
/email add <配置>  添加账号
/email del <邮箱>  删除账号

== 参数设置 ==
/email interval [秒]   检查间隔
/email text [字符数]   字符上限

== 其他 ==
/email reinit     重建连接
/email refresh    刷新缓存""")

    async def handle_debug(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 debug 命令"""
        valid = self._plugin._account_manager.get_valid_accounts(logger=logger)
        last_time = (
            time.strftime('%H:%M:%S', time.localtime(self._plugin._monitor.last_recreate_time))
            if self._plugin._monitor.last_recreate_time else '从未'
        )
        run_status = ">>" if self._plugin._monitor.is_running else "<<"
        yield event.plain_result(f"""## Debug 信息

活跃目标: {len(self._plugin._targets)}
监控服务: {run_status} {'Running' if self._plugin._monitor.is_running else 'Stopped'}
有效账号: {len(valid)}/{len(self._plugin._account_manager.get_accounts())}
通知器数: {len(self._plugin._monitor.notifiers)}
检查间隔: {self._plugin._interval}s
上次重建: {last_time}""")

    async def handle_refresh(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 refresh 命令"""
        self._plugin._account_manager.clear_cache()
        valid = len(self._plugin._account_manager.get_valid_accounts(force_refresh=True, logger=logger))
        total = len(self._plugin._account_manager.get_accounts())
        yield event.plain_result(f">> 缓存已刷新\n有效账号: {valid}/{total}")

    async def handle_reinit(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 reinit 命令"""
        if not self._plugin._monitor.is_running:
            yield event.plain_result("!! 服务未运行")
            return
        self._plugin._monitor.init_notifiers()
        yield event.plain_result(f">> 连接已重建 (账号: {len(self._plugin._monitor.notifiers)})")

    async def handle_on(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 on 命令"""
        self._plugin._register_and_start(event)
        yield event.plain_result(
            f">> 邮件推送已开启\n\n"
            f"-- 监控账号: {len(self._plugin._monitor.notifiers)}\n"
            f"-- 检查间隔: {self._plugin._interval}s"
        )

    async def handle_off(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理 off 命令"""
        uid = event.unified_msg_origin
        if uid in self._plugin._targets:
            self._plugin._targets.discard(uid)
            self._plugin._event_map.pop(uid, None)
            self._plugin._save_active_targets()
            if not self._plugin._targets:
                await self._plugin._monitor.stop()
            yield event.plain_result(">> 推送已关闭")
        else:
            yield event.plain_result("!! 当前会话未开启推送")

    async def handle_status(self, event: AstrMessageEvent, arg: Optional[str]) -> None:
        """处理默认状态查询"""
        uid = event.unified_msg_origin
        session = "+ 已开启" if uid in self._plugin._targets else "- 未开启"
        service = ">> Running" if self._plugin._monitor.is_running else "<< Stopped"
        valid = len(self._plugin._account_manager.get_valid_accounts(logger=logger))
        total = len(self._plugin._account_manager.get_accounts())

        yield event.plain_result(f"""-- EmailNotixion 状态

推送状态: {session}
监控服务: {service}
活跃目标: {len(self._plugin._targets)}
邮箱账号: {valid}/{total} 有效
检查间隔: {self._plugin._interval}s

/email on   开启  |  /email off   关闭
/email list 账号  |  /email help  帮助""")


@register(
    _metadata.get("name", "EmailNotixion"),
    _metadata.get("author", "Temmie"),
    _metadata.get("description", "EmailNotixion - 实时 IMAP 邮件推送插件"),
    _metadata.get("version", "v1.1.1"),
    _metadata.get("repo", "https://github.com/OlyMarco/EmailNotixion"),
)
class EmailNotixion(Star):
    """EmailNotixion - 实时 IMAP 邮件推送插件"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config: AstrBotConfig = config
        self._init_config()

        self._targets: Set[str] = set()
        self._event_map: Dict[str, AstrMessageEvent] = {}

        self._account_manager: AccountManager = AccountManager(
            config_getter=self.config.get,
            config_setter=self.config.__setitem__,
            save_config=self.config.save_config,
            logger_func=self._log
        )

        self._monitor: EmailMonitor = EmailMonitor(
            account_manager=self._account_manager,
            log_func=self._log,
            send_func=self._send_email_notification,
            text_num=self._text_num,
            logger=logger
        )
        self._monitor.interval = self._interval

        self._handlers: CommandHandlers = CommandHandlers(self)

        saved_count = len(self.config.get("active_targets", []))
        if saved_count > 0:
            self._log(f"~> 检测到 {saved_count} 个保存的推送目标")

        valid = len(self._account_manager.get_valid_accounts(logger=logger))
        total = len(self._account_manager.get_accounts())
        self._log(f">> 插件初始化完成 (账号: {valid}/{total})")

    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        getattr(logger, level.value)(f"[EmailNotixion] {message}")

    def _init_config(self) -> None:
        defaults: Dict[str, Any] = {
            "accounts": [],
            "interval": Config.DEFAULT_INTERVAL,
            "text_num": Config.DEFAULT_TEXT_NUM,
            "active_targets": []
        }
        for key, default in defaults.items():
            self.config.setdefault(key, default)
        self.config.save_config()

        self._interval: float = max(float(self.config["interval"]), Config.MIN_INTERVAL)
        self._text_num: int = max(int(self.config["text_num"]), Config.MIN_TEXT_NUM)

    def _save_active_targets(self) -> None:
        self.config["active_targets"] = list(self._targets)
        self.config.save_config()

    def _update_config(self, key: str, value: Any, min_value: Optional[float] = None) -> None:
        if min_value is not None:
            value = max(value, min_value)
        setattr(self, f"_{key}", value)
        self.config[key] = value
        self.config.save_config()

        if key == "interval":
            self._monitor.interval = value
        elif key == "text_num":
            self._monitor.text_num = value

    async def _send_email_notification(
        self,
        target_event: AstrMessageEvent,
        user: str,
        email_time: Optional[Any],
        subject: str,
        mail_content: str
    ) -> bool:
        try:
            message = f"= 新邮件通知 ({user})\n"
            if email_time:
                message += f"@ 时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            message += f":: 主题: {subject}\n... 内容: {mail_content}"
            await target_event.send(MessageChain().message(message))
            return True
        except Exception as e:
            self._log(f"!! 发送失败: {e}", LogLevel.ERROR)
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
    async def _auto_restore(self, event: AstrMessageEvent) -> None:
        uid = event.unified_msg_origin
        saved = self.config.get("active_targets", [])

        if uid in saved and uid not in self._event_map:
            self._event_map[uid] = event
            self._targets.add(uid)
            self._log(f"~> 自动恢复: {uid}")

            if not self._monitor.is_running and self._targets:
                self._monitor.start(self._targets, self._event_map)

    @filter.command("email", alias={"mail"})
    async def cmd_email(
        self,
        event: AstrMessageEvent,
        sub: Optional[str] = None,
        arg: Optional[str] = None
    ):
        action = (sub or "status").lower()

        handler_map: Dict[str, Callable] = {
            "interval": self._handlers.handle_interval,
            "text": self._handlers.handle_text,
            "textnum": self._handlers.handle_text,
            "limit": self._handlers.handle_text,
            "add": self._handlers.handle_add,
            "a": self._handlers.handle_add,
            "del": self._handlers.handle_del,
            "remove": self._handlers.handle_del,
            "rm": self._handlers.handle_del,
            "list": self._handlers.handle_list,
            "help": self._handlers.handle_help,
            "debug": self._handlers.handle_debug,
            "refresh": self._handlers.handle_refresh,
            "reinit": self._handlers.handle_reinit,
            "reset": self._handlers.handle_reinit,
            "reconnect": self._handlers.handle_reinit,
            "on": self._handlers.handle_on,
            "start": self._handlers.handle_on,
            "enable": self._handlers.handle_on,
            "off": self._handlers.handle_off,
            "stop": self._handlers.handle_off,
            "disable": self._handlers.handle_off,
        }

        if action in handler_map:
            handler = handler_map[action]
            async for result in handler(event, arg):
                yield result
        else:
            async for result in self._handlers.handle_status(event, None):
                yield result

    async def terminate(self) -> None:
        self._log("~> 正在卸载插件...")
        await self._monitor.stop()
        self._account_manager.clear_cache()
        self._log(">> 插件已卸载")
