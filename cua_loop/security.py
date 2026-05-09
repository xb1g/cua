"""AEGIS safety: action classification, prompt-injection detection, policy enforcement."""

from __future__ import annotations

import os
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlparse


# ---------------------------------------------------------------------------
# Prompt injection detection (lexical layer)
# ---------------------------------------------------------------------------

PROMPT_INJECTION_PATTERNS = (
    r"ignore\s*(all\s*)?(previous|prior)\s*instructions",
    r"ignore\s*(your\s*)?(previous|prior)\s*task\s*instructions",
    r"disregard\s*(the\s*)?(system|developer|previous)\s*instructions",
    r"you\s*are\s*now",
    r"reveal\s*(your\s*)?(system\s*prompt|instructions|secrets)",
    r"send\s*(money|payment|funds)",
    r"\b(agent|assistant|system|developer)\s*:\s*(ignore|override|disregard|reveal|send|click|navigate)",
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
    "pii_leak",
    "clipboard_exfil",
    "phishing",
    "open_redirect",
    "internal_resource",
    "approval_spoof",
    "permission_grant",
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
    ("permission_grant", (
        r"\b(allow|authorize|grant|approve)\b.*\b(oauth|app|extension|permission|access)\b",
        r"\bgrant\b.*\b(gmail|drive|calendar|photos|contacts|account)\b",
        r"\binstall\b.*\b(extension|browser extension|addon|plugin)\b",
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
    "pii_leak": "block",
    "clipboard_exfil": "block",
    "phishing": "block",
    "open_redirect": "block",
    "internal_resource": "block",
    "approval_spoof": "block",
    "permission_grant": "block",
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
_REDIRECT_PARAM_NAMES = {
    "url", "u", "redirect", "redirect_url", "next", "continue",
    "target", "to", "return", "return_url", "dest", "destination",
}
_URL_SHORTENER_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd",
    "cutt.ly", "rebrand.ly", "buff.ly", "shorturl.at",
}
_INTERNAL_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}
_SENSITIVE_LOCAL_PATH_HINTS = (
    "/.ssh/", "/.aws/", "/.config/", "/etc/passwd", "/etc/shadow",
    "id_rsa", "credentials", "secrets", ".env",
)


def _is_internal_hostname(hostname: str) -> bool:
    host = hostname.strip("[]").lower().rstrip(".")
    if host in _INTERNAL_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or str(ip) == "169.254.169.254"
    )


