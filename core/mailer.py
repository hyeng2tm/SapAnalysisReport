import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class SAPMailer:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.sender_email = os.getenv("SENDER_EMAIL")
        self.email_password = os.getenv("EMAIL_PASSWORD")

    def send_report(self, recipient_email, report_path, subject="[SAP Report] AI System Analysis"):
        """Sends the generated PDF report via email."""
        if not self.sender_email or not self.email_password:
            logger.warning("Email credentials not configured. Skipping email notification.")
            return False

        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        msg['To'] = recipient_email
        msg['Subject'] = subject

        body = "안녕하세요,\n\nAI가 분석한 SAP 서버 부하 리포트가 생성되었습니다. 첨부된 PDF 파일을 확인해 주세요.\n\n감사합니다."
        msg.attach(MIMEText(body, 'plain'))

        # Attach PDF
        if os.path.exists(report_path):
            with open(report_path, "rb") as f:
                attach = MIMEApplication(f.read(), _subtype="pdf")
                attach.add_header('Content-Disposition', 'attachment', filename=os.path.basename(report_path))
                msg.attach(attach)

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.email_password)
                server.send_message(msg)
            logger.info(f"Email sent successfully to {recipient_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
