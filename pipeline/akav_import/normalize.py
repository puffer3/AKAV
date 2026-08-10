"""Identity + value normalization.

norm_email / norm_phone / norm_name MUST stay byte-for-byte consistent with
normEmail / normPhone / normName in "AKAV Onboarding/google-apps-script.gs" —
both sides key person matching on their output.
"""

import re
import unicodedata
from datetime import date, datetime, time

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COMBINING = re.compile("[\u0300-\u036f]")


def norm_email(s):
    return str(s or "").strip().lower()


def norm_phone(s):
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def norm_name(s):
    t = str(s or "").lower()
    t = unicodedata.normalize("NFD", t)
    t = _COMBINING.sub("", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def slugify(label):
    t = norm_name(label)
    t = re.sub(r"\s+", "-", t)
    return t or "unknown-show"


# ── Cell-value coercion ────────────────────────────────────


def is_blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def clean_str(v):
    if v is None:
        return ""
    return str(v).strip()


def looks_like_email(v):
    return isinstance(v, str) and bool(EMAIL_RE.match(v.strip()))


def looks_like_phone(v):
    """Phone-ish: mostly digits/punctuation, 7-15 digits."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(int(v))
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s or looks_like_email(s):
        return False
    digits = re.sub(r"\D", "", s)
    non_phone_chars = re.sub(r"[\d\s\-\(\)\+\.]", "", s)
    return 7 <= len(digits) <= 15 and not non_phone_chars


def to_iso_date(v):
    """datetime/date cell → 'YYYY-MM-DD'; else '' """
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return ""


def to_hhmm(v):
    """time/datetime cell → 'HH:MM'; passthrough for HH:MM strings."""
    if isinstance(v, time):
        return "%02d:%02d" % (v.hour, v.minute)
    if isinstance(v, datetime):
        return "%02d:%02d" % (v.hour, v.minute)
    if isinstance(v, str):
        m = re.match(r"^\s*(\d{1,2}):(\d{2})", v)
        if m:
            return "%02d:%02d" % (int(m.group(1)), int(m.group(2)))
    return ""


def to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace("$", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None
