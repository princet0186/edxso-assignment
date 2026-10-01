"""Gmail SMTP delivery (STARTTLS on 587 with an App Password)."""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from outreach.config import Secrets, require
from outreach.sending.queue import EmailToSend


class SmtpMailer:
    def __init__(self, secrets: Secrets, timeout_seconds: float) -> None:
        self._host = require(secrets.smtp_host, "SMTP_HOST")
        self._port = secrets.smtp_port
        self._user = require(secrets.smtp_user, "SMTP_USER")
        self._password = require(secrets.smtp_app_password, "SMTP_APP_PASSWORD")
        self._sender_name = secrets.sender_name
        self._timeout_seconds = timeout_seconds

    def send(self, email: EmailToSend) -> str:
        """Sends one email and returns its Message-ID."""
        message = EmailMessage()
        message["From"] = formataddr((self._sender_name, self._user))
        message["To"] = email.deliver_to
        message["Subject"] = email.subject
        message["Message-ID"] = make_msgid(domain=self._user.split("@")[-1])
        message["X-Outreach-Key"] = email.idempotency_key
        message.set_content(email.body)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._user, self._password)
            smtp.send_message(message)
        return message["Message-ID"]
