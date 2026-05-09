"""AEGIS safety: action classification, prompt-injection detection, policy enforcement."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Prompt injection detection (lexical layer)
# ---------------------------------------------------------------------------

PROMPT_INJECTION_PATTERNS = (
    r"ignore (all )?(previous|prior) instructions",
    r"disregard (the )?(system|developer|previous) instructions",
    r"you are now",
    r"reveal (your )?(system prompt|instructions|secrets)",
    r"send (money|payment|funds)",
    r"(system|admin)\s*:\s*(override|mode|access)",
    r"as an ai\b",
    r"forget (everything|all|your)",
)


_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿]")

_HOMOGLYPH_MAP = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p",
    "с": "c", "у": "y", "х": "x", "і": "i",
    "А": "A", "Е": "E", "О": "O", "Р": "P",
    "С": "C", "У": "Y", "Х": "X", "І": "I",
    "Α": "A", "Β": "B", "Ε": "E", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
    "α": "a", "ο": "o", "ρ": "p",
})


def _normalize_text(text: str) -> str:
    text = _ZERO_WIDTH.sub("", text)
    text = text.translate(_HOMOGLYPH_MAP)
    return text


def detect_prompt_injection(*texts: str | None) -> str | None:
    raw = "\n".join(t or "" for t in texts)
    haystack = _normalize_text(raw).lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, haystack):
            return f"prompt injection pattern matched: {pattern}"
    return None


# ---------------------------------------------------------------------------
# Action categories + patterns
# ---------------------------------------------------------------------------

ActionCategory = Literal[
    "file_destructive",
    "purchase",
    "outbound_message",
    "credential_entry",
    "prompt_injection",
    "domain_blocked",
    "xss_attempt",
]

_CATEGORY_PATTERNS: list[tuple[ActionCategory, tuple[str, ...]]] = [
    ("file_destructive", (
        r"\b(delete|remove|drop|destroy|wipe|rm|rmdir|unlink|shred)\b",
        r"\b(format|erase)\b.*\b(disk|drive|volume|partition)\b",
    )),
    ("credential_entry", (
        r"\bsubmit\b.*\b(password|payment|card|ssn|secret|token|credential)\b",
        r"\b(log\s*in|sign\s*in|authenticate)\b",
        r"\b(enter|type|input)\b.*\b(password|credit.card|cvv|pin|ssn)\b",
    )),
    ("purchase", (
        r"\b(purchase|buy|checkout|order|subscribe|add.to.cart)\b",
        r"\b(confirm|place|complete)\b.*\b(order|purchase|payment)\b",
        r"\b(pay\s+now|buy\s+now|proceed.to.checkout)\b",
    )),
    ("outbound_message", (
        r"\b(send|message|contact|email|reply|post|comment|dm)\b",
        r"\b(call|text|sms|chat)\b.*\b(seller|buyer|user|owner|agent)\b",
    )),
]

_CATEGORY_VERDICTS: dict[ActionCategory, Literal["approve", "block"]] = {
    "file_destructive": "block",
    "purchase": "block",
    "outbound_message": "approve",
    "credential_entry": "approve",
    "prompt_injection": "block",
    "domain_blocked": "block",
    "xss_attempt": "block",
}


# ---------------------------------------------------------------------------
# Domain boundary enforcement
# ---------------------------------------------------------------------------

ALLOWED_DOMAINS: set[str] = set()
_allowed = os.getenv("AEGIS_ALLOWED_DOMAINS", "")
if _allowed:
    ALLOWED_DOMAINS.update(d.strip().lower() for d in _allowed.split(",") if d.strip())

BLOCKED_DOMAINS: set[str] = {
    "evil.com",
    "malware.com",
    "phishing.com",
    "exfil.com",
    "steal-data.com",
}
_blocked = os.getenv("AEGIS_BLOCKED_DOMAINS", "")
if _blocked:
    BLOCKED_DOMAINS.update(d.strip().lower() for d in _blocked.split(",") if d.strip())

_SUSPICIOUS_TLDS = {".tk", ".ml", ".ga", ".cf", ".gq", ".buzz", ".top", ".xyz", ".click", ".loan", ".work"}


def check_domain(url: str) -> SecurityVerdict | None:
    if re.match(r"(?i)javascript\s*:", url):
        return SecurityVerdict(
            verdict="block",
            reason="xss_attempt: javascript: URI detected",
            category="xss_attempt",
            matched_rule="javascript_uri",
        )

    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return SecurityVerdict(
            verdict="block",
            reason="domain_blocked: malformed URL",
            category="domain_blocked",
            matched_rule="malformed_url",
        )

    if not hostname:
        return None

    for blocked in BLOCKED_DOMAINS:
        if hostname == blocked or hostname.endswith("." + blocked):
            return SecurityVerdict(
                verdict="block",
                reason=f"domain_blocked: {hostname} is on the blocklist",
                category="domain_blocked",
                matched_rule=f"blocklist:{blocked}",
            )

    if ALLOWED_DOMAINS:
        if not any(hostname == d or hostname.endswith("." + d) for d in ALLOWED_DOMAINS):
            return SecurityVerdict(
                verdict="block",
                reason=f"domain_blocked: {hostname} is not on the allowlist",
                category="domain_blocked",
                matched_rule="allowlist_miss",
            )

    for tld in _SUSPICIOUS_TLDS:
        if hostname.endswith(tld):
            return SecurityVerdict(
                verdict="block",
                reason=f"domain_blocked: suspicious TLD {tld} — {hostname}",
                category="domain_blocked",
                matched_rule=f"suspicious_tld:{tld}",
            )

    if parsed.scheme == "javascript":
        return SecurityVerdict(
            verdict="block",
            reason="xss_attempt: javascript: URI detected",
            category="xss_attempt",
            matched_rule="javascript_uri",
        )

    return None


# ---------------------------------------------------------------------------
# XSS / script injection detection
# ---------------------------------------------------------------------------

_XSS_PATTERNS = (
    r"<script[\s>]",
    r"javascript\s*:",
    r"\balert\s*\(",
    r"\bconfirm\s*\(",
    r"\bprompt\s*\(",
    r"on(load|error|click|mouseover|focus|submit)\s*=",
    r"document\.(cookie|location|write)",
    r"window\.(location|open)\s*[=(]",
    r"eval\s*\(",
    r"\bfetch\s*\(",
    r"new\s+Image\s*\(\s*\)\s*\.src",
    r"<iframe[\s>]",
    r"<img[^>]+onerror",
    r"String\.fromCharCode",
    r"atob\s*\(",
)


def detect_xss(*texts: str | None) -> str | None:
    raw = "\n".join(t or "" for t in texts)
    haystack = _normalize_text(raw).lower()
    for pattern in _XSS_PATTERNS:
        if re.search(pattern, haystack):
            return f"xss pattern matched: {pattern}"
    return None

TRUSTED_ORIGINS: set[str] = {
    "accounts.google.com",
    "login.microsoftonline.com",
    "appleid.apple.com",
    "github.com",
    "auth0.com",
}

_extra_origins = os.getenv("AEGIS_TRUSTED_ORIGINS", "")
if _extra_origins:
    TRUSTED_ORIGINS.update(o.strip().lower() for o in _extra_origins.split(",") if o.strip())


# ---------------------------------------------------------------------------
# SecurityVerdict (tri-state)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecurityVerdict:
    verdict: Literal["allow", "approve", "block"]
    reason: str
    category: ActionCategory | None = None
    matched_rule: str | None = None
    requires_human: bool = False

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"


# backward compat alias
PolicyDecision = SecurityVerdict


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _text_from_action(action: Any) -> str:
    parts: list[str] = [str(getattr(action, "type", ""))]
    for attr in ("text", "url", "result"):
        value = getattr(action, attr, None)
        if value:
            parts.append(str(value))
    keys = getattr(action, "keys", None)
    if keys:
        parts.append(" ".join(str(k) for k in keys))
    return " ".join(parts).lower()


def _origin_from_action(action: Any) -> str | None:
    url = getattr(action, "url", None)
    if not url:
        return None
    try:
        return urlparse(str(url)).hostname or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_action(
    action: Any,
    model_message: str | None = None,
) -> SecurityVerdict:
    action_text = _text_from_action(action)

    injection = detect_prompt_injection(model_message, action_text)
    if injection:
        return SecurityVerdict(
            verdict="block",
            reason=injection,
            category="prompt_injection",
            matched_rule=injection,
            requires_human=False,
        )

    url = getattr(action, "url", None)
    if url and getattr(action, "type", "") in ("navigate", "goto", "click"):
        domain_verdict = check_domain(str(url))
        if domain_verdict is not None:
            return domain_verdict

    xss = detect_xss(action_text, getattr(action, "text", None), url)
    if xss:
        return SecurityVerdict(
            verdict="block",
            reason=xss,
            category="xss_attempt",
            matched_rule=xss,
        )

    for category, patterns in _CATEGORY_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, action_text):
                if category == "credential_entry":
                    origin = _origin_from_action(action)
                    if origin and origin.lower() in TRUSTED_ORIGINS:
                        return SecurityVerdict(
                            verdict="allow",
                            reason=f"credential entry on trusted origin {origin}",
                            category=category,
                            matched_rule=pattern,
                        )

                default_verdict = _CATEGORY_VERDICTS[category]
                return SecurityVerdict(
                    verdict=default_verdict,
                    reason=f"{category}: {pattern}",
                    category=category,
                    matched_rule=pattern,
                    requires_human=default_verdict == "approve",
                )

    return SecurityVerdict(verdict="allow", reason="no dangerous pattern matched")


def check_action_policy(action: Any, model_message: str | None = None) -> SecurityVerdict:
    if os.getenv("AEGIS_ALLOW_DANGEROUS_ACTIONS", "").lower() in {"1", "true", "yes"}:
        return SecurityVerdict(verdict="allow", reason="AEGIS_ALLOW_DANGEROUS_ACTIONS override")

    sv = classify_action(action, model_message)

    if sv.verdict in ("approve", "block"):
        return SecurityVerdict(
            verdict=sv.verdict,
            reason=sv.reason,
            category=sv.category,
            matched_rule=sv.matched_rule,
            requires_human=sv.requires_human,
        )

    return sv
