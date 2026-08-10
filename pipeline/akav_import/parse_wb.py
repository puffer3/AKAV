"""Parser for 'Workbook' / 'WB - LIVE' sheets.

Rows 1-5 hold show metadata as label/value pairs scattered across columns
(Show, Venue, Client, PO, PM, ...). A header row (~row 6) is followed by
per-person-per-day rows grouped by date with blank separator rows.

Headers here are unreliable (AnaheimPRG's Name header is '  '), so column
roles come from data-shape inference like the tidy parser; the header row
is only used to locate where data starts and to grab OT/DT columns.
"""

from .models import ShowMeta, WorkRecord
from .normalize import (
    clean_str, is_blank, norm_phone, slugify, to_hhmm, to_iso_date, to_number,
)
from .workbook import WB_HEADER_KEYWORDS, find_header_row, infer_columns

META_LABELS = {
    "show": "showLabel",
    "venue": "venue",
    "client": "client",
    "po": "po",
    "pm": "pm",
}

_SKIP_ROW_MARKERS = ("total", "admin fee", "grand total")


def extract_show_meta(ws, fallback_label=""):
    meta = ShowMeta()
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= 5:
            break
        rows.append(row)
    for row in rows:
        for c, v in enumerate(row):
            label = clean_str(v).lower()
            target = META_LABELS.get(label)
            if not target or getattr(meta, target):
                continue
            if c + 1 < len(row) and not is_blank(row[c + 1]):
                setattr(meta, target, clean_str(row[c + 1]))
    if not meta.showLabel:
        meta.showLabel = fallback_label
    meta.showId = slugify(meta.showLabel)
    return meta


def parse(ws):
    """Return (records, colmap). Same contract as parse_tidy.parse."""
    rows = list(ws.iter_rows(values_only=True))
    header_idx, headers = find_header_row(rows, WB_HEADER_KEYWORDS)
    if header_idx is None:
        raise ValueError("%s: no header row found" % ws.title)

    data = [r for r in rows[header_idx + 1:]
            if any(not is_blank(c) for c in r)]
    colmap = infer_columns(data)
    if "date" not in colmap or "name" not in colmap:
        raise ValueError(
            "%s: could not locate date/name columns (inferred: %s)"
            % (ws.title, colmap))

    # OT/DT columns from the header row (data inference can't see them —
    # they're sparse small numbers)
    ot_idx = dt_idx = None
    for i, h in enumerate(headers):
        if h == "ot":
            ot_idx = i
        elif h == "dt":
            dt_idx = i

    records = []
    last_date = ""
    for rownum, row in enumerate(rows, start=1):
        if rownum <= header_idx + 1:
            continue
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
        first = clean_str(row[0]).lower()
        if any(m in first for m in _SKIP_ROW_MARKERS):
            continue
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
        for label, idx in (("OT", ot_idx), ("DT", dt_idx)):
            if idx is not None and idx < len(row) and not is_blank(row[idx]):
                note_parts.append("%s:%s" % (label, clean_str(row[idx])))

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
