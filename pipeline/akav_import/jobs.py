"""The canonical job registry, from the 'War Room' calendar workbook.

This workbook is AKAV's master calendar -- 438 jobs, Jan 2025 to Dec 2026 --
and its `BOOK` column is the canonical job code (`ATLOns21`, `BaltAVFX1`,
`DayBS-1`). That column is what standardizes job naming: six of the eight
contract folders are named for their BOOK code exactly, and the live Sheet's
show labels ('Cisco Live 2026') are prose for the same thing.

Why this matters: the same job otherwise appears under several names, which is
the redundancy the rolodex has to collapse. A BOOK code gives every work record
one identity, plus a client, city and real dates.

Matching is by DATE OVERLAP scored with CITY, never by folder name -- two of
the contract folders hold contracts spanning months, i.e. several jobs each, so
folder name is not a reliable job identity.
"""

import datetime
import os
import re

import openpyxl

from .normalize import norm_name

# Header row is not row 1; find it by its first column.
_HEADER_MARK = "estimate"

FIELDS = ["Estimate", "contracts", "cntrctspaid", "final sent", "final paid",
          "Start Date", "End Date", "City", "State", "days", "units",
          "Client", "Contact", "BOOK"]


def _as_date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return None


def load(path):
    """Return [job dicts] from the War Room workbook."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()

    hdr = None
    for i, r in enumerate(rows):
        if r and str(r[0] or "").strip().lower() == _HEADER_MARK:
            hdr = i
            break
    if hdr is None:
        return []
    head = [str(c).strip() if c else "" for c in rows[hdr]]
    idx = {n: head.index(n) for n in FIELDS if n in head}

    out = []
    for r in rows[hdr + 1:]:
        if not any(c is not None and str(c).strip() for c in r):
            continue

        def g(name):
            i = idx.get(name)
            return r[i] if i is not None and i < len(r) else None

        start, end = _as_date(g("Start Date")), _as_date(g("End Date"))
        if start is None and end is None:
            continue
        book = str(g("BOOK") or "").strip()
        out.append({
            "book": book,
            "client": str(g("Client") or "").strip(),
            "contact": str(g("Contact") or "").strip(),
            "city": str(g("City") or "").strip(),
            "state": str(g("State") or "").strip(),
            "start": start or end,
            "end": end or start,
            "days": g("days"),
            # Billing state, straight from the calendar's X marks.
            "estimate_sent": bool(str(g("Estimate") or "").strip()),
            "contracts_sent": bool(str(g("contracts") or "").strip()),
            "contracts_paid": bool(str(g("cntrctspaid") or "").strip()),
            "final_sent": bool(str(g("final sent") or "").strip()),
            "final_paid": bool(str(g("final paid") or "").strip()),
        })
    return out


def _city_key(s):
    """'atl (morrow)' and 'Atlanta ' both reduce to something comparable."""
    t = re.sub(r"\([^)]*\)", " ", str(s or "")).lower()
    t = re.sub(r"[^a-z ]", " ", t)
    return " ".join(t.split())


CITY_SYNONYMS = {
    "atl": "atlanta", "nola": "new orleans", "nyc": "new york",
    "dc": "washington", "wdc": "washington", "balt": "baltimore",
    "mnpls": "minneapolis", "vegas": "las vegas", "philly": "philadelphia",
    "sf": "san francisco", "la": "los angeles",
}


def _city_match(a, b):
    ka, kb = _city_key(a), _city_key(b)
    if not ka or not kb:
        return 0.0
    ka = CITY_SYNONYMS.get(ka, ka)
    kb = CITY_SYNONYMS.get(kb, kb)
    if ka == kb:
        return 1.0
    # 'atlanta' vs 'atl', 'baltimore ' vs 'balt'
    short, long_ = sorted([ka, kb], key=len)
    if len(short) >= 3 and long_.startswith(short):
        return 0.8
    if short in long_.split():
        return 0.7
    return 0.0


# A date overlap ALONE is not identification: on any given week AKAV has jobs
# running in several cities, so "these dates overlap" matched Orlando
# contracts to Boston and Vegas jobs. A confident match needs corroboration --
# the city agreeing, or an explicit BOOK code. Below this, we return no match
# and the work record carries an unknown job rather than a wrong one.
CONFIDENT = 2.9


def match(jobs, dates, city_hint="", book_hint=""):
    """Best job for a set of worked dates. Returns (job, score, why).

    Scoring: date overlap is required; city agreement and an explicit BOOK
    hint break the ties. Returns (None, 0, reason) when nothing overlaps.

    Callers should treat score < CONFIDENT as "unidentified job" -- see the
    constant above for why date overlap on its own is not enough.
    """
    dates = sorted(d for d in dates if d)
    if not dates:
        return None, 0.0, "no dates on the contract"
    lo, hi = dates[0], dates[-1]

    hint = re.sub(r"[^a-z0-9]", "", str(book_hint or "").lower())

    # An exact BOOK code beats date arithmetic. 'Orlando418' names a real job,
    # and if its calendar dates disagree with its own contracts that is a
    # finding about the calendar -- not a reason to hunt for some other city's
    # job that happens to fall on the same week.
    if hint:
        for j in jobs:
            if re.sub(r"[^a-z0-9]", "", j["book"].lower()) == hint:
                inside = sum(1 for d in dates if j["start"] <= d <= j["end"])
                if inside == len(dates):
                    return j, 5.0, "exact BOOK code, all dates inside"
                return j, 4.0, (
                    "exact BOOK code, but only %d/%d contract days fall in the "
                    "calendar window %s..%s" % (inside, len(dates),
                                                j["start"], j["end"]))

    best, best_score, why = None, 0.0, "no overlapping job in the calendar"

    for j in jobs:
        if j["start"] > hi or j["end"] < lo:
            continue
        # Fraction of this contract's days that fall inside the job window.
        inside = sum(1 for d in dates if j["start"] <= d <= j["end"])
        score = 2.0 * (inside / len(dates))
        cm = _city_match(city_hint, j["city"])
        score += 1.5 * cm
        jb = re.sub(r"[^a-z0-9]", "", j["book"].lower())
        if hint and jb and hint == jb:
            score += 3.0
        elif hint and jb and (hint.startswith(jb) or jb.startswith(hint)):
            score += 1.0
        # Prefer a tight window over a job that merely contains the dates.
        span = (j["end"] - j["start"]).days + 1
        score += max(0.0, 0.5 - 0.02 * span)
        if score > best_score:
            best, best_score = j, score
            why = "%d/%d days inside, city %.0f%%" % (
                inside, len(dates), cm * 100)
    return best, round(best_score, 2), why


def index_by_book(jobs):
    out = {}
    for j in jobs:
        key = re.sub(r"[^a-z0-9]", "", j["book"].lower())
        if key:
            out.setdefault(key, j)
    return out


# ── The Jobs tab ───────────────────────────────────────────

JOB_TAB_FIELDS = [
    "book", "client", "contact", "city", "state", "start", "end", "days",
    "contracts_found", "people_contracted", "billing",
    "discrepancy", "notes",
]

# Discrepancies live HERE rather than on the people Review tab: they are about
# a job, not a person, and would sort as nameless rows over there. Keeping them
# inline on the registry means the tab is useful reference AND the problem
# list, instead of a bare list of complaints nobody opens.
DISCREPANCY_NOTES = {
    "dates_outside_calendar":
        "Contracts for this job are dated outside its calendar window. Either "
        "the job moved and the calendar wasn't updated, or those contracts are "
        "filed under the wrong job.",
    "no_book_code":
        "This job has no BOOK code, so nothing can be reliably filed against "
        "it. 87% of jobs overlap another, so dates alone can't identify it.",
    "contracts_unfiled":
        "Signed contracts exist that don't name a BOOK code, so we can't tell "
        "which job they belong to. Which job are these?",
    "paid_without_contracts":
        "Marked as paid, but no contracts were marked sent.",
    "contracts_sent_not_paid":
        "Contracts sent but not marked paid — may just be pending.",
    "duplicate_book":
        "This BOOK code appears on more than one calendar row, with different "
        "billing states. Which row is the real one?",
    "likely_year_typo":
        "The contracts are almost exactly one year off the calendar dates — "
        "this looks like a mistyped year on the calendar rather than a real "
        "scheduling difference.",
}


def is_book_code(value):
    """Is this an actual job code, or a note someone typed in the BOOK cell?

    Real codes are one token of city+client+number: 'ATLOns21', 'BaltAVFX1',
    'DayBS-1'. The column also carries multi-word placeholder prose, which
    must not be treated as identities.
    """
    v = str(value or "").strip()
    # A trailing client reference is fine: 'ATLCT1 (CTNY001226)'.
    v = re.sub(r"\([^)]*\)", " ", v).strip()
    if len(v) < 4:
        return False
    # 'ATLOns14 & ATLOns15' is two real codes joined -- allow that one shape.
    parts = [p.strip() for p in re.split(r"&|\+", v) if p.strip()]
    if len(parts) > 1:
        return all(is_book_code(p) for p in parts)
    # The real signal is that a code is ONE token. Codes may lack a number
    # ('AustinCT', 'BethesdaAVFX'); placeholders are prose (notes-to-self,
    # maybes, headcounts) and always contain a space.
    if " " in v:
        return False
    return bool(re.search(r"[A-Za-z]{3}", v))


def billing_state(job):
    """Compact billing summary, in the calendar's own order."""
    steps = [("est", job["estimate_sent"]), ("contracts", job["contracts_sent"]),
             ("cpaid", job["contracts_paid"]), ("final", job["final_sent"]),
             ("fpaid", job["final_paid"])]
    done = [n for n, v in steps if v]
    return " → ".join(done) if done else "not started"


