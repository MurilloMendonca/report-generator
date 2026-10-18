"""Transactional email (verification, password reset, email change).

Backends:
- smtp:    when SMTP_HOST is set (production).
- memory:  under APP_ENV=test; messages land in `outbox`.
- console: otherwise; the message, including its link, is written to the log.

Sending never blocks a request: outside tests it runs in a background thread,
and failures are logged, not raised.
"""
import logging
import os
import smtplib
import ssl
import string
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

logger = logging.getLogger(__name__)
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str


outbox: list[SentEmail] = []


def public_url(path: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}{path}"


def backend() -> str:
    if os.getenv("SMTP_HOST", "").strip():
        return "smtp"
    return "memory" if os.getenv("APP_ENV", "").lower() == "test" else "console"


def render(template: str, **values) -> tuple[str, str]:
    """Templates start with a `Subject: ...` line; `$name` placeholders are filled in."""
    text = (TEMPLATE_DIR / f"{template}.txt").read_text(encoding="utf-8")
    first_line, _, body = text.partition("\n")
    if not first_line.startswith("Subject:"):
        raise ValueError(f"Email template {template} has no Subject line")
    values = {"app_name": "Report Generator", "base_url": public_url(""), **values}
    subject = string.Template(first_line[len("Subject:"):].strip()).substitute(values)
    return subject, string.Template(body.lstrip("\n")).substitute(values)


def _send_smtp(to: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_FROM", "").strip()
    if not sender:
        raise RuntimeError("SMTP_FROM is not set")
    message = EmailMessage()
    message["From"] = formataddr(("Report Generator", sender)) if "<" not in sender else sender
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].strip(">"))
    message.set_content(body)
    context = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    with server:
        if port != 465 and os.getenv("SMTP_STARTTLS", "1") == "1":
            server.starttls(context=context)
        username = os.getenv("SMTP_USERNAME", "")
        if username:
            server.login(username, os.getenv("SMTP_PASSWORD", ""))
        server.send_message(message)


def _deliver(to: str, template: str, values: dict) -> None:
    try:
        subject, body = render(template, **values)
        kind = backend()
        if kind == "smtp":
            _send_smtp(to, subject, body)
        elif kind == "memory":
            outbox.append(SentEmail(to=to, subject=subject, body=body))
        else:
            logger.warning("Email not sent (SMTP_HOST is not set). To: %s Subject: %s\n%s", to, subject, body)
    except Exception:
        logger.exception("Could not send %s email", template)


def send(to: str, template: str, **values) -> None:
    """Queue an email. Returns immediately (synchronous under tests)."""
    if backend() == "memory":
        _deliver(to, template, values)
    else:
        threading.Thread(target=_deliver, args=(to, template, values), daemon=True).start()
