# capture/privacy.py
from __future__ import annotations

from collections.abc import Iterable


def redact(app: str | None, title: str | None, blocklist: set[str]) -> str | None:
    if app is not None and app.casefold() in blocklist:
        return None

    return title


# Sites whose page titles often carry private content (mail subjects, account
# names, balances, chat names). The browser reader still records the domain for
# these, but never the title. Subdomains match too. Extend the list with
# `title_blocklist_domains` in config.toml.
DEFAULT_SENSITIVE_DOMAINS: frozenset[str] = frozenset(
    {
        # mail and accounts
        "mail.google.com", "accounts.google.com", "myaccount.google.com",
        "outlook.live.com", "outlook.office.com", "outlook.office365.com",
        "mail.yahoo.com", "mail.proton.me", "proton.me",
        # messaging
        "web.whatsapp.com", "web.telegram.org", "messages.google.com",
        "messenger.com",
        # password managers
        "1password.com", "bitwarden.com", "lastpass.com", "dashlane.com",
        # payments and banking
        "paypal.com", "paytm.com", "phonepe.com", "pay.google.com", "razorpay.com",
        "hdfcbank.com", "icicibank.com", "axisbank.com", "kotak.com",
        "onlinesbi.sbi", "onlinesbi.com", "sbi.co.in", "yesbank.in",
        "indusind.com", "pnbindia.in", "bankofbaroda.in", "idfcfirstbank.com",
        # investing, tax and identity
        "zerodha.com", "groww.in", "incometax.gov.in", "uidai.gov.in",
        "digilocker.gov.in",
    }
)


def _normalize(domain: str) -> str:
    return domain.strip().strip(".").casefold()


def is_sensitive_domain(domain: str, extra: Iterable[str] = ()) -> bool:
    """True when `domain` is, or is a subdomain of, a sensitive domain."""
    d = _normalize(domain)
    if not d:
        return False

    listed = DEFAULT_SENSITIVE_DOMAINS | {_normalize(e) for e in extra if _normalize(e)}
    return any(d == s or d.endswith("." + s) for s in listed)


def redact_title_for_domain(
    domain: str,
    title: str | None,
    extra: Iterable[str] = (),
) -> str | None:
    """The title, or None when the domain is on the sensitive list."""
    if is_sensitive_domain(domain, extra):
        return None

    return title
