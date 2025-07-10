"""
EmailNotifier - 独立同步邮件检查模块

⚠️ 重要说明：
本模块是一个独立的同步模块，专门设计用于在独立线程中运行。
所有方法都是同步的，使用标准的阻塞I/O操作。
在异步环境中使用时，必须通过 asyncio.to_thread() 包装以避免阻塞事件循环。

设计原则：
- 保持简单的同步接口，便于理解和维护
- 通过外部异步包装器确保并发安全性
- 不直接依赖异步框架，保持模块独立性
"""
import imaplib
import email as email_stdlib
import time
import os
from datetime import datetime, timedelta, timezone

class EmailNotifier:
    """
    同步邮件通知器
    
    ⚠️ 重要：此类使用同步阻塞的 imaplib 库
    - 所有网络操作（连接、搜索、获取）都会阻塞当前线程
    - 在异步环境中使用时必须通过 asyncio.to_thread() 包装
    - 这种设计是为了保持简单性和线程安全性
    """
    def __init__(self, host, user, token, logger=None):
        self.host = host
        self.user = user
        self.token = token
        self.last_uid = None
        self.mail = None
        self.logger = logger  # 可选的外部日志记录器

    def _connect(self):
        """
        建立并维护 IMAP 连接
        
        ⚠️ 阻塞操作：此方法包含同步网络I/O操作，会阻塞当前线程
        在异步环境中调用时必须使用 asyncio.to_thread() 包装
        """
        try:
            # 检查连接是否仍然有效
            self.mail.noop()
        except (AttributeError, imaplib.IMAP4.error):
            # 如果连接丢失或未初始化，则重新连接
            if self.logger:
                self.logger.info(f"[EmailNotifier] 正在连接到邮箱 {self.host}...")
            else:
                print("正在连接到邮箱...")
            self.mail = imaplib.IMAP4_SSL(self.host)
            self.mail.login(self.user, self.token)
            if self.logger:
                self.logger.info("[EmailNotifier] 连接成功")
            else:
                print("连接成功。")
        self.mail.select("INBOX")

    def _get_email_content(self, msg):
        """从邮件消息中解析主题和正文第一行。"""
        subject = ""
        # 解码主题
        if msg['Subject']:
            try:
                subject = email_stdlib.header.decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
            except Exception:
                subject = msg['Subject'] # Fallback

        first_line = "（无文本内容）"
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        payload = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8')
                        first_line = payload.strip().split('\n')[0]
                        break
                    except Exception:
                        continue
        else:
            if msg.get_content_type() == "text/plain":
                 try:
                    payload = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8')
                    first_line = payload.strip().split('\n')[0]
                 except Exception:
                    pass # Keep default
        
        return subject, first_line.strip()

    def check_and_notify(self):
        """
        检查新邮件并返回其详细信息
        
        ⚠️ 阻塞操作：此方法包含多个同步网络I/O操作，会阻塞当前线程
        在异步环境中调用时必须使用 asyncio.to_thread() 包装
        
        返回值：
        - None: 无新邮件或发生错误
        - tuple: (时间, 主题, 第一行内容)
        """
        try:
            self._connect()
            # ① 搜索所有邮件UID
            typ, data = self.mail.uid('SEARCH', None, 'ALL')
            if typ != 'OK' or not data or not data[0]:
                return None # 邮箱为空

            latest_uid = data[0].split()[-1]

            # 如果是第一次运行，则将最新邮件ID设为基准，不通知
            if self.last_uid is None:
                self.last_uid = latest_uid
                if self.logger:
                    self.logger.info(f"[EmailNotifier] 初始化完成，当前最新邮件ID: {latest_uid.decode()}")
                else:
                    print(f"初始化完成，当前最新邮件ID: {latest_uid.decode()}")
                return None

            # ② 如果没有新邮件，则直接返回
            if latest_uid == self.last_uid:
                return None

            # ③ 获取最新邮件的日期和内容
            typ, msg_data = self.mail.uid('FETCH', latest_uid, '(RFC822)')
            if typ != 'OK':
                return None

            msg = email_stdlib.message_from_bytes(msg_data[0][1])
            
            # 检查邮件日期是否在1分钟内
            local_date = None
            date_tuple = email_stdlib.utils.parsedate_tz(msg['Date'])
            if date_tuple:
                local_date = datetime.fromtimestamp(email_stdlib.utils.mktime_tz(date_tuple))
                if datetime.now() - local_date > timedelta(minutes=1):
                    self.last_uid = latest_uid # 将旧邮件也标记为已读
                    return None # 邮件太旧

            # ④ 更新ID并返回邮件内容
            self.last_uid = latest_uid
            subject, first_line = self._get_email_content(msg)
            return local_date, subject, first_line

        except imaplib.IMAP4.error as e:
            if self.logger:
                self.logger.error(f"[EmailNotifier] IMAP 错误: {e}")
            else:
                print(f"IMAP 错误: {e}")
            # 正确释放连接资源
            if self.mail:
                try:
                    self.mail.logout()
                except Exception:
                    pass  # 忽略登出时的错误
            self.mail = None # 强制下次重连
        except Exception as e:
            if self.logger:
                self.logger.error(f"[EmailNotifier] 发生未知错误: {e}")
            else:
                print(f"发生未知错误: {e}")
            # 正确释放连接资源
            if self.mail:
                try:
                    self.mail.logout()
                except Exception:
                    pass  # 忽略登出时的错误
            self.mail = None


    def run(self, interval=10):
        """
        启动轮询循环
        
        ⚠️ 阻塞操作：此方法包含 time.sleep() 会阻塞当前线程
        在异步环境中不应直接使用此方法，而应使用 check_and_notify() 结合 asyncio.sleep()
        """
        while True:
            notification = self.check_and_notify()
            if notification:
                email_time, subject, first_line = notification
                if self.logger:
                    self.logger.info(f"[EmailNotifier] 新邮件通知 - 主题: {subject}")
                else:
                    print("\n--- 📧 新邮件通知 ---")
                    if email_time:
                        print(f"时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"主题: {subject}")
                    print(f"内容: {first_line}")
                    print("--------------------")
            time.sleep(interval)

if __name__ == "__main__":
    # ⚠️ 注意：在生产环境中，请使用环境变量或配置文件来加载敏感信息
    # 示例：HOST = os.getenv('EMAIL_HOST', 'imap.example.com')
    #      USER = os.getenv('EMAIL_USER', 'user@example.com')
    #      TOKEN = os.getenv('EMAIL_TOKEN', 'your_app_password')
    
    HOST = os.getenv('EMAIL_HOST', 'imap.cuc.edu.cn')
    USER = os.getenv('EMAIL_USER', 'xxx@cuc.edu.cn')
    TOKEN = os.getenv('EMAIL_TOKEN', 'xxxxxxxxxxxx')  # 应用专用密码

    notifier = EmailNotifier(HOST, USER, TOKEN)
    try:
        notifier.run(interval=3)
    except KeyboardInterrupt:
        print("\n程序已停止。")
        if notifier.mail:
            notifier.mail.logout()
