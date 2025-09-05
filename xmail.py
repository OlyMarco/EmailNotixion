"""
EmailNotifier - 同步邮件检查模块

设计说明：
- 纯同步设计，使用阻塞I/O操作
- 在异步环境中需通过 asyncio.to_thread() 包装
- 保持简单接口，便于理解和维护
- 模块独立，不依赖异步框架
"""
import imaplib
import email as email_stdlib
import time
import os
import re
from datetime import datetime

class EmailNotifier:
    """同步邮件通知器
    
    ⚠️ 重要：此类使用同步阻塞的 imaplib 库
    - 所有网络操作都会阻塞当前线程
    - 在异步环境中使用时必须通过 asyncio.to_thread() 包装
    """
    
    def __init__(self, host, user, token, logger=None):
        self.host = host
        self.user = user
        self.token = token
        self.last_uid = None
        self.mail = None
        self.logger = logger
        self.text_num = 50  # 默认文本长度限制
        self.last_successful_check = None  # 上次成功检查时间

    def _log(self, message, level='info'):
        """统一日志记录"""
        if self.logger:
            getattr(self.logger, level)(message)
        else:
            print(message)

    def test_connection(self) -> bool:
        """测试IMAP连接是否有效
        
        ⚠️ 阻塞操作：包含同步网络I/O操作
        
        返回值：
        - True: 连接成功
        - False: 连接失败
        """
        try:
            # 尝试建立连接
            test_mail = imaplib.IMAP4_SSL(self.host, timeout=30)
            test_mail.login(self.user, self.token)
            test_mail.select("INBOX")
            test_mail.logout()
            return True
        except Exception as e:
            self._log(f"[EmailNotifier] 连接测试失败 {self.user}: {e}", 'error')
            return False

    def _connect(self):
        """建立并维护 IMAP 连接
        
        ⚠️ 阻塞操作：包含同步网络I/O操作
        """
        try:
            # 检查连接是否仍然有效
            if self.mail:
                try:
                    self.mail.noop()
                    return
                except Exception:
                    pass
            
            # 清理旧连接
            if self.mail:
                try:
                    self.mail.logout()
                except:
                    pass
                self.mail = None
            
            # 建立新连接
            self.mail = imaplib.IMAP4_SSL(self.host, timeout=30)
            self.mail.login(self.user, self.token)
            self.mail.select("INBOX")
            
        except Exception as e:
            self._log(f"[EmailNotifier] 连接失败: {e}", 'error')
            if self.mail:
                try:
                    self.mail.logout()
                except:
                    pass
                self.mail = None
            raise

    def _html_to_text(self, html_content):
        """将HTML内容转换为纯文本"""
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
        """从邮件消息中解析主题和正文内容，限制text_num个字符"""
        subject = ""
        # 解码主题
        if msg['Subject']:
            try:
                subject = email_stdlib.header.decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
            except Exception:
                subject = msg['Subject']  # Fallback
        
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
                pass  # Keep default
        
        return subject, content

    def _process_content(self, text):
        """处理文本内容，统一换行符并限制长度"""
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
        """检查新邮件并返回其详细信息
        
        ⚠️ 阻塞操作：此方法包含多个同步网络I/O操作，会阻塞当前线程
        在异步环境中调用时必须使用 asyncio.to_thread() 包装
        
        返回值：
        - None: 无新邮件或发生错误
        - list: [(时间, 主题, 邮件内容), ...] - 新邮件列表
        """
        try:
            self._connect()
            new_emails = []
            current_time = time.time()
            two_minutes_ago = current_time - 120  # 2分钟前的时间戳
            processed_uids = set()  # 防止重复处理
            
            # 检查未读邮件
            try:
                typ, data = self.mail.uid('SEARCH', None, 'UNSEEN')
                if typ == 'OK' and data and data[0]:
                    unread_uids = data[0].split()
                    for uid in unread_uids:
                        if uid in processed_uids:
                            continue
                        processed_uids.add(uid)
                        
                        if self.last_uid is None or uid > self.last_uid:
                            email_info = self._get_email_info(uid)
                            if email_info:
                                email_time, subject, mail_content = email_info
                                # 新的判定条件：未读且2分钟内
                                if email_time and email_time.timestamp() > two_minutes_ago:
                                    new_emails.append(email_info)
                                    self.last_uid = uid
                                elif email_time and email_time.timestamp() <= two_minutes_ago:
                                    self.last_uid = uid  # 更新UID但不推送
                                elif not email_time:
                                    # 如果无法获取邮件时间，为了安全起见也加入新邮件列表
                                    new_emails.append(email_info)
                                    self.last_uid = uid
            except Exception as e:
                self._log(f"[EmailNotifier] 检查未读邮件错误: {e}", 'warning')
            
            # 检查所有邮件 - 只处理未在未读检查中处理过的邮件
            typ, data = self.mail.uid('SEARCH', None, 'ALL')
            if typ != 'OK' or not data or not data[0]:
                if new_emails:
                    self.last_successful_check = time.time()
                    return new_emails
                return None

            all_uids = data[0].split()
            
            # 如果是第一次运行，设置基准点
            if self.last_uid is None:
                self.last_uid = all_uids[-1] if all_uids else None
                self.last_successful_check = time.time()
                return None

            # 找到所有比last_uid更新的邮件，并检查时间条件
            for uid in all_uids:
                if uid in processed_uids:
                    continue  # 跳过已处理的邮件
                    
                if uid > self.last_uid:
                    processed_uids.add(uid)
                    email_info = self._get_email_info(uid)
                    if email_info:
                        email_time, subject, mail_content = email_info
                        # 应用相同的时间判定条件：2分钟内
                        if email_time and email_time.timestamp() > two_minutes_ago:
                            new_emails.append(email_info)
                            self.last_uid = uid
                        elif email_time and email_time.timestamp() <= two_minutes_ago:
                            self.last_uid = uid  # 更新UID但不推送
                        elif not email_time:
                            # 如果无法获取邮件时间，为了安全起见也加入新邮件列表
                            new_emails.append(email_info)
                            self.last_uid = uid

            if new_emails:
                self.last_successful_check = time.time()
                return new_emails
            else:
                self.last_successful_check = time.time()
                return None
            
        except Exception as e:
            self._log(f"[EmailNotifier] 检查邮件错误: {e}", 'error')
            self._reset_connection_state()
            return None
    
    def _reset_connection_state(self):
        """重置连接状态，但保留UID状态"""
        if self.mail:
            try:
                self.mail.logout()
            except Exception:
                pass
        self.mail = None
    
    def _get_email_info(self, uid):
        """获取指定UID邮件的详细信息"""
        try:
            typ, msg_data = self.mail.uid('FETCH', uid, '(RFC822)')
            if typ != 'OK' or not msg_data or not msg_data[0]:
                return None

            msg = email_stdlib.message_from_bytes(msg_data[0][1])
            
            local_date = None
            date_tuple = email_stdlib.utils.parsedate_tz(msg['Date'])
            if date_tuple:
                local_date = datetime.fromtimestamp(email_stdlib.utils.mktime_tz(date_tuple))

            subject, mail_content = self._get_email_content(msg)
            return local_date, subject, mail_content
            
        except Exception as e:
            self._log(f"[EmailNotifier] 获取邮件信息失败: {e}", 'error')
            return None
    
    def reset_connection(self):
        """重置连接状态，强制重新初始化"""
        if self.mail:
            try:
                self.mail.logout()
            except Exception:
                pass
        self.mail = None


    def run(self, interval=10):
        """启动轮询循环
        
        ⚠️ 阻塞操作：此方法包含 time.sleep() 会阻塞当前线程
        在异步环境中不应直接使用此方法，而应使用 check_and_notify() 结合 asyncio.sleep()
        """
        while True:
            notification = self.check_and_notify()
            if notification:
                # 处理邮件列表
                if isinstance(notification, list):
                    for email_time, subject, mail_content in notification:
                        if self.logger:
                            self.logger.info(f"[EmailNotifier] 新邮件通知 - 主题: {subject}")
                        else:
                            print("\n--- 📧 新邮件通知 ---")
                            if email_time:
                                print(f"时间: {email_time.strftime('%Y-%m-%d %H:%M:%S')}")
                            print(f"主题: {subject}")
                            print(f"内容: {mail_content}")
                            print("--------------------")
                else:
                    # 兼容旧版本单邮件格式
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
        print("❌ 错误: 请设置必要的环境变量")
        print("  EMAIL_HOST - IMAP服务器地址")
        print("  EMAIL_USER - 邮箱地址")  
        print("  EMAIL_TOKEN - 应用专用密码")
        print("\n💡 示例:")
        print("  export EMAIL_HOST=imap.gmail.com")
        print("  export EMAIL_USER=user@gmail.com")
        print("  export EMAIL_TOKEN=your_app_password")
        exit(1)

    notifier = EmailNotifier(HOST, USER, TOKEN)
    try:
        print(f"📧 开始监控邮箱: {USER}")
        notifier.run(interval=3)
    except KeyboardInterrupt:
        print("\n⚠️ 程序已停止")
        if notifier.mail:
            notifier.mail.logout()
