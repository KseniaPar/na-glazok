"""Gateway package — Input/Output guards (in-process)."""
from na_glazok.gateway import audit, guards

RATE_LIMIT_STORE = audit.RATE_LIMIT_STORE
AUDIT_LOG_PATH = audit.AUDIT_LOG_PATH
logger = guards.logger
message_text = guards.message_text

__all__ = [
    "RATE_LIMIT_STORE",
    "AUDIT_LOG_PATH",
    "logger",
    "message_text",
]
