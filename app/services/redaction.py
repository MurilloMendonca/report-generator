"""Redact provider secrets before logging and before persisting report state."""
import logging
import re
import traceback

_KEY = re.compile(r"(?<![\w-])(?:sk-(?:ant-)?[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{12,}|[A-Za-z0-9+/]{80,}={0,2})(?![\w-])")
_SHARE_LINK = re.compile(r"(/(?:share|api/shared-reports)/)[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")


def redact(value, secrets=()):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return _SHARE_LINK.sub(r"\1[REDACTED]", _KEY.sub("[REDACTED]", value))
    if isinstance(value, dict):
        return {redact(key, secrets): redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item, secrets) for item in value]
    return value


class SecretRedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def install_log_redaction() -> None:
    """Use a record factory as well, covering handlers installed after startup."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacts_secrets", False):
        return
    redactor = SecretRedactionFilter()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        redactor.filter(record)
        return record

    factory._redacts_secrets = True
    logging.setLogRecordFactory(factory)
