"""Parser for 'Crew Status' sheets.

One row per contractor: name, phone, email, contract sent, amounts
($ due / amount paid / total), pay dates, and free-text notes. Vegas-style
sheets carry an UNLABELED letter-grade column — detected separately by
grades.py using the colmap this parser returns.
"""

import re
from datetime import date, datetime

from .models import StatusRow
from .normalize import (
    clean_str, is_blank, looks_like_email, looks_like_phone,
    norm_phone, to_number,
)
from .workbook import STATUS_HEADER_KEYWORDS, find_header_row

_NOISE_NOTES = re.compile(r"^(true|false|paid|x+|-+)$", re.I)
from .grades import GRADE_RE


def parse(ws):
    """Return (status_rows, info) where info = {header_idx, name_col,
    header_cells, data_start} for grade detection."""
    rows = list(ws.iter_rows(values_only=True))
    header_idx, headers = find_header_row(
        rows, STATUS_HEADER_KEYWORDS, max_scan=3, min_hits=2)
    if header_idx is None:
        raise ValueError("%s: no Crew Status header row" % ws.title)

    name_col = email_col = phone_col = None
    amount_cols, other_cols = [], []
    for i, h in enumerate(headers):
        if h in ("contractor", "name") and name_col is None:
            name_col = i
        elif h == "email":
            email_col = i
        elif h == "number":
            phone_col = i
        elif ("$" in h or "amount" in h or h == "total"
              or (("due" in h) and ("date" not in h))):
            amount_cols.append(i)
        else:
            other_cols.append(i)

    if name_col is None:
        raise ValueError("%s: no contractor/name column" % ws.title)

    out = []
    for rownum, row in enumerate(rows, start=1):
        if rownum <= header_idx + 1:
            continue
        if all(is_blank(c) for c in row):
            continue

        def val(i):
            return row[i] if (i is not None and i < len(row)) else None

        name = clean_str(val(name_col))
        if not name or looks_like_email(name):
            continue

        amount = None
        for i in amount_cols:
            n = to_number(val(i))
            if n is not None:
                amount = (amount or 0.0) + n

        # Free-text notes: any leftover string cell that isn't identity,
        # amounts, dates, booleans, status words, or a bare letter grade.
        notes = []
        for i, v in enumerate(row):
            if i in (name_col, email_col, phone_col) or i in amount_cols:
                continue
            if is_blank(v) or isinstance(v, bool):
                continue
            if isinstance(v, (int, float, datetime, date)):
                continue
            s = clean_str(v)
            if (len(s) <= 3 or _NOISE_NOTES.match(s) or GRADE_RE.match(s)
                    or looks_like_email(s) or looks_like_phone(s)):
                continue
            if s.lower() == name.lower():          # contract col repeats name
                continue
            if name.lower() in s.lower() and len(s) <= len(name) + 15:
                continue                            # 'Jane Doe - IC CONTRACT'
            notes.append(s)

        out.append(StatusRow(
            name=name,
            email=clean_str(val(email_col)).lower(),
            phoneDigits=norm_phone(val(phone_col)),
            amount=amount,
            notes="; ".join(notes),
            sourceSheet=ws.title,
            sourceRow=rownum,
        ))

    info = {
        "header_idx": header_idx,
        "name_col": name_col,
        "headers": headers,
        "data_start": header_idx + 2,
    }
    return out, info
