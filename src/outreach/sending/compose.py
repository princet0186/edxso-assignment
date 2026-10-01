"""Turns an approved message into the exact email that goes out."""

from outreach.config import BrandProfile

REDIRECT_SUBJECT_PREFIX = "[TEST → {recipient}] "


def compose_body(validated_body: str, brand: BrandProfile, sender_name: str) -> str:
    """Signature and opt-out are appended after validation, so they are identical on every
    email and are not counted toward the 60-90 word body limit."""
    signature = f"{sender_name or brand.default_sender_name}\n{brand.name}"
    return f"{validated_body}\n\n{signature}\n\n{brand.opt_out_line}"


def redirect_subject(subject: str, intended_recipient: str) -> str:
    return REDIRECT_SUBJECT_PREFIX.format(recipient=intended_recipient) + subject