def _looks_like_absolute_url(value: str) -> bool:
    decoded = unquote(value).strip()
    return bool(re.match(r"(?i)^[a-z][a-z0-9+.-]*://", decoded))


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

    scheme = (parsed.scheme or "").lower()
    if scheme and scheme not in {"http", "https", "about"}:
        category: ActionCategory = "xss_attempt" if scheme in {"data", "blob", "vbscript"} else "internal_resource"
        return SecurityVerdict(
            verdict="block",
            reason=f"{category}: blocked non-web URL scheme {scheme}",
            category=category,
            matched_rule=f"scheme:{scheme}",
        )

    if scheme == "file":
        return SecurityVerdict(
            verdict="block",
            reason="internal_resource: file:// navigation blocked",
            category="internal_resource",
            matched_rule="scheme:file",
        )

    if not hostname:
        return None

    if _is_internal_hostname(hostname):
        return SecurityVerdict(
            verdict="block",
            reason=f"internal_resource: navigation to local/internal host {hostname} blocked",
            category="internal_resource",
            matched_rule="internal_host",
        )

    if parsed.path:
        path_lower = unquote(parsed.path).lower()
        if any(hint in path_lower for hint in _SENSITIVE_LOCAL_PATH_HINTS):
            return SecurityVerdict(
                verdict="block",
                reason=f"internal_resource: sensitive local path requested ({parsed.path})",
                category="internal_resource",
                matched_rule="sensitive_path",
            )

    if hostname in _URL_SHORTENER_DOMAINS:
        return SecurityVerdict(
            verdict="block",
            reason=f"domain_blocked: URL shortener {hostname} hides the final destination",
            category="domain_blocked",
            matched_rule="url_shortener",
        )

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

    for name, value in parse_qsl(parsed.query, keep_blank_values=False):
        if name.lower() not in _REDIRECT_PARAM_NAMES or not _looks_like_absolute_url(value):
            continue
        target = unquote(value).strip()
        target_verdict = check_domain(target)
        if target_verdict is not None:
            return SecurityVerdict(
                verdict="block",
                reason=(
                    f"open_redirect: parameter {name} points to blocked target "
                    f"{target} ({target_verdict.reason})"
                ),
                category="open_redirect",
                matched_rule=f"redirect_param:{name}",
            )
        target_host = (urlparse(target).hostname or "").lower()
        if target_host and target_host != hostname:
            return SecurityVerdict(
                verdict="block",
                reason=f"open_redirect: parameter {name} leaves {hostname} for {target_host}",
                category="open_redirect",
                matched_rule=f"redirect_param:{name}",
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


def detect_approval_spoof(*texts: str | None) -> str | None:
    raw = "\n".join(t or "" for t in texts)
    haystack = _normalize_text(raw).lower()
    if re.search(r"\b(post|fetch|xhr|request)\b.{0,80}/approve\b", haystack):
        return "approval_spoof: page attempted to call the human approval endpoint"
    if "/approve" in haystack and re.search(r'"?approved"?\s*[:=]\s*true', haystack):
        return "approval_spoof: forged approved=true payload"
    return None

# ---------------------------------------------------------------------------
# PII leak detection
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b", "phone_number"),
    (r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b", "ssn"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "email_address"),
    (r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[- ]?\d{4}[- ]?\d{4}[- ]?\d{3,4}\b", "credit_card"),
    (r"\b\d{5}[-\s]?\d{4}\b", "zip_plus4"),
    (r"\b(?:otp|mfa|2fa|verification code|one[- ]time code)\b\D{0,20}\b\d{6}\b", "mfa_code"),
    (r"\bsk-[A-Za-z0-9_-]{20,}\b", "api_key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "aws_access_key"),
    (r"\b(?:seed phrase|recovery phrase|mnemonic)\b.{0,80}\b(?:abandon|ability|able|about|above|absent|absorb|abstract)\b", "recovery_phrase"),
]


def detect_pii(text: str | None) -> tuple[str, str] | None:
    if not text:
        return None
    for pattern, pii_type in _PII_PATTERNS:
        m = re.search(pattern, text)
        if m:
            return pii_type, m.group()
    return None


# ---------------------------------------------------------------------------
# Clipboard exfiltration detection
# ---------------------------------------------------------------------------

_CLIPBOARD_COPY_KEYS = {
    frozenset({"ctrl", "a"}), frozenset({"meta", "a"}), frozenset({"cmd", "a"}),
    frozenset({"command", "a"}),
    frozenset({"ctrl", "c"}), frozenset({"meta", "c"}), frozenset({"cmd", "c"}),
    frozenset({"command", "c"}),
}

_CLIPBOARD_PASTE_KEYS = {
    frozenset({"ctrl", "v"}), frozenset({"meta", "v"}), frozenset({"cmd", "v"}),
    frozenset({"command", "v"}),
}


class ClipboardTracker:
    """Tracks select-all/copy/paste sequences to detect clipboard exfiltration."""

    def __init__(self) -> None:
        self._copied = False
        self._copy_domain: str | None = None
        self._actions: list[str] = []

    def record(self, action: Any, current_url: str | None = None) -> SecurityVerdict | None:
        action_type = getattr(action, "type", "")
        keys = getattr(action, "keys", None) or []
        key_set = frozenset(k.lower() for k in keys)

        if action_type in ("key", "keypress", "hotkey") and key_set in _CLIPBOARD_COPY_KEYS:
            self._copied = True
            self._copy_domain = _extract_domain(current_url) if current_url else None
            self._actions.append("copy")
            return None

        if action_type in ("key", "keypress", "hotkey") and key_set in _CLIPBOARD_PASTE_KEYS:
            if self._copied:
                paste_domain = _extract_domain(getattr(action, "url", None) or current_url) if current_url else None
                if paste_domain and self._copy_domain and paste_domain != self._copy_domain:
                    self._actions.append("paste_cross_domain")
                    return SecurityVerdict(
                        verdict="block",
                        reason=f"clipboard_exfil: copy on {self._copy_domain}, paste on {paste_domain} — cross-domain clipboard exfiltration",
                        category="clipboard_exfil",
                        matched_rule="cross_domain_paste",
                    )
                self._actions.append("paste")

        if action_type == "navigate":
            url = getattr(action, "url", None)
            if url and self._copied:
                new_domain = _extract_domain(url)
                if new_domain and self._copy_domain and new_domain != self._copy_domain:
                    pass  # navigation after copy is suspicious but not yet a paste

        return None

    def check_sequence(self) -> SecurityVerdict | None:
        recent = self._actions[-6:]
        if "copy" in recent and "paste_cross_domain" in recent:
            return SecurityVerdict(
                verdict="block",
                reason="clipboard_exfil: copy→navigate→paste sequence detected across different domains",
                category="clipboard_exfil",
                matched_rule="copy_navigate_paste",
            )
        return None


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(str(url)).hostname
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phishing detection
# ---------------------------------------------------------------------------

_BRAND_DOMAINS: dict[str, set[str]] = {
    "google": {"google.com", "accounts.google.com", "mail.google.com", "drive.google.com"},
    "microsoft": {"microsoft.com", "login.microsoftonline.com", "outlook.com", "live.com"},
    "apple": {"apple.com", "appleid.apple.com", "icloud.com"},
    "github": {"github.com", "github.dev"},
    "facebook": {"facebook.com", "fb.com", "meta.com"},
    "amazon": {"amazon.com", "aws.amazon.com"},
    "paypal": {"paypal.com"},
}

_BRAND_KEYWORDS = set(_BRAND_DOMAINS.keys())

_LEET_MAP = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a"})


def _deleet(s: str) -> str:
    return s.translate(_LEET_MAP)


def detect_phishing(url: str | None, page_text: str | None = None) -> SecurityVerdict | None:
    if not url:
        return None
    domain = _extract_domain(url)
    if not domain:
        return None

    if domain in TRUSTED_ORIGINS:
        return None

    domain_normalized = _deleet(domain)
    for brand, legit_domains in _BRAND_DOMAINS.items():
        if domain in legit_domains:
            return None
        if (brand in domain or brand in domain_normalized) and domain not in legit_domains:
            return SecurityVerdict(
                verdict="block",
                reason=f"phishing: domain {domain} contains '{brand}' but is not a legitimate {brand} domain",
                category="phishing",
                matched_rule=f"brand_impersonation:{brand}",
            )

    if page_text:
        text_lower = page_text.lower()
        has_login_form = any(kw in text_lower for kw in ("password", "sign in", "log in", "enter your credentials"))
        if has_login_form and domain not in TRUSTED_ORIGINS:
            mentioned_brands = [b for b in _BRAND_KEYWORDS if b in text_lower]
            if mentioned_brands:
                return SecurityVerdict(
                    verdict="block",
                    reason=f"phishing: login form on {domain} mentions {', '.join(mentioned_brands)} — possible credential harvesting",
                    category="phishing",
                    matched_rule=f"fake_login:{','.join(mentioned_brands)}",
                )

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

    approval_spoof = detect_approval_spoof(action_text, getattr(action, "text", None), url)
    if approval_spoof:
        return SecurityVerdict(
            verdict="block",
            reason=approval_spoof,
            category="approval_spoof",
            matched_rule="forged_approval",
        )

    typed_text = getattr(action, "text", None)
    if typed_text and getattr(action, "type", "") in ("type", "input"):
        pii = detect_pii(typed_text)
        if pii:
            pii_type, matched = pii
            return SecurityVerdict(
                verdict="block",
                reason=f"pii_leak: {pii_type} detected in outbound text ({matched})",
                category="pii_leak",
                matched_rule=f"pii:{pii_type}",
            )

    if url and getattr(action, "type", "") in ("navigate", "goto", "click"):
        phish = detect_phishing(url)
        if phish:
            return phish

    if url and getattr(action, "type", "") in ("navigate", "goto"):
        phish_text = detect_phishing(url, getattr(action, "text", None))
        if phish_text:
            return phish_text

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
