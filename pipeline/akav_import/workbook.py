"""Sheet classification and column-role inference.

The workbooks are all variations of one evolving personal template, so we
classify by sheet name first, then confirm by content. Column roles are
inferred from the *data* (value shapes), not headers — headers in these
files are unreliable (the Name header in AnaheimPRG's Workbook is
literally '  ', and tidy sheets have no header row at all).
"""

import re
from datetime import date, datetime, time

from .normalize import (
    clean_str, is_blank, looks_like_email, looks_like_phone, to_number,
)

ROLE_TIDY = "TIDY"
ROLE_WB = "WB_GRID"
ROLE_STATUS = "CREW_STATUS"
ROLE_IGNORE = "IGNORE"

_IGNORE_PAT = re.compile(
    r"grid|p&l|estimate|totals|areas key|^sheet\d+$|old wb", re.I)

WB_HEADER_KEYWORDS = {
    "date", "position", "name", "phone", "email",
    "call start", "call end", "notes", "ot", "dt",
}
STATUS_HEADER_KEYWORDS = {
    "contractor", "name", "number", "email", "contract", "sent",
    "$ due", "due date", "pay date", "total",
}

RATE_MIN, RATE_MAX = 50, 5000
SCAN_ROWS = 40  # rows sampled for classification / column inference


def _rows(ws, limit=None):
    out = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if limit is not None and i >= limit:
            break
        out.append(row)
    return out


def find_header_row(rows, keywords, max_scan=15, min_hits=3):
    """Return (row_index, header_cells) of the first row matching >= min_hits
    keywords (cells stripped + lowercased), or (None, None)."""
    for i, row in enumerate(rows[:max_scan]):
        cells = [clean_str(c).lower() for c in row]
        hits = sum(1 for c in cells if c in keywords)
        if hits >= min_hits:
            return i, cells
    return None, None


class ColProfile:
    __slots__ = ("dates", "times", "numbers", "rates", "emails", "phones",
                 "texts", "filled", "values")

    def __init__(self):
        self.dates = self.times = self.numbers = self.rates = 0
        self.emails = self.phones = self.texts = self.filled = 0
        self.values = []          # distinct-ish sample of text values

    def add(self, v):
        if is_blank(v):
            return
        self.filled += 1
        if isinstance(v, bool):
            return
        if isinstance(v, datetime):
            # openpyxl gives midnight datetimes for date cells and
            # 1899-based datetimes are not produced for pure times
            if (v.hour, v.minute, v.second) != (0, 0, 0) and v.year <= 1900:
                self.times += 1
            else:
                self.dates += 1
            return
        if isinstance(v, time):
            self.times += 1
            return
        if isinstance(v, date):
            self.dates += 1
            return
        if isinstance(v, (int, float)):
            self.numbers += 1
            if RATE_MIN <= float(v) <= RATE_MAX:
                self.rates += 1
            return
        s = v.strip()
        if looks_like_email(s):
            self.emails += 1
        elif looks_like_phone(s):
            self.phones += 1
        else:
            self.texts += 1
            if len(self.values) < 200:
                self.values.append(s)


def profile_columns(rows):
    ncols = max((len(r) for r in rows), default=0)
    profs = [ColProfile() for _ in range(ncols)]
    for r in rows:
        for i in range(ncols):
            v = r[i] if i < len(r) else None
            profs[i].add(v)
    return profs


_NAMEISH = re.compile(r"^[A-ZÁÉÍÓÚÑ][\w'\-\.áéíóúñ]*( [A-ZÁÉÍÓÚÑ][\w'\-\.áéíóúñ]*\.?)+ *$")


def _name_score(prof):
    """How many sampled text values look like 'Firstname Lastname'."""
    return sum(1 for v in prof.values if _NAMEISH.match(v))


