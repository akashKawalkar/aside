from __future__ import annotations

import os
from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib
from dotenv import load_dotenv

from review.generate import DailyReview


@dataclass(frozen=True)
class SMTPConfig:
    hostname: str
    port: int

    sender: str
    recipients: tuple[str, ...]

    username: str | None = None
    password: str | None = None

    # 465 = implicit TLS
    # 587 = STARTTLS
    use_tls: bool = False
    start_tls: bool = True

    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class EmailResult:
    ok: bool
    message: str


def smtp_config_from_env(recipient: str) -> SMTPConfig | None:
    """
    SMTP settings from .env (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM), so the password never sits in a settings file. Returns None
    when SMTP_HOST isn't set.
    """
    load_dotenv()

    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER") or None

    return SMTPConfig(
        hostname=host,
        port=port,
        sender=os.environ.get("SMTP_FROM") or user or recipient,
        recipients=(recipient,),
        username=user,
        password=os.environ.get("SMTP_PASSWORD") or None,
        use_tls=port == 465,
        start_tls=port != 465,
    )


class ReviewEmailSender:
    """
    Sends one H.1 daily review over SMTP.

    Does not:
        - generate the review
        - access storage
        - call the agent
        - call an LLM
    """

    def __init__(self, config: SMTPConfig) -> None:
        self.config = config

    async def send(
        self,
        review: DailyReview,
    ) -> EmailResult:
        """
        Send one daily review.

        All sending failures are converted into EmailResult rather
        than escaping and crashing the daily-review process.
        """

        try:
            self._validate_config()

            message = self._build_message(review)

            await aiosmtplib.send(
                message,
                hostname=self.config.hostname,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                use_tls=self.config.use_tls,
                start_tls=self.config.start_tls,
                timeout=self.config.timeout_seconds,
            )

            return EmailResult(
                ok=True,
                message="daily review sent",
            )

        except Exception as exc:
            return EmailResult(
                ok=False,
                message=f"email send failed: {exc}",
            )

    def _build_message(
        self,
        review: DailyReview,
    ) -> EmailMessage:
        message = EmailMessage()

        message["From"] = self.config.sender
        message["To"] = ", ".join(
            self.config.recipients
        )
        message["Subject"] = (
            f"Review — "
            f"{review.review_date.isoformat()}"
        )

        body = review.text.strip()

        if not body:
            body = (
                f"Daily review for "
                f"{review.review_date.isoformat()} "
                f"is empty."
            )

        message.set_content(body)

        return message

    def _validate_config(self) -> None:
        if not self.config.hostname.strip():
            raise ValueError(
                "SMTP hostname must not be empty"
            )

        if not self.config.sender.strip():
            raise ValueError(
                "sender must not be empty"
            )

        if not self.config.recipients:
            raise ValueError(
                "at least one recipient is required"
            )

        if any(
            not isinstance(recipient, str)
            or not recipient.strip()
            for recipient in self.config.recipients
        ):
            raise ValueError(
                "all recipients must be non-empty strings"
            )

        if self.config.port <= 0:
            raise ValueError(
                "SMTP port must be greater than 0"
            )

        if self.config.timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than 0"
            )

        if self.config.use_tls and self.config.start_tls:
            raise ValueError(
                "use_tls and start_tls cannot both be enabled"
            )


async def send_review(
    review: DailyReview,
    *,
    config: SMTPConfig,
) -> EmailResult:
    """
    Public H.2 entrypoint.
    """

    sender = ReviewEmailSender(config)

    return await sender.send(review)