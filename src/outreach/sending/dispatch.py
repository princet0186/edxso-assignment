"""Python sender (`outreach send`): the same queue, claim and result logging that the n8n
workflow uses, so the system works identically with or without n8n."""

import logging
import smtplib
import time
from collections import Counter
from collections.abc import Callable
from typing import Protocol

from sqlmodel import Session

from outreach.config import BrandProfile, Secrets, Settings
from outreach.models import DeliveryStatus
from outreach.sending.queue import (
    DeliveryMethod,
    DeliveryResult,
    EmailToSend,
    claim_emails,
    record_result,
)

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, email: EmailToSend) -> str: ...


def deliver(email: EmailToSend, mailer_factory: Callable[[], Mailer]) -> DeliveryResult:
    if email.method == DeliveryMethod.SIMULATE:
        return DeliveryResult(
            DeliveryStatus.SIMULATED, provider_message_id=f"simulated:{email.idempotency_key}"
        )
    try:
        message_id = mailer_factory().send(email)
    except (smtplib.SMTPException, OSError) as exc:
        return DeliveryResult(DeliveryStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
    return DeliveryResult(DeliveryStatus.SENT, provider_message_id=message_id)


def send_queued_emails(
    session: Session,
    settings: Settings,
    secrets: Secrets,
    brand: BrandProfile,
    mailer_factory: Callable[[], Mailer],
) -> dict[str, int]:
    outcomes: Counter[str] = Counter()
    for index, email in enumerate(
        claim_emails(session, settings, secrets, brand, settings.sending.batch_size)
    ):
        if index and email.method == DeliveryMethod.SMTP:
            time.sleep(settings.sending.seconds_between_sends)
        result = deliver(email, mailer_factory)
        record_result(session, email.outreach_id, result)
        outcomes[result.status] += 1
        logger.info("%s -> %s: %s", email.intended_recipient, email.deliver_to, result.status)
    return dict(outcomes)
