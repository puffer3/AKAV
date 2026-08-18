"""Parse the QuickBooks 'Vendor Contact List' export -- for ADDRESSES.

This is the shortcut around parsing ~10k invoice emails: QuickBooks already
holds a billing address for everyone AKAV has paid, because 1099 filing
requires one. 1,767 of the 1,881 people in the export have one, and 1,345 of
them match someone already in the rolodex.

SECURITY: this export also contains Tax IDs, most of them bare 9-digit
numbers (i.e. Social Security Numbers). Those are dropped at parse time by
`config.scrub_row` and never returned, never logged, never written. QuickBooks
files the 1099s; the rolodex has no workflow that needs a TIN. The useful
signal -- "do we have their tax paperwork" -- is the existing W-9 status flag.

An address here is a BILLING address, which for a freelancer is usually home
but can be a PO box or an LLC's registered address. It is the best evidence we
have for `home_base`, and better than guessing from which rolodex someone
appears on -- but it is still evidence, not proof.
"""

import csv
import re

from .config import is_forbidden_field, looks_like_ssn
from .names import split_aka
from .normalize import norm_email, norm_name, norm_phone

# The export has a title block before the real header row.
HEADER_MARKERS = {"vendor", "full name", "billing address"}

# 'Phone:+14108052052 ' / 'Mobile:+1 555 123 4567'
PHONE_RE = re.compile(r"(?:\+?1)?\D?(\d{3})\D?(\d{3})\D?(\d{4})")

# 'P.O. BOX 15372 Middle River MD 21220 USA' -> state + zip at the end. ZIP is
# sometimes 9 digits with no dash ('782104340').
ADDR_TAIL_RE = re.compile(
    r"\b(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-?\d{4})?)\s*(?:USA?)?\s*$")

# Street-type words. The city is whatever follows the LAST of these.
STREET_SUFFIX = {
    "st", "street", "ave", "av", "avenue", "rd", "road", "dr", "drive",
    "ln", "lane", "ct", "court", "cir", "circle", "blvd", "boulevard",
    "way", "pl", "place", "ter", "terrace", "trl", "trail", "pkwy",
    "parkway", "hwy", "highway", "cv", "cove", "run", "row", "loop",
    "bnd", "bend", "xing", "crossing", "sq", "square", "pt", "point",
    "ridge", "rdg", "creek", "crk", "park", "path", "walk", "gln", "glen",
    "box", "pike", "plz", "plaza", "aly", "alley", "mnr", "manor",
}

# Unit markers -- the city never precedes these.
UNIT_WORD = {"apt", "unit", "ste", "suite", "no", "num", "fl", "floor",
             "bldg", "rm", "room", "trlr", "lot", "spc"}

# US cities whose name BEGINS with St/St. -- the only case where an 'st'
# token belongs to the city instead of the street. Everything else stays a
# street suffix: the corpus has 28 addresses that write the suffix WITH a
# period ('Gloucester Gate St. Las Vegas'), so "St.-means-Saint" is not a
# safe rule on its own.
SAINT_CITIES = {
    "st louis", "st paul", "st petersburg", "st augustine", "st charles",
    "st cloud", "st george", "st peters", "st johns", "st albans",
    "st matthews", "st pete beach", "st rose", "st bernard", "st gabriel",
    "st francisville", "st joseph", "st simons island",
}


def _find_header(rows):
    for i, r in enumerate(rows[:20]):
        cells = {str(c).strip().lower() for c in r if c}
        if len(cells & HEADER_MARKERS) >= 2:
            return i
    return None


def split_address(raw):
    """Best-effort (street, city, state, zip) from one flat address string.

    The addresses are a single unpunctuated run: '2785 Walton Creek Dr
    Colorado Springs CO 80922 USA'. There is no comma to split on, so the city
    is found by walking BACKWARDS from the state, taking word-only tokens until
    a street suffix ('Dr'), a unit marker ('Apt') or anything containing a
    digit ('#1', '15372') stops it.

    Matching forwards instead grabs part of the street -- 'Walton Creek D',
    'Breeze Ter Aus' -- which is worse than no city at all.
    """
    s = re.sub(r"\s+", " ", str(raw or "")).strip().rstrip(",")
    if not s:
        return {"address": "", "city": "", "state": "", "zip": "", "full": ""}

    m = ADDR_TAIL_RE.search(s)
    if not m:
        return {"address": s, "city": "", "state": "", "zip": "", "full": s}

    head = s[: m.start()].strip(" ,")
    tokens = head.split()
    city_parts = []
    for tok in reversed(tokens):
        bare = tok.strip(".,#").lower()
        if not tok or any(ch.isdigit() for ch in tok):
            break
        # 'St'/'St.' before an already-collected city is ambiguous: street
        # suffix in '...Gate St. Las Vegas', city prefix in '...St St. Louis'.
        # Only a KNOWN Saint city claims the token; otherwise it is a suffix.
        if bare == "st" and city_parts:
            cand = re.sub(r"[^a-z ]", "",
                          ("st " + " ".join(reversed(city_parts))).lower())
            if cand in SAINT_CITIES:
                city_parts.append(tok)
            break
        if bare in STREET_SUFFIX or bare in UNIT_WORD:
            break
        if not re.match(r"^[A-Za-z][A-Za-z.'\-]*$", tok):
            break
        city_parts.append(tok)
        if len(city_parts) >= 4:      # no US city name is longer
            break
    city = " ".join(reversed(city_parts)).strip(" ,")
    street = head[: len(head) - len(city)].strip(" ,") if city else head

    return {
        "address": street,
        "city": city,
        "state": m.group("state"),
        "zip": m.group("zip"),
        "full": s,
    }