def audit(jobs, contract_index=None):
    """Return rows for the Jobs tab, with discrepancies flagged.

    contract_index: {book_code_lower: {"files": n, "people": set, "dates": set}}
    """
    contract_index = contract_index or {}

    # A BOOK code on two calendar rows means two records of one job, usually
    # with the billing half-updated on one of them. Only count real codes:
    # the column also holds free-text placeholders and notes-to-self,
    # which are not duplicates of each other, they are missing codes.
    seen = {}
    for j in jobs:
        if not is_book_code(j["book"]):
            continue
        k = re.sub(r"[^a-z0-9]", "", j["book"].lower())
        seen[k] = seen.get(k, 0) + 1
    dupes = {k for k, n in seen.items() if n > 1}

    rows = []
    for j in jobs:
        key = re.sub(r"[^a-z0-9]", "", j["book"].lower())
        found = contract_index.get(key, {})
        flags, notes = [], []

        if not is_book_code(j["book"]):
            flags.append("no_book_code")
        if key in dupes:
            flags.append("duplicate_book")
        dates = found.get("dates") or set()
        if dates and (min(dates) < j["start"] or max(dates) > j["end"]):
            flags.append("dates_outside_calendar")
            notes.append("contracts run %s..%s, calendar says %s..%s"
                         % (min(dates), max(dates), j["start"], j["end"]))
            # ~365 days off in either direction is a mistyped year, not a
            # rescheduled job.
            off = abs((min(dates) - j["start"]).days)
            if 360 <= off <= 370:
                flags.append("likely_year_typo")
        if j["final_paid"] and not j["contracts_sent"]:
            flags.append("paid_without_contracts")

        rows.append({
            "book": j["book"],
            "client": j["client"],
            "contact": j["contact"],
            "city": j["city"],
            "state": j["state"],
            "start": j["start"].isoformat() if j["start"] else "",
            "end": j["end"].isoformat() if j["end"] else "",
            "days": j["days"] or "",
            "contracts_found": found.get("files", 0),
            "people_contracted": len(found.get("people") or ()),
            "billing": billing_state(j),
            "discrepancy": ", ".join(flags),
            "notes": "; ".join(
                notes + [DISCREPANCY_NOTES[f] for f in flags
                         if f in DISCREPANCY_NOTES]),
        })
    rows.sort(key=lambda r: (not r["discrepancy"], r["start"]))
    return rows


def write_jobs_tab(rows, path):
    import csv as _csv
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=JOB_TAB_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def find_war_room(source_dir):
    """Locate the War Room workbook under a Source Files tree."""
    for dirpath, _, files in os.walk(source_dir):
        for f in files:
            if f.lower().startswith("war room") and f.lower().endswith(".xlsx"):
                return os.path.join(dirpath, f)
    return None
