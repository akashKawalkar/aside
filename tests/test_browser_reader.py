from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from capture.browser_reader import MAX_TITLE_CHARS, BrowserEvent, to_event
from capture.privacy import (
    DEFAULT_SENSITIVE_DOMAINS,
    is_sensitive_domain,
    redact_title_for_domain,
)

T0 = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)


def make(**overrides):
    data = {
        "domain": "github.com",
        "title": "aside/backend: pull requests",
        "ts_start": T0,
        "ts_end": T0 + timedelta(seconds=60),
    }
    data.update(overrides)
    return BrowserEvent(**data)


# ---------- sensitive domains ----------

@pytest.mark.parametrize(
    "domain, expected",
    [
        ("mail.google.com", True),
        ("MAIL.GOOGLE.COM", True),
        ("hdfcbank.com", True),
        ("netbanking.hdfcbank.com", True),  # subdomain of a listed site
        ("github.com", False),
        ("google.com", False),  # only the sensitive Google hosts are listed
        ("notmail.google.com.evil.example", False),  # suffix tricks do not match
        ("fakehdfcbank.com", False),
        ("", False),
    ],
)
def test_default_sensitive_domains(domain, expected):
    assert is_sensitive_domain(domain) is expected


def test_config_can_extend_the_list():
    assert not is_sensitive_domain("intranet.example.org")
    assert is_sensitive_domain("intranet.example.org", extra=["example.org"])
    assert is_sensitive_domain("wiki.example.org", extra=["Example.org."])


def test_redact_title_only_for_sensitive_domains():
    assert redact_title_for_domain("mail.google.com", "Inbox (3)") is None
    assert redact_title_for_domain("github.com", "Pull requests") == "Pull requests"


def test_defaults_are_normalised():
    assert all(d == d.casefold() and not d.startswith(".") for d in DEFAULT_SENSITIVE_DOMAINS)


# ---------- the event model ----------

def test_domain_is_cleaned():
    assert make(domain="  GitHub.COM. ").domain == "github.com"


@pytest.mark.parametrize("bad", ["https://github.com/x", "github.com/path", "has space.com", "a_b.com", ""])
def test_urls_and_junk_are_refused(bad):
    with pytest.raises(ValidationError):
        make(domain=bad)


def test_naive_and_reversed_times_are_refused():
    with pytest.raises(ValidationError):
        make(ts_start=datetime(2026, 10, 6, 9, 0))
    with pytest.raises(ValidationError):
        make(ts_end=T0 - timedelta(seconds=1))


def test_overlong_segment_is_refused():
    with pytest.raises(ValidationError):
        make(ts_end=T0 + timedelta(hours=2))  # a service worker that slept, not activity


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        BrowserEvent(domain="a.com", ts_start=T0, ts_end=T0, url="https://a.com/secret")


# ---------- building the stored event ----------

def test_event_keeps_title_for_ordinary_sites():
    event = to_event(make())
    assert (event.source, event.kind, event.app) == ("browser", "app_focus", None)
    assert event.domain == "github.com"
    assert event.window_title == "aside/backend: pull requests"


def test_event_drops_title_but_keeps_domain_for_sensitive_sites():
    event = to_event(make(domain="mail.google.com", title="Your OTP is 123456"))
    assert event.domain == "mail.google.com"
    assert event.window_title is None


def test_extra_domains_from_config_apply():
    event = to_event(make(domain="wiki.example.org", title="Salaries"), ["example.org"])
    assert event.window_title is None


def test_long_titles_are_trimmed_and_blank_titles_become_none():
    assert len(to_event(make(title="x" * 1500)).window_title) == MAX_TITLE_CHARS
    assert to_event(make(title="   ")).window_title is None


def test_same_stretch_gives_the_same_dedupe_key():
    # The extension may resend a segment after a failure; the database ignores the duplicate.
    assert to_event(make()).dedupe_key == to_event(make()).dedupe_key
