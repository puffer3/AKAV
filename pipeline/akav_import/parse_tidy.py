"""Parser for tidy per-person-per-day sheets ('Scratch Paper', 'Show details ').

These sheets have no header row — data starts at row 1, one row per
person per day: date, position, start, end, rate, area, name[, phone, email].
"""

from .models import WorkRecord
from .normalize import (
    clean_str, is_blank, norm_phone, to_hhmm, to_iso_date, to_number,
)
from .workbook import infer_columns


def parse(ws):
    """Return (records, colmap). Records lack personKey/recordHash —
    identity resolution fills those in later."""
    rows = [r for r in ws.iter_rows(values_only=True)]
    data = [r for r in rows if any(not is_blank(c) for c in r)]
    colmap = infer_columns(data)
    if "date" not in colmap or "name" not in colmap:
        raise ValueError(
            "%s: could not locate date/name columns (inferred: %s)"
            % (ws.title, colmap))

    records = []
    last_date = ""
    for rownum, row in enumerate(rows, start=1):
        if all(is_blank(c) for c in row):
            continue

        def cell(role):
            i = colmap.get(role)
            if i is None or i >= len(row):
                return None
            return row[i]

        name = clean_str(cell("name"))
        if not any(ch.isalpha() for ch in name):
            name = ""  # numeric junk in the name column ('1.0')
        d = to_iso_date(cell("date")) or last_date
        if not name or not d:
            continue
        last_date = d

        note_parts = []
        for i in colmap.get("notes", []):
            if i < len(row) and not is_blank(row[i]):
                v = clean_str(row[i])
                if v and len(v) <= 50:
                    note_parts.append(v)

        records.append(WorkRecord(
            date=d,
            position=clean_str(cell("position")),
            callStart=to_hhmm(cell("start")),
            callEnd=to_hhmm(cell("end")),
            rate=to_number(cell("rate")),
            area=clean_str(cell("area")),
            otNote="; ".join(note_parts),
            name=name,
            email=clean_str(cell("email")).lower(),
            phoneDigits=norm_phone(cell("phone")),
            sourceSheet=ws.title,
            sourceRow=rownum,
        ))
    return records, colmap
