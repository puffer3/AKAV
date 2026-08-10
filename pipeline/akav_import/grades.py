"""Letter-grade detection and manual side-input merge.

Grades live in UNLABELED columns near the name column of Crew Status /
scratch tabs — values like 'b-', 'B+', 'c', 'd', 'x' ('x' = do-not-rehire).
A column qualifies only if EVERY non-empty value is grade-shaped, there are
at least 3 of them, and it sits within 2 columns of the name column. The
preview surfaces every detected column with samples so a human confirms it.

Manual side input (--grades grades.csv, columns: name_or_email,grade)
covers grades that live in the client's phone; it wins on conflict.
"""

import csv
import re

from .normalize import clean_str, is_blank, looks_like_email, norm_email, norm_name

GRADE_RE = re.compile(r"^[A-DFXa-dfx][+-]?$")


def normalize_grade(s):
    s = clean_str(s)
    if not s:
        return ""
    return s[0].upper() + s[1:]


def detect(ws, name_col, header_idx, data_start):
    """Scan one sheet for grade columns.

    Returns (grades, detections):
      grades     = {norm_name: grade}
      detections = [{sheet, column, samples, count}] for preview confirmation
    """
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {}, []
    headers = rows[header_idx] if header_idx is not None else ()
    ncols = max(len(r) for r in rows)

    grades, detections = {}, []
    lo, hi = max(0, name_col - 2), min(ncols - 1, name_col + 2)
    for c in range(lo, hi + 1):
        if c == name_col:
            continue
        header = clean_str(headers[c]) if c < len(headers) else ""
        if header:
            continue
        vals = []
        for r in rows[data_start - 1:]:
            v = r[c] if c < len(r) else None
            if is_blank(v) or isinstance(v, bool):
                continue
            if not isinstance(v, str):
                vals = []          # numbers/dates → not a grade column
                break
            vals.append(v.strip())
        if len(vals) < 3 or not all(GRADE_RE.match(v) for v in vals):
            continue

        col_grades = {}
        for r in rows[data_start - 1:]:
            g = r[c] if c < len(r) else None
            nm = r[name_col] if name_col < len(r) else None
            if is_blank(g) or is_blank(nm) or not isinstance(g, str):
                continue
            if GRADE_RE.match(g.strip()):
                col_grades[norm_name(nm)] = normalize_grade(g)
        if col_grades:
            grades.update(col_grades)
            detections.append({
                "sheet": ws.title,
                "column": _col_letter(c + 1),
                "samples": vals[:8],
                "count": len(col_grades),
            })
    return grades, detections


def load_manual(path):
    """grades.csv: name_or_email,grade (header row optional).
    Returns ({norm_name: grade}, {norm_email: grade}, unparsed_rows)."""
    by_name, by_email, bad = {}, {}, []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < 2:
                bad.append(row)
                continue
            who, grade = row[0].strip(), row[1].strip()
            if who.lower() in ("name", "email", "name_or_email"):
                continue          # header row
            if not GRADE_RE.match(grade):
                bad.append(row)
                continue
            if looks_like_email(who):
                by_email[norm_email(who)] = normalize_grade(grade)
            else:
                by_name[norm_name(who)] = normalize_grade(grade)
    return by_name, by_email, bad


def _col_letter(n):
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s
