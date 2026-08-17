"""Parse signed Statements of Work (.docx) for job titles + DAY RATES.

Each SOW has a schedule table, one row per worked day:

    Date | Role | Rate | In Time | Out Time | Dress Code          (BaltAVFX1)
    Date | Role | In Time | Out Time | Rate | Dress Code | Area   (ATLOnS21)

The column ORDER differs between templates, so everything is looked up by
HEADER NAME -- never by index. Rate appears as both '$550' and '500'.

AK & KB's rules applied here:
  * day rate is the number that matters (OT/DT/meal penalty are boilerplate
    in the prose and deliberately ignored)
  * half days are ignored -- '- Half' rows never contribute to a rate

The person is identified by FILENAME ('Pat Sample - IC - Statement of
Work.docx' -> 'Pat Sample'); templates are skipped.
"""

import os
import re
import statistics
import zipfile
from collections import defaultdict
from xml.etree import ElementTree as ET

from .names import split_aka, split_segments
from .normalize import norm_name
from .roles import RoleResolver, split_qualifiers

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Files that are blank templates, not a person's signed contract.
TEMPLATE_RE = re.compile(r"(?i)template|^copy of |email template")

# Filename segments that are boilerplate, not part of anyone's name. Real
# filenames look like 'Yoshi - Jamie Sample - AKAV - Statement of Work
# (W2).docx', so this is a per-segment filter, not a suffix strip.
BOILERPLATE_SEG_RE = re.compile(
    r"(?i)^(AKAV|IC|W2|Statement of Work.*|SOW.*|Corrected|Final|Signed)$")

# A plausible AV day rate. Outside this band we flag rather than trust.
RATE_MIN, RATE_MAX = 100.0, 2000.0


def person_from_filename(path):
    """Return (display_name, aliases).

    Handles the three shapes actually present in the contracts:
      'Pat Sample - IC - Statement of Work'       -> ('Pat Sample', [])
      'Yoshi - Jamie Sample - AKAV - ...'         -> ('Jamie Sample', ['Yoshi'])
      'Devendra (Robin) Sample - AKAV - ...'      -> ('Devendra Sample', ['Robin Sample'])

    Aliases matter: 'Devendra (Robin) Sample' and 'Robin Sample' are two
    separate contract files for one human, and only the alias links them.
    """
    base = os.path.splitext(os.path.basename(path))[0]
    primary, aliases = split_segments(base, drop_re=BOILERPLATE_SEG_RE)
    # 'Devendra (Robin) Sample' -> 'Devendra Sample' + aka 'Robin Sample',
    # which is what links this file to the separate 'Robin Sample' contract.
    primary, akas, _ = split_aka(primary)
    return primary, aliases + akas


def _tables(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    for tbl in root.iter(W + "tbl"):
        rows = []
        for tr in tbl.findall(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                cells.append("".join(
                    t.text or "" for t in tc.iter(W + "t")).strip())
            rows.append(cells)
        yield rows


def _to_rate(text):
    s = re.sub(r"[^\d.]", "", str(text or ""))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _header_index(header_row):
    """Case/space-insensitive header -> column index."""
    out = {}
    for i, name in enumerate(header_row):
        key = re.sub(r"\s+", " ", str(name or "").strip().lower())
        if key:
            out[key] = i
    return out


def parse_file(path, resolver):
    """Return (person_name, [day dicts], [problem strings])."""
    person, aliases = person_from_filename(path)
    days, problems = [], []
    show = os.path.basename(os.path.dirname(path))

    for rows in _tables(path):
        if not rows:
            continue
        idx = _header_index(rows[0])
        if "role" not in idx:
            continue                      # signature block, tips table, etc.
        ri, rate_i = idx["role"], idx.get("rate")
        date_i = idx.get("date")

        for row in rows[1:]:
            if ri >= len(row):
                continue
            role_raw = row[ri].strip()
            if not role_raw:
                continue                  # padding rows in a blank template
            base, quals = split_qualifiers(role_raw)
            titles = resolver.resolve_cell(base)
            rate = _to_rate(row[rate_i]) if rate_i is not None and rate_i < len(row) else None
            date = row[date_i].strip() if date_i is not None and date_i < len(row) else ""

            if rate is not None and not (RATE_MIN <= rate <= RATE_MAX):
                problems.append(
                    "rate %s outside $%d-$%d for %r (%s)"
                    % (rate, RATE_MIN, RATE_MAX, role_raw, os.path.basename(path)))
                rate = None

            days.append({
                "person": person,
                "aliases": aliases,
                "show": show,
                "date": date,
                "role_raw": role_raw,
                "titles": titles,
                "is_half": "half" in quals,
                "rate": rate,
                "source": os.path.relpath(path),
            })
    return person, days, problems


def parse_folder(root, resolver=None):
    """Walk a contracts folder. Returns (days, skipped, problems).

    `root` is a parameter, not a constant: a second contracts folder is
    expected from the client and must import with no code change.
    """
    resolver = resolver or RoleResolver()
    days, skipped, problems = [], [], []

    for dirpath, _, filenames in os.walk(root):
        for fn in sorted(filenames):
            if not fn.lower().endswith(".docx") or fn.startswith("~$"):
                continue
            path = os.path.join(dirpath, fn)
            if TEMPLATE_RE.search(fn):
                skipped.append(path)
                continue
            try:
                _, d, p = parse_file(path, resolver)
            except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
                problems.append("unreadable %s: %s" % (fn, exc))
                continue
            days.extend(d)
            problems.extend(p)
    return days, skipped, problems


def summarize_rates(days):
    """Per (person, canonical title): the day-rate picture.

    Half days are excluded from every rate statistic per AK & KB, but still
    counted so we know the person has done the role.
    """
    buckets = defaultdict(lambda: {"rates": [], "half_days": 0, "days": 0,
                                   "shows": set(), "dates": []})
    for d in days:
        for title in d["titles"] or ["(unmapped)"]:
            b = buckets[(norm_name(d["person"]), title)]
            b["days"] += 1
            b["shows"].add(d["show"])
            if d["date"]:
                b["dates"].append(d["date"])
            if d["is_half"]:
                b["half_days"] += 1
            elif d["rate"] is not None:
                b["rates"].append(d["rate"])

    out = []
    for (person_key, title), b in sorted(buckets.items()):
        rates = b["rates"]
        out.append({
            "person_key": person_key,
            "title": title,
            "typical_day_rate": statistics.median(rates) if rates else None,
            "rate_low": min(rates) if rates else None,
            "rate_high": max(rates) if rates else None,
            "rated_days": len(rates),
            "half_days": b["half_days"],
            "total_days": b["days"],
            "shows": sorted(b["shows"]),
        })
    return out
