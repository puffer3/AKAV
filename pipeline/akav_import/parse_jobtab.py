"""Parse job workbooks that live as TABS inside the rolodex files.

Twenty rolodex tabs are complete job workbooks rather than crew lists -- a
header block (Show / Venue / Client / Job Number / Estimate / Invoice) above a
crew table (Date | Position | Name | Phone | Email | ...). They were being
skipped as `kind=show`; they hold 432 crew-day rows.

Nineteen of the twenty predate AKAV LLC (the ShowPhaze era). Rules for
that era:

  * take WHO WORKED WHAT -- person, position, dates
  * take NO grades: none were recorded then
  * take NO rates: AKAV did not run payroll then, so the money on these
    sheets is not AKAV pay data and must not feed the day-rate figures
  * label the job 'SHOWPHAZE <job>' so the roster never implies AKAV did work
    it did not do

Post-2025 tabs are AKAV's own and keep their plain label.
"""

import datetime
import re

from .names import split_aka
from .normalize import (clean_str, looks_like_email, looks_like_phone,
                        norm_email, norm_phone)
from .roles import RoleResolver

# AKAV LLC's calendar starts here; anything before is the ShowPhaze era.
AKAV_START = datetime.date(2025, 1, 1)
SHOWPHAZE_PREFIX = "SHOWPHAZE"
# No usable dates -> era can't be established, so say so rather than
# defaulting to AKAV.
UNDATED_PREFIX = "[UNDATED]"

# Header block labels, e.g. 'Show:' / 'Venue:' / 'Job Number:'
META_KEYS = {
    "show": "show", "venue": "venue", "address": "address",
    "client": "client", "job number": "job_number", "checkin": "checkin",
    "estimate": "estimate", "invoice": "invoice",
    "crew coordinator": "coordinator", "onsite contact": "onsite_contact",
    "attire": "attire", "event": "show",
}

# The crew table's header row.
CREW_HEADERS = {"date", "position", "name", "phone", "email"}

# Columns we deliberately do NOT read from this era.
EXCLUDED_HEADERS = {"rate", "client rate", "grade", "pay", "day rate",
                    "total", "ot", "dt", "cc", "sow"}


def _norm_key(s):
    return re.sub(r"\s+", " ", clean_str(s).lower()).strip(": ")


def read_meta(rows, limit=8):
    """Pull the header block above the crew table."""
    meta = {}
    for r in rows[:limit]:
        cells = [clean_str(c) for c in r]
        for i, c in enumerate(cells):
            key = META_KEYS.get(_norm_key(c))
            if not key:
                continue
            for j in range(i + 1, min(i + 3, len(cells))):
                if cells[j]:
                    meta.setdefault(key, cells[j])
                    break
    return meta


def find_crew_header(rows):
    """Index of the 'Date | Position | Name | ...' row, or -1."""
    for i, r in enumerate(rows[:20]):
        keys = {_norm_key(c) for c in r if clean_str(c)}
        if len(keys & CREW_HEADERS) >= 3:
            return i
    return -1


def job_label(meta, tab_name, era):
    """'SHOWPHAZE BCG WWOM November 2024' / 'Symposium'.

    An UNKNOWN era must not produce a bare label: a bare label reads as AKAV's
    own work, which is exactly what this module exists to prevent. Undated
    tabs are marked so they surface for review instead.
    """
    name = meta.get("show") or tab_name or "Untitled job"
    name = re.sub(r"\s+", " ", str(name)).strip()
    if era == "showphaze":
        return "%s %s" % (SHOWPHAZE_PREFIX, name)
    if era == "unknown":
        return "%s %s" % (UNDATED_PREFIX, name)
    return name


def parse_tab(ws, tab_name, resolver=None):
    """Return (job dict, [work records]).

    Work records carry person + position + date only. Rates and grades are
    never read for the ShowPhaze era -- see the module docstring.
    """
    resolver = resolver or RoleResolver()
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    meta = read_meta(rows)
    hi = find_crew_header(rows)
    if hi < 0:
        return None, []

    head = [_norm_key(c) for c in rows[hi]]
    col = {}
    for i, h in enumerate(head):
        if h in EXCLUDED_HEADERS:
            continue                      # never even index the money columns
        if h in ("date", "position", "name", "phone", "email", "notes"):
            col.setdefault(h, i)

    records, dates = [], []
    for r in rows[hi + 1:]:
        if not any(c is not None and str(c).strip() for c in r):
            continue

        def g(key):
            i = col.get(key)
            return r[i] if i is not None and i < len(r) else None

        raw_name = clean_str(g("name"))
        if not raw_name or looks_like_phone(raw_name) or looks_like_email(raw_name):
            continue
        name, akas, _ = split_aka(raw_name)
        if not name:
            continue

        d = g("date")
        d = d.date() if isinstance(d, datetime.datetime) else (
            d if isinstance(d, datetime.date) else None)
        if d:
            dates.append(d)

        position_raw = clean_str(g("position"))
        records.append({
            "name": name,
            "aka": akas,
            "phoneDigits": norm_phone(g("phone")) if looks_like_phone(
                clean_str(g("phone"))) else "",
            "email": norm_email(g("email")) if looks_like_email(
                clean_str(g("email"))) else "",
            "date": d.isoformat() if d else "",
            "position_raw": position_raw,
            "positions": resolver.resolve_cell(position_raw),
            "notes": clean_str(g("notes")),
        })

    # Dates decide the era, so NO dates must not quietly mean "AKAV". A tab
    # whose Date column is text-formatted yields no date objects at all, and
    # defaulting to AKAV would label ShowPhaze work as AKAV's own -- exactly
    # what this module exists to prevent.
    era_showphaze = bool(dates) and max(dates) < AKAV_START
    era = "showphaze" if era_showphaze else ("akav" if dates else "unknown")
    job = {
        "label": job_label(meta, tab_name, era),
        "tab": tab_name,
        "era": era,
        "client": meta.get("client", ""),
        "venue": meta.get("venue", ""),
        "job_number": meta.get("job_number", ""),
        "coordinator": meta.get("coordinator", ""),
        "start": min(dates).isoformat() if dates else "",
        "end": max(dates).isoformat() if dates else "",
        "crew_rows": len(records),
        "people": len({(r["name"] or "").lower() for r in records}),
        # Explicitly recorded so nobody later wonders whether we lost them.
        "rates_read": False,
        "grades_read": False,
    }
    return job, records