def infer_columns(rows):
    """Infer column roles from data rows. Returns a dict of role -> col index
    (0-based), roles missing when not found:
    date, position, start, end, rate, area, name, phone, email, notes[list]
    """
    profs = profile_columns(rows)
    n = len(profs)
    cols = {}
    used = set()

    def take(role, idx):
        if idx is not None and 0 <= idx < n:
            cols[role] = idx
            used.add(idx)

    # date: column with most date-typed values
    date_idx = max(range(n), key=lambda i: profs[i].dates, default=None)
    if date_idx is None or profs[date_idx].dates < 3:
        date_idx = None
    take("date", date_idx)

    # times: first two time-typed columns left to right
    time_idxs = [i for i in range(n) if profs[i].times >= 3 and i not in used]
    if len(time_idxs) >= 2:
        take("start", time_idxs[0])
        take("end", time_idxs[1])

    # email / phone
    email_idx = max(range(n), key=lambda i: profs[i].emails, default=None)
    if email_idx is not None and profs[email_idx].emails >= 2:
        take("email", email_idx)
    phone_idx = max(
        (i for i in range(n) if i not in used),
        key=lambda i: profs[i].phones, default=None)
    if phone_idx is not None and profs[phone_idx].phones >= 2:
        take("phone", phone_idx)

    # rate: numeric column with most values in the plausible day-rate band
    rate_idx = max(
        (i for i in range(n) if i not in used),
        key=lambda i: profs[i].rates, default=None)
    if rate_idx is not None and profs[rate_idx].rates >= 1:
        take("rate", rate_idx)

    # position: first text column right of the date column
    if date_idx is not None:
        for i in range(date_idx + 1, n):
            if i not in used and profs[i].texts >= 3:
                take("position", i)
                break

    # name: remaining text column that most looks like people names.
    # Require real variety — a constant column ('Tech Blacks' on every row)
    # is not a name column no matter how name-shaped its value is.
    def _name_candidate(i):
        p = profs[i]
        if i in used or p.texts < 3:
            return False
        distinct = set(p.values)
        return len(distinct) >= 3 and len(distinct) >= 0.3 * len(p.values)

    name_idx = max(
        (i for i in range(n) if _name_candidate(i)),
        key=lambda i: _name_score(profs[i]), default=None)
    if name_idx is not None and _name_score(profs[name_idx]) >= 3:
        take("name", name_idx)

    # area: low-cardinality short-text column (BO / GS / Tech Blacks)
    area_idx = None
    for i in range(n):
        if i in used or profs[i].texts < 3:
            continue
        distinct = set(profs[i].values)
        if 1 <= len(distinct) <= 6 and all(len(v) <= 15 for v in distinct):
            area_idx = i
            break
    take("area", area_idx)

    # notes: leftover text columns, minus constant long-text columns
    # (e.g. a show name repeated on every row carries no information)
    notes = []
    for i in range(n):
        if i in used or profs[i].texts < 1:
            continue
        distinct = set(profs[i].values)
        if len(distinct) <= 2 and any(len(v) > 15 for v in distinct):
            continue
        notes.append(i)
    cols["notes"] = notes
    return cols


# ── Classification ─────────────────────────────────────────


def classify_sheets(wb):
    """Return (roles, log): roles = {sheet_name: role}, log = list of
    human-readable classification decisions for the preview."""
    roles, log = {}, []
    for ws in wb.worksheets:
        name = ws.title.strip().lower()
        role = None
        if _IGNORE_PAT.search(name):
            role = ROLE_IGNORE
        elif "scratch" in name or "show detail" in name:
            role = _confirm_tidy(ws) or ROLE_IGNORE
        elif "crew status" in name:
            role = _confirm_status(ws) or ROLE_IGNORE
        elif name in ("workbook", "wb - live") or name.startswith("wb"):
            role = _confirm_wb(ws) or ROLE_IGNORE
        else:
            # unknown name: try content in priority order
            role = (_confirm_tidy(ws) or _confirm_status(ws)
                    or _confirm_wb(ws) or ROLE_IGNORE)
        roles[ws.title] = role
        log.append("%-32s -> %s" % (repr(ws.title), role))
    return roles, log


def _confirm_tidy(ws):
    rows = [r for r in _rows(ws, SCAN_ROWS) if any(not is_blank(c) for c in r)]
    if len(rows) < 3:
        return None
    cols = infer_columns(rows)
    if "date" in cols and "name" in cols and ("rate" in cols or "start" in cols):
        # tidy sheets have data from row 1 (no header row)
        first = rows[0]
        if any(isinstance(c, (datetime, date)) for c in first):
            return ROLE_TIDY
    return None


def _confirm_wb(ws):
    rows = _rows(ws, SCAN_ROWS)
    idx, _ = find_header_row(rows, WB_HEADER_KEYWORDS)
    if idx is not None:
        return ROLE_WB
    return None


def _confirm_status(ws):
    rows = _rows(ws, 5)
    idx, _ = find_header_row(rows, STATUS_HEADER_KEYWORDS, max_scan=3, min_hits=2)
    if idx is not None:
        return ROLE_STATUS
    return None


def pick_person_day_source(roles):
    """TIDY beats WB_GRID as the person-day record source."""
    tidy = [s for s, r in roles.items() if r == ROLE_TIDY]
    wbs = [s for s, r in roles.items() if r == ROLE_WB]
    if tidy:
        return tidy[0], "TIDY (per-day rows with rates)"
    if wbs:
        return wbs[0], "WB_GRID (no tidy sheet found — rates may be missing)"
    return None, "no usable person-day sheet found"