def _phone(raw):
    m = PHONE_RE.search(str(raw or ""))
    return "".join(m.groups()) if m else ""


def parse(path):
    """Return (people, stats). Tax IDs are dropped, never returned."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.reader(fh))

    hi = _find_header(rows)
    if hi is None:
        return [], {"error": "no header row found"}
    head = [str(c).strip() for c in rows[hi]]

    # Refuse the sensitive columns up front, by index.
    keep = [i for i, h in enumerate(head) if h and not is_forbidden_field(h)]
    dropped_cols = [h for h in head if h and is_forbidden_field(h)]

    people = []
    stats = {"rows": 0, "people_1099": 0, "with_address": 0,
             "dropped_columns": dropped_cols, "scrubbed_values": 0}

    idx = {head[i]: i for i in keep}

    # Without 'Track 1099' every row is skipped and parse() returns [] with no
    # complaint -- a silent empty import that looks like "no people found".
    missing = [c for c in ("Vendor", "Track 1099") if c not in idx]
    if missing:
        stats["error"] = "expected column(s) missing: %s" % ", ".join(missing)
        stats["header_seen"] = head
        return [], stats

    def g(r, name):
        i = idx.get(name)
        return str(r[i]).strip() if i is not None and i < len(r) else ""

    for r in rows[hi + 1:]:
        if not any(str(c).strip() for c in r):
            continue
        stats["rows"] += 1

        # 'Track 1099' = Yes marks a person AKAV pays, vs 7-Eleven or AAA.
        is_person = g(r, "Track 1099").lower() == "yes"
        if not is_person:
            continue
        stats["people_1099"] += 1

        raw_name = g(r, "Full name") or g(r, "Vendor")
        name, akas, ambiguous = split_aka(raw_name)
        addr = split_address(g(r, "Billing address"))
        if addr.get("full") or addr.get("address"):
            stats["with_address"] += 1

        rec = {
            "name": name,
            "aka": akas,
            "name_ambiguous": ambiguous,
            "vendor": g(r, "Vendor"),
            "company": g(r, "Company"),
            "email": norm_email(g(r, "Email")),
            "phoneDigits": _phone(g(r, "Phone numbers")),
            "home_base": addr.get("city", ""),
            "state": addr.get("state", ""),
            "zip": addr.get("zip", ""),
            "address": addr.get("full") or addr.get("address", ""),
            "source": "QuickBooks Vendor Contact List",
        }

        # Belt and braces: if an SSN turned up inside a free-text field we
        # kept, blank that field rather than carrying it.
        #
        # 'zip' is exempt: a ZIP+4 written without the dash ('782104340') is
        # nine digits and matches the SSN shape exactly. Scrubbing it destroyed
        # 54 valid postcodes. Only free-text fields are checked -- an SSN would
        # never legitimately appear in them anyway.
        for k in ("name", "vendor", "company", "address"):
            v = rec.get(k)
            if isinstance(v, str) and looks_like_ssn(v):
                rec[k] = ""
                stats["scrubbed_values"] += 1

        if rec["name"] or rec["email"]:
            people.append(rec)

    return people, stats


def index_for_match(people):
    """Lookup tables for merging onto the rolodex: email, phone, name."""
    by_email, by_phone, by_name = {}, {}, {}
    for p in people:
        if p["email"]:
            by_email.setdefault(p["email"], p)
        if p["phoneDigits"]:
            by_phone.setdefault(p["phoneDigits"], p)
        n = norm_name(p["name"])
        if n:
            by_name.setdefault(n, p)
    return {"email": by_email, "phone": by_phone, "name": by_name}
