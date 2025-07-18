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
import re
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
        self.text_num = 50  # 默认文本长度限制

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

    def _html_to_text(self, html_content):
        """
        将HTML内容转换为纯文本
        首先解码quoted-printable编码，然后去除HTML标签
        """
        if not html_content:
            return ""
        
        # 先处理quoted-printable编码（如=E5=B0=8A=E6=95=AC）
        def decode_quoted_printable(match):
            try:
                hex_string = match.group(0).replace('=', '')
                if len(hex_string) % 2 == 0:
                    bytes_data = bytes.fromhex(hex_string)
                    return bytes_data.decode('utf-8', errors='ignore')
                return match.group(0)
            except:
                return match.group(0)
        
        # 解码quoted-printable编码
        text = re.sub(r'(?:=[0-9A-F]{2})+', decode_quoted_printable, html_content)
        
        # 处理3D等号编码 (如 =3D)
        text = text.replace('=3D', '=')
        
        # 去除HTML标签（包括样式和脚本）
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        
        # 解码HTML实体
        html_entities = {
            '&nbsp;': ' ', '&lt;': '<', '&gt;': '>', '&amp;': '&',
            '&quot;': '"', '&apos;': "'", '&copy;': '©', '&reg;': '®',
            '&trade;': '™', '&mdash;': '—', '&ndash;': '–',
            '&hellip;': '...', '&laquo;': '«', '&raquo;': '»'
        }
        
        for entity, char in html_entities.items():
            text = text.replace(entity, char)
        
        # 去除多余空白字符
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()

    def _get_email_content(self, msg):
        """从邮件消息中解析主题和正文内容，限制text_num个字符。"""
        subject = ""
        # 解码主题
        if msg['Subject']:
            try:
                subject = email_stdlib.header.decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
            except Exception:
                subject = msg['Subject'] # Fallback
        
        # 限制主题长度为text_num个字符
        if len(subject) > self.text_num:
            subject = subject[:self.text_num] + "..."

        content = "（无文本内容）"
        
        # 处理多部分和单部分邮件
        if msg.is_multipart():
            # 优先寻找纯文本，如果没有则使用HTML并转换
            text_content = None
            html_content = None
            
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            text_content = payload.decode(part.get_content_charset() or 'utf-8')
                            break
                    except Exception:
                        continue
                elif content_type == "text/html" and html_content is None:
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            html_content = payload.decode(part.get_content_charset() or 'utf-8')
                    except Exception:
                        continue
            
            # 优先使用纯文本，否则转换HTML
            if text_content:
                content = self._process_content(text_content)
            elif html_content:
                content = self._process_content(self._html_to_text(html_content))
        else:
            # 单部分邮件
            content_type = msg.get_content_type()
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    text = payload.decode(msg.get_content_charset() or 'utf-8')
                    if content_type == "text/plain":
                        content = self._process_content(text)
                    elif content_type == "text/html":
                        content = self._process_content(self._html_to_text(text))
            except Exception:
                pass # Keep default
        
        return subject, content

    def _process_content(self, text):
        """处理文本内容，统一换行符并限制长度。"""
        if not text:
            return "（无文本内容）"
        
        # 统一换行符处理：将所有类型的换行符转换为空格
        text = text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
        
        # 清理多余空格
        text = ' '.join(text.split())
        
        # 限制长度为text_num个字符
        if len(text) > self.text_num:
            text = text[:self.text_num] + "..."
        
        return text.strip() if text.strip() else "（无文本内容）"

    def check_and_notify(self):
        """
        检查新邮件并返回其详细信息
        
        ⚠️ 阻塞操作：此方法包含多个同步网络I/O操作，会阻塞当前线程
        在异步环境中调用时必须使用 asyncio.to_thread() 包装
        
        返回值：
        - None: 无新邮件或发生错误
        - tuple: (时间, 主题, 邮件内容)
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
            
            # 获取邮件日期
            local_date = None
            date_tuple = email_stdlib.utils.parsedate_tz(msg['Date'])
            if date_tuple:
                local_date = datetime.fromtimestamp(email_stdlib.utils.mktime_tz(date_tuple))

            # ④ 更新ID并返回邮件内容
            self.last_uid = latest_uid
            subject, mail_content = self._get_email_content(msg)
            return local_date, subject, mail_content

        except (imaplib.IMAP4.error, Exception) as e:
            # 统一处理所有预期的和未知的错误
            log_message = f"[EmailNotifier] IMAP 错误: {e}" if isinstance(e, imaplib.IMAP4.error) else f"[EmailNotifier] 发生未知错误: {e}"
            if self.logger:
                self.logger.error(log_message)
            else:
                print(log_message)
            
            # 统一的清理逻辑
            if self.mail:
                try:
                    self.mail.logout()
                except Exception:
                    pass  # 注销失败也无需额外操作
            self.mail = None
            return None  # 确保出错时返回 None


    def run(self, interval=10):
        """
        启动轮询循环
        
        ⚠️ 阻塞操作：此方法包含 time.sleep() 会阻塞当前线程
        在异步环境中不应直接使用此方法，而应使用 check_and_notify() 结合 asyncio.sleep()
        """
        while True:
            notification = self.check_and_notify()
            if notification:
                email_time, subject, mail_content = notification
                if self.logger:
                    self.logger.info(f"[EmailNotifier] 新邮件通知 - 主题: {subject}")
                else:
                    print("\n--- 📧 新邮件通知 ---")
                    if email_time:
                        print(f"时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"主题: {subject}")
                    print(f"内容: {mail_content}")
                    print("--------------------")
            time.sleep(interval)

if __name__ == "__main__":
    # ⚠️ 安全注意：在生产环境中，请使用环境变量来加载敏感信息
    # 设置环境变量示例：
    # export EMAIL_HOST=imap.example.com
    # export EMAIL_USER=user@example.com  
    # export EMAIL_TOKEN=your_app_password
    
    HOST = os.getenv('EMAIL_HOST')
    USER = os.getenv('EMAIL_USER')
    TOKEN = os.getenv('EMAIL_TOKEN')
    
    # 检查必要的环境变量
    if not all([HOST, USER, TOKEN]):
        print("错误：请设置必要的环境变量：")
        print("  EMAIL_HOST - IMAP服务器地址")
        print("  EMAIL_USER - 邮箱地址")  
        print("  EMAIL_TOKEN - 应用专用密码")
        print("\n示例：")
        print("  export EMAIL_HOST=imap.gmail.com")
        print("  export EMAIL_USER=user@gmail.com")
        print("  export EMAIL_TOKEN=your_app_password")
        exit(1)

    notifier = EmailNotifier(HOST, USER, TOKEN)
    try:
        print(f"开始监控邮箱: {USER}")
        notifier.run(interval=3)
    except KeyboardInterrupt:
        print("\n程序已停止。")
        if notifier.mail:
            notifier.mail.logout()
