"""Parser for the crew rolodex ('LA _ San Diego Rolly.xlsx').

Header-less ragged sheets of contacts: name, phone, email, skills, and
sometimes a city or a letter grade, in varying column orders. City rules
(Henry's): explicit city cell wins; otherwise the sheet's region decides —
San Diego sheets default to 'San Diego', LA sheets to 'LA'. Sheets that
aren't crew (VW Execs) are skipped.

Grades/skills/notes ARE captured into the local JSON for future use, but
only name/phone/email/city are uploaded by the rolly command.
"""

import re

from .normalize import (
    clean_str, is_blank, looks_like_email, looks_like_phone,
    norm_email, norm_name, norm_phone,
)

# 'B+', 'a', 'B-/C+' …
GRADE_COMBO = re.compile(r"^[A-Fa-fXx][+-]?(\s*/\s*[A-Fa-fXx][+-]?)?$")

CITY_VOCAB = {
    "san diego", "la", "los angeles", "long beach", "riverside",
    "sacramento", "oceanside", "carlsbad", "escondido", "chula vista",
    "vista", "el cajon", "la mesa", "santee", "poway", "temecula",
    "murrieta", "irvine", "anaheim", "fullerton", "santa ana",
    "orange county", "hollywood", "north hollywood", "burbank",
    "glendale", "pasadena", "van nuys", "culver city", "torrance",
    "inglewood", "santa monica", "ventura", "oxnard", "camarillo",
    "thousand oaks", "simi valley", "palmdale", "lancaster",
    "bakersfield", "palm springs", "las vegas", "henderson", "phoenix",
}


def canonical_city(s):
    t = clean_str(s)
    if t.lower() in ("la", "los angeles"):
        return "LA"
    return " ".join(w.capitalize() for w in t.split())


def sheet_default_city(sheet_name):
    """Region default per sheet, or None to skip the sheet entirely."""
    n = " %s " % sheet_name.strip().lower()
    if "san diego" in n:
        return "San Diego"
    if "sacramento" in n:
        return "Sacramento"
    if " la " in n or n.strip().startswith("la "):
        return "LA"
    return None


def parse(wb, fallback_city=None, skip_sheets=None):
    """Return (contacts, info). Contacts are merged across sheets by
    email -> phone -> name. Each: {name, email, phoneDigits, city,
    cityExplicit, grade, notes, noteText, sheets}.

    fallback_city: used for sheets whose name carries no region (for
    single-city rolly files where tabs aren't labeled); without it such
    sheets are skipped. skip_sheets: exact sheet names to ignore."""
    info = []
    clusters = []            # list of dicts
    by_email, by_phone, by_name = {}, {}, {}

    def merge_into(c, target):
        if not target["email"] and c["email"]:
            target["email"] = c["email"]
        elif c["email"] and c["email"] != target["email"]:
            c["notes"].append("alt email: %s" % c["email"])
        if not target["phoneDigits"] and c["phoneDigits"]:
            target["phoneDigits"] = c["phoneDigits"]
        elif c["phoneDigits"] and c["phoneDigits"] != target["phoneDigits"]:
            c["notes"].append("alt phone: %s" % c["phoneDigits"])
        if len(c["name"]) > len(target["name"]):
            target["name"] = c["name"]
        # explicit city beats sheet default; first explicit wins
        if c["cityExplicit"] and not target["cityExplicit"]:
            target["city"], target["cityExplicit"] = c["city"], True
        elif not target["city"]:
            target["city"] = c["city"]
        if c["grade"] and not target["grade"]:
            target["grade"] = c["grade"]
        for note in c["notes"]:
            if note not in target["notes"]:
                target["notes"].append(note)
        target["sheets"] |= c["sheets"]

    def add_contact(c):
        keys = []
        if c["email"]:
            keys.append(("e", norm_email(c["email"]), by_email))
        if c["phoneDigits"]:
            keys.append(("p", c["phoneDigits"], by_phone))
        if c["name"]:
            keys.append(("n", norm_name(c["name"]), by_name))
        target = None
        for _, k, m in keys:
            if k in m:
                target = m[k]
                break
        if target is None:
            clusters.append(c)
            target = c
        else:
            merge_into(c, target)
        for _, k, m in keys:
            m.setdefault(k, target)

    skip = {s.strip().lower() for s in (skip_sheets or [])}
    for ws in wb.worksheets:
        if ws.title.strip().lower() in skip:
            info.append("skipped sheet %r (--skip-sheet)" % ws.title)
            continue
        default_city = sheet_default_city(ws.title) or fallback_city
        if default_city is None:
            info.append("skipped sheet %r (no region in name; use "
                        "--default-city to include)" % ws.title)
            continue
        rows = 0
        for rownum, cells_row in enumerate(ws.iter_rows(), start=1):
            row = [c.value for c in cells_row]
            if all(is_blank(c) for c in row):
                continue
            # Font-color markers: the client color-codes rows (bright red =
            # 'union only' in San Diego; dark red = suspected do-not-hire).
            # Semantics unconfirmed (CLIENT_QUESTIONS) — flag, don't drop.
            n_red = n_dark = 0
            for c in cells_row:
                if c.value is None or c.font is None or c.font.color is None:
                    continue
                rgb = c.font.color.rgb
                if not isinstance(rgb, str):
                    continue
                if rgb == "FFFF0000":
                    n_red += 1
                elif rgb in ("FF980000", "FFA61C00", "FF660000",
                             "FFCC0000", "FFE06666"):
                    n_dark += 1
            color_flag = ""
            if n_dark:
                color_flag = "marked DARK RED in rolly (possible DNH?)"
            elif n_red:
                color_flag = "marked RED in rolly"
            name = email = ""
            phone = grade = city = ""
            notes = []
            for v in row:
                if is_blank(v) or isinstance(v, bool):
                    continue
                s = clean_str(v)
                if not s:
                    continue
                if looks_like_email(s):
                    if not email:
                        email = s.lower()
                    elif s.lower() != email:
                        notes.append("alt email: %s" % s.lower())
                elif looks_like_phone(s):
                    if not phone:
                        phone = norm_phone(s)
                    elif norm_phone(s) != phone:
                        notes.append("alt phone: %s" % s)
                elif GRADE_COMBO.match(s) and not name and not grade:
                    grade = s          # grade column precedes the name
                elif not name and sum(1 for ch in s if ch.isalpha()) >= 2:
                    name = s
                elif s.lower() in CITY_VOCAB and not city:
                    city = canonical_city(s)
                elif len(s) > 1:
                    notes.append(s)
            if not name:
                continue
            if color_flag:
                notes.append(color_flag)
            rows += 1
            add_contact({
                "name": name,
                "email": email,
                "phoneDigits": phone,
                "city": city or default_city,
                "cityExplicit": bool(city),
                "grade": grade,
                "notes": notes,
                "sheets": {ws.title},
            })
        info.append("sheet %r: %d contact rows (default city %s)"
                    % (ws.title, rows, default_city))

    for c in clusters:
        c["sheets"] = sorted(c["sheets"])
        # Skills/comments ride to the Notes column; the grade is its own
        # structured field (goes to the master's general Grade column).
        c["grade"] = c["grade"].upper()
        c["noteText"] = "; ".join(c["notes"])
    clusters.sort(key=lambda c: norm_name(c["name"]))
    return clusters, info
