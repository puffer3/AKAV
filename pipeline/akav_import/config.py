"""Where the source material lives, and what must never be read from it.

The client's raw files -- rolodexes, the phone export, signed contracts, the
QuickBooks vendor list -- are deliberately kept OUTSIDE this repo. The repo is
public and deploys via GitHub Pages, and the vendor export alone carries 1,260
Social Security Numbers. Keeping them out means no `.gitignore` mistake, no
`git add -f`, and no stray tool can publish them.

Override with the AKAV_SOURCE_DIR environment variable.
"""

import os
import re

DEFAULT_SOURCE_DIR = os.path.expanduser("~/AKAV-private")


def source_dir():
    return os.environ.get("AKAV_SOURCE_DIR") or DEFAULT_SOURCE_DIR


def source_path(*parts):
    return os.path.join(source_dir(), *parts)


def rollies_dir():
    return source_path("Source Files", "Rollies")


def contracts_dir():
    return source_path("Source Files", "Contracts")


def contacts_vcf():
    return source_path("Source Files", "All Contacts.vcf")


def vendor_csv():
    return source_path("Source Files", "AKAV LLC_Vendor Contact List.csv")


def out_dir():
    """Local working output. Gitignored; still keep PII out of the repo where
    practical -- prefer writing derived files under the private dir."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "out")


# ── What counts as a DAY RATE ──────────────────────────────

# Only a contracted per-day figure is a day rate. Crew Status '$ due' and the
# workbook totals are PAYROLL -- what someone was paid across a whole job,
# often several days plus OT, meal penalty and adjustments. Dividing that by
# days would manufacture a rate nobody agreed to, so these columns are never
# read as rate data. (Henry, explicitly: "those payroll are NOT day rates and
# we dont need them".)
PAYROLL_NOT_RATE = {
    "$ due", "due", "total", "total due", "amount", "amount due", "paid",
    "invoice", "estimate", "gross", "net", "subtotal", "balance",
}

# Rate-ish headers that ARE a per-day figure.
DAY_RATE_HEADERS = {"rate", "day rate", "client rate", "daily rate"}


def is_payroll_total(header):
    return re.sub(r"\s+", " ", str(header or "").strip().lower()) in PAYROLL_NOT_RATE


# ── Fields we refuse to carry ──────────────────────────────

# Dropped at parse time, not merely "not used" -- so they cannot reach an
# intermediate JSON, a debug print, or a preview table by accident.
# Tax IDs stay in QuickBooks, which files the 1099s. Bank details likewise.
FORBIDDEN_FIELD_RE = re.compile(
    r"(?i)^(tax\s*id|ssn|social(\s*security)?(\s*(no|num|number))?|ein|tin|"
    r"account\s*#?|account\s*(no|num|number)|routing(\s*(no|num|number))?|"
    r"bank\s*account|iban|swift|card\s*(no|num|number)|cvv)$")

# Values that look like a bare SSN, in case one turns up in a free-text cell.
#
# NB: a nine-digit run is ambiguous -- a ZIP+4 written without the dash
# ('782104340') has exactly the same shape. Apply this to FREE-TEXT fields
# only; never to a postcode field, or valid data gets destroyed.
SSN_VALUE_RE = re.compile(r"^\s*\d{3}-?\d{2}-?\d{4}\s*$")


def is_forbidden_field(name):
    return bool(FORBIDDEN_FIELD_RE.match(str(name or "").strip()))


def scrub_row(row):
    """Drop forbidden columns from a {header: value} row before it goes
    anywhere. Returns (clean_row, dropped_field_names)."""
    clean, dropped = {}, []
    for k, v in (row or {}).items():
        if is_forbidden_field(k):
            dropped.append(k)
            continue
        clean[k] = v
    return clean, dropped


def looks_like_ssn(value):
    return bool(SSN_VALUE_RE.match(str(value or "")))
