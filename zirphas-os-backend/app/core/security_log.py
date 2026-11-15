"""Structured security events, one JSON object per line.

Events go to the ``zirphas.security`` logger so they can be shipped to a SIEM or
analyzed offline. E-mail addresses are not logged in clear text: a truncated
SHA-256 of the normalized address still lets analysts correlate attempts against
the same account.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger("zirphas.security")


def account_fingerprint(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]


def log_security_event(event: str, *, client_ip: str, email: str, **fields) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        "client_ip": client_ip,
        "account": account_fingerprint(email),
        **fields,
    }
    level = logging.INFO if event == "auth.login_success" else logging.WARNING
    logger.log(level, json.dumps(record, sort_keys=True))
