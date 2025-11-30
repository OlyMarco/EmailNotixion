"""邮件检查模块 - 同步 IMAP 实现"""
import imaplib
import email as email_stdlib
import time
import re
from datetime import datetime
from typing import Optional, List, Tuple


class EmailConfig:
    """邮件配置常量"""
    CONNECTION_TIMEOUT = 30
    NEW_EMAIL_WINDOW = 120
    DEFAULT_TEXT_NUM = 50


class EmailNotifier:
    """同步邮件通知器"""
    
    def __init__(self, host: str, user: str, token: str, logger=None):
        self.host = host
        self.user = user
        self.token = token
        self.last_uid: Optional[bytes] = None
        self.mail: Optional[imaplib.IMAP4_SSL] = None
        self.logger = logger
        self.text_num = EmailConfig.DEFAULT_TEXT_NUM
        self.last_successful_check: Optional[float] = None

    def _log(self, message: str, level: str = 'info') -> None:
        if self.logger:
            getattr(self.logger, level)(message)

    def cleanup(self) -> None:
        if self.mail:
            try:
                self.mail.logout()
            except Exception:
                pass
            finally:
                self.mail = None

    def test_connection(self) -> bool:
        test_mail = None
        try:
            test_mail = imaplib.IMAP4_SSL(self.host, timeout=EmailConfig.CONNECTION_TIMEOUT)
            test_mail.login(self.user, self.token)
            test_mail.select("INBOX")
            return True
        except Exception as e:
            self._log(f"[EmailNotifier] 连接失败 {self.user}: {e}", 'error')
            return False
        finally:
            if test_mail:
                try:
                    test_mail.logout()
                except Exception:
                    pass

    def _connect(self) -> None:
        try:
            if self.mail:
                try:
                    self.mail.noop()
                    return
                except Exception:
                    pass
            
            self.cleanup()
            self.mail = imaplib.IMAP4_SSL(self.host, timeout=EmailConfig.CONNECTION_TIMEOUT)
            self.mail.login(self.user, self.token)
            self.mail.select("INBOX")
        except Exception as e:
            self._log(f"[EmailNotifier] 连接失败: {e}", 'error')
            self.cleanup()
            raise

    def _html_to_text(self, html: str) -> str:
        if not html:
            return ""
        
        def decode_qp(match):
            try:
                hex_str = match.group(0).replace('=', '')
                if len(hex_str) % 2 == 0:
                    return bytes.fromhex(hex_str).decode('utf-8', errors='ignore')
            except:
                pass
            return match.group(0)
        
        text = re.sub(r'(?:=[0-9A-F]{2})+', decode_qp, html)
        text = text.replace('=3D', '=')
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        
        entities = {'&nbsp;': ' ', '&lt;': '<', '&gt;': '>', '&amp;': '&', '&quot;': '"'}
        for entity, char in entities.items():
            text = text.replace(entity, char)
        
        return re.sub(r'\s+', ' ', text).strip()

    def _get_email_content(self, msg) -> Tuple[str, str]:
        subject = ""
        if msg['Subject']:
            try:
                decoded = email_stdlib.header.decode_header(msg['Subject'])[0][0]
                subject = decoded.decode() if isinstance(decoded, bytes) else decoded
            except Exception:
                subject = msg['Subject']
        
        if len(subject) > self.text_num:
            subject = subject[:self.text_num] + "..."

        content = "（无文本内容）"
        
        if msg.is_multipart():
            text_content = html_content = None
            for part in msg.walk():
                ct = part.get_content_type()
                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    decoded = payload.decode(part.get_content_charset() or 'utf-8')
                    if ct == "text/plain":
                        text_content = decoded
                        break
                    elif ct == "text/html" and not html_content:
                        html_content = decoded
                except Exception:
                    continue
            
            if text_content:
                content = self._process_content(text_content)
            elif html_content:
                content = self._process_content(self._html_to_text(html_content))
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    text = payload.decode(msg.get_content_charset() or 'utf-8')
                    if msg.get_content_type() == "text/html":
                        text = self._html_to_text(text)
                    content = self._process_content(text)
            except Exception:
                pass
        
        return subject, content

    def _process_content(self, text: str) -> str:
        if not text:
            return "（无文本内容）"
        text = ' '.join(text.split())
        if len(text) > self.text_num:
            text = text[:self.text_num] + "..."
        return text.strip() or "（无文本内容）"

    def _is_recent(self, email_time: Optional[datetime]) -> bool:
        if not email_time:
            return True
        try:
            return (time.time() - email_time.timestamp()) < EmailConfig.NEW_EMAIL_WINDOW
        except Exception:
            return True

    def check_and_notify(self) -> Optional[List[Tuple[Optional[datetime], str, str]]]:
        try:
            self._connect()
            
            typ, data = self.mail.uid('SEARCH', None, 'ALL')
            if typ != 'OK' or not data or not data[0]:
                return None

            all_uids = data[0].split()
            if not all_uids:
                return None
            
            if self.last_uid is None:
                self.last_uid = all_uids[-1]
                self.last_successful_check = time.time()
                self._log(f"[EmailNotifier] 初始化基准 UID: {self.last_uid}")
                return None

            new_emails = []
            new_last_uid = self.last_uid
            
            for uid in all_uids:
                if uid <= self.last_uid:
                    continue
                
                info = self._get_email_info(uid)
                if info and self._is_recent(info[0]):
                    new_emails.append(info)
                
                if uid > new_last_uid:
                    new_last_uid = uid

            self.last_uid = new_last_uid
            self.last_successful_check = time.time()
            
            return new_emails if new_emails else None
            
        except Exception as e:
            self._log(f"[EmailNotifier] 检查错误: {e}", 'error')
            self.cleanup()
            return None

    def _get_email_info(self, uid: bytes) -> Optional[Tuple[Optional[datetime], str, str]]:
        try:
            typ, msg_data = self.mail.uid('FETCH', uid, '(RFC822)')
            if typ != 'OK' or not msg_data or not msg_data[0]:
                return None

            msg = email_stdlib.message_from_bytes(msg_data[0][1])
            
            local_date = None
            date_tuple = email_stdlib.utils.parsedate_tz(msg['Date'])
            if date_tuple:
                local_date = datetime.fromtimestamp(email_stdlib.utils.mktime_tz(date_tuple))

            subject, content = self._get_email_content(msg)
            return local_date, subject, content
            
        except Exception as e:
            self._log(f"[EmailNotifier] 获取邮件失败: {e}", 'error')
            return None
