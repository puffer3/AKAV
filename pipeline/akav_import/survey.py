"""Survey the 45 rolodex workbooks and draft the per-tab mapping spec.

The rollies are header-less and ragged: data starts at row 1, column order
changes per tab, and only 3 of 202 non-empty tabs have a real header row. So
a fixed parser cannot work. Instead this module PROFILES each column by what
its cells actually look like, drafts `rolly_spec.csv`, and reports a
confidence per tab.

The draft is reviewed by a human before any import runs. Low-confidence tabs
are the review queue; the spec file is the checked-in record of that judgment,
which is what makes a re-import reproducible when a corrected rolly arrives.

Tab kinds:
  people  - a crew list (what we want)
  show    - a job-planning sheet ('Show:/Venue:/Address:'), skipped this pass
  empty   - no data (28 stray 'SheetN' tabs)
"""

import csv
import os
import re

import openpyxl

from .names import split_aka
from .normalize import clean_str, looks_like_email, looks_like_phone
from .roles import RoleResolver

# The spec names client workbooks and tabs (some tabs are named after
# individuals), so it lives OUTSIDE this public repo with the source files.
from .config import source_path as _source_path
ROLLY_SPEC_CSV = _source_path("rolly_spec.csv")

SPEC_FIELDS = [
    "workbook", "tab", "kind", "parse_mode", "market", "confidence",
    "rows", "header_rows",
    "name_col", "phone_col", "email_col", "roles_col", "city_col",
    "grade_col", "notes_col", "referred_col", "status", "supersedes",
    "review_note",
]

# Tabs that are not AKAV crew at all. 'VW Execs' is a client's executives --
# the existing parse_rolly.py already established skipping it.
NON_CREW_TAB = re.compile(r"(?i)^(vw execs?|execs?|clients?|vendors?)$")

# 'B+', 'a', 'X', 'B-/C+'
GRADE_RE = re.compile(r"^[A-Fa-fXx][+-]?(\s*/\s*[A-Fa-fXx][+-]?)?$")

# A show/job sheet rather than a crew list.
SHOW_MARKERS = ("show:", "show", "venue:", "venue", "address:", "call time")

# Tab-name semantics AK & KB use. Order matters: 'no hire' before 'hire'.
STATUS_BY_TAB = [
    (re.compile(r"(?i)no\s*hire|questionable|do\s*not"), "No Hire"),
    (re.compile(r"(?i)short\s*list|shortlist|shorties"), "Short List"),
    (re.compile(r"(?i)\bactive\b"), "Active"),
    (re.compile(r"(?i)\blead(s)?\b|indeed|linkedin|linkdin"), "Lead"),
    (re.compile(r"(?i)\bnot\b"), "Wrong Region"),
]


def tab_status(tab_name):
    for pat, status in STATUS_BY_TAB:
        if pat.search(tab_name or ""):
            return status
    return ""


# Tab names that group by status/craft/nothing rather than by place. For these
# the market comes from the workbook instead.
NON_PLACE_TAB = re.compile(
    r"(?i)^(sheet\s*\d*|all|active|contacts?|short\s*list|shortlist|shorties|"
    r"leads?|indeed|linked ?in|linkdin|hands?|bos|riggers?|video|audio|"
    r"lighting|cam ops?|carps?|specials|copy of .*|new .*|old .*|no hire|"
    r"questionable|\d+|.*\d{3,}.*)$")


# Market shorthand -> canonical market name. Same reviewable pattern as
# role_map: the rollies name the same market a dozen ways.
MARKET_ALIASES = {
    "atl": "Atlanta", "la": "Los Angeles", "los angeles": "Los Angeles",
    "nola": "New Orleans", "new orleans": "New Orleans",
    "mnpls": "Minneapolis", "mpls": "Minneapolis",
    "nyc": "New York", "new york city": "New York",
    "sf": "San Francisco", "okc": "Oklahoma City",
    "abq": "Albuquerque", "abq santa fe": "Albuquerque / Santa Fe",
    "vegas": "Las Vegas", "philly": "Philadelphia",
    "dmv": "Washington DC", "dc": "Washington DC", "wdc": "Washington DC",
    "fl": "Florida", "az": "Arizona", "ok": "Oklahoma",
    "slc": "Salt Lake City", "salt lake": "Salt Lake City",
    "portland or": "Portland", "pdx": "Portland",
    "charleston sc": "Charleston, SC", "st louis": "St. Louis",
    "carolina ga": "Carolinas / Georgia", "new mexico": "New Mexico",
    "indy": "Indianapolis", "chattanooga": "Chattanooga",
}

# Words that survive tab-name cleaning but aren't part of a market name.
# NB: 'new' is NOT here -- New Orleans / New York / New Mexico all need it.
MARKET_NOISE = re.compile(
    r"(?i)\b(rolling|copy|of|old|list|ons?|sh|avt|bo|techs?|show|"
    r"crew|from|various|sourced|unconfirmed)\b")

# Short all-caps tokens that are genuine abbreviations, not shouting.
_ACRONYM = re.compile(r"^[A-Z]{2}$")


def _titlecase(text):
    """Title-case without destroying 'SC' / 'GA', and without preserving the
    shouting in tab names like 'NASHVILLE' (which would otherwise become a
    second market distinct from 'Nashville')."""
    out = []
    for w in text.split():
        core = w.strip(",.")
        out.append(w if _ACRONYM.match(core) else w.capitalize())
    return " ".join(out)


def canonical_market(raw):
    """'ATL' -> 'Atlanta'; 'Rolling LA' -> 'Los Angeles'."""
    t = re.sub(r"[^\w\s,/]", " ", str(raw or ""))
    t = re.sub(r"\s+", " ", t).strip(" ,-/")

    def lookup(s):
        return MARKET_ALIASES.get(re.sub(r"[^a-z0-9 ]", "", s.lower()).strip())

    # Alias first, so 'New Mexico' resolves before noise-stripping could eat
    # the 'New'.
    hit = lookup(t)
    if hit:
        return hit
    t2 = re.sub(r"\s+", " ", MARKET_NOISE.sub(" ", t)).strip(" ,-/")
    hit = lookup(t2)
    if hit:
        return hit
    return _titlecase(t2 or t)


# Sub-markets that appear as TABS inside a bigger workbook. An allowlist beats
# a blocklist here: tabs are also named for shows ('ADCES', 'AWS', 'Watts7',
# 'Massage Envy Vegas') and crafts ('Trux', 'Green Homies', 'Promptor Ops'),
# and those must inherit the workbook's market rather than invent one.
KNOWN_PLACES = {
    "miami", "tampa", "orlando", "jacksonville", "houston", "austin",
    "san antonio", "dallas", "fort worth", "savannah", "asheville",
    "charlotte", "raleigh", "durham", "columbia", "greenville",
    "vancouver", "toronto", "montreal", "calgary", "ottawa",
    "colorado springs", "boulder", "milwaukee", "madison", "green bay",
    "san diego", "sacramento", "long beach", "oakland", "san jose",
    "fresno", "anaheim", "riverside", "pasadena", "burbank",
    "seattle", "tacoma", "olympia", "spokane", "portland", "eugene",
    "detroit", "grand rapids", "ann arbor", "lansing",
    "cincinnati", "cleveland", "columbus", "dayton", "toledo", "akron",
    "birmingham", "montgomery", "mobile", "huntsville",
    "memphis", "knoxville", "chattanooga", "nashville",
    "baltimore", "richmond", "norfolk", "annapolis",
    "boston", "providence", "hartford", "new haven", "worcester",
    "buffalo", "rochester", "albany", "syracuse",
    "pittsburgh", "harrisburg", "allentown",
    "santa fe", "albuquerque", "tucson", "phoenix", "scottsdale", "mesa",
    "reno", "boise", "omaha", "des moines", "wichita", "tulsa",
    "louisville", "lexington", "indianapolis", "fort wayne",
    "st. louis", "kansas city", "minneapolis", "st. paul", "duluth",
    "new orleans", "baton rouge", "shreveport", "jackson",
    "atlanta", "augusta", "macon", "athens",
    "chicago", "naperville", "rockford", "springfield",
    "los angeles", "new york", "san francisco", "las vegas", "denver",
    "philadelphia", "washington dc", "salt lake city", "oklahoma city",
    "charleston, sc", "charleston", "portland or",
}


def _place_key(s):
    return re.sub(r"[^a-z0-9,. ]", "", str(s or "").lower()).strip()


def workbook_market(workbook):
    stem = re.sub(r"(?i)\s*roll?y.*|\s*roladex.*|\(\d+\)|\.xlsx$", "",
                  workbook or "").strip(" -_")
    return canonical_market(stem.replace("_", " "))


def market_for(workbook, tab_name):
    """Which market this tab represents.

    A tab name is used ONLY when it names a place we recognise -- 'Florida
    Rolly' has separate Miami, Tampa and Orlando tabs, and someone on the Miami
    tab works Miami, not 'Florida'. Tabs named for shows or crafts inherit the
    workbook's market instead of inventing one.

    This is a MARKET (where they work), never a residence. Appearing on the LA
    rolodex does not mean living in LA.
    """
    tab = (tab_name or "").strip()
    # Strip the bookkeeping words FIRST -- 'Copy of LA Active' is still an LA
    # tab, and rejecting it on the 'copy of' prefix loses that.
    # NB: 'new' is deliberately absent -- New Orleans / New York / New Haven
    # are all real markets, and stripping it sent every person on those tabs
    # to the workbook's market instead ('New Orleans' -> 'Florida').
    cleaned = re.sub(
        r"(?i)\b(copy|of|rolly|roladex|rolodex|active|inactive|all|crew|"
        r"short\s*list|shortlist|shorties|leads?|old|list)\b", " ", tab)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_,")
    if len(cleaned) >= 2:
        candidate = canonical_market(cleaned)
        if _place_key(candidate) in KNOWN_PLACES:
            return candidate
    return workbook_market(workbook)


def _cells(ws, max_rows=400):
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= max_rows:
            break
        rows.append(list(row))
    return rows


def _looks_like_name(v):
    s = clean_str(v)
    if not s or len(s) > 45 or "@" in s:
        return False
    core, _, _ = split_aka(s)
    words = [w for w in re.split(r"\s+", core) if w]
    if not 1 <= len(words) <= 4:
        return False
    alpha = re.sub(r"[^A-Za-z]", "", core)
    return len(alpha) >= 3 and len(alpha) / max(len(core), 1) > 0.6


def _looks_like_city(v, vocab):
    return clean_str(v).lower() in vocab


def profile_columns(rows, resolver, city_vocab):
    """Per column, the fraction of non-blank cells matching each role."""
    width = max((len(r) for r in rows), default=0)
    profiles = []
    for c in range(width):
        vals = [r[c] for r in rows if c < len(r) and not _blank(r[c])]
        n = len(vals)
        if not n:
            profiles.append({"filled": 0})
            continue
        role_hits = sum(1 for v in vals if resolver.resolve_cell(v))
        profiles.append({
            "filled": n,
            "fill_rate": n / len(rows),
            "email": sum(1 for v in vals if looks_like_email(v)) / n,
            "phone": sum(1 for v in vals if looks_like_phone(v)) / n,
            "grade": sum(1 for v in vals if GRADE_RE.match(clean_str(v))) / n,
            "role": role_hits / n,
            "name": sum(1 for v in vals if _looks_like_name(v)) / n,
            "city": sum(1 for v in vals if _looks_like_city(v, city_vocab)) / n,
            "long": sum(1 for v in vals if len(clean_str(v)) > 45) / n,
        })
    return profiles


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


# A handful of tabs DO have a header row ('grade | z | number | email | ...').
# Where one exists it beats any inference, and it must be skipped as data or
# it imports a person named 'z'.
HEADER_WORDS = {
    "name": "name_col", "contractor": "name_col", "crew": "name_col",
    "phone": "phone_col", "number": "phone_col", "cell": "phone_col",
    "email": "email_col", "e-mail": "email_col",
    "position": "roles_col", "position/s": "roles_col", "role": "roles_col",
    "roles": "roles_col", "best role": "roles_col", "skills": "roles_col",
    "city": "city_col", "location": "city_col", "market": "city_col",
    "grade": "grade_col",
    "notes": "notes_col", "note": "notes_col", "comments": "notes_col",
    "referred by": "referred_col",
}


def detect_header(rows):
    """Return (assigned, header_row_index) if row 0 is a header, else ({}, -1)."""
    if not rows:
        return {}, -1
    first = rows[0]
    assigned, hits = {}, 0
    for i, cell in enumerate(first):
        key = re.sub(r"\s+", " ", clean_str(cell).lower()).strip(": ")
        role = HEADER_WORDS.get(key)
        if role:
            hits += 1
            assigned.setdefault(role, i)
    # Two independent header words is enough; one could be coincidence
    # (a person legitimately named 'Grade' is not a thing, but 'Cam' is).
    if hits >= 2:
        return assigned, 0
    return {}, -1


# 'Pat Sample / 555-014-2718 / pat@example.com' or '3.    Sam (555) 014-9921'
# -- name and phone crammed into ONE cell. Three tabs do this.
SINGLE_CELL_RE = re.compile(
    r"^\s*(?:\d+[.)]\s*)?(?P<name>[^/|,\d]{2,40}?)\s*[/|,]?\s*"
    r"(?P<phone>\+?1?\s*\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4})")


def parse_mode_for(rows):
    """'columns' (normal) or 'single_cell' (name+phone in one cell)."""
    filled = [r for r in rows if any(not _blank(c) for c in r)]
    if not filled:
        return "columns"
    # Only consider it single-cell if the sheet is essentially one column AND
    # those cells routinely hold a name followed by a phone number.
    widths = [sum(1 for c in r if not _blank(c)) for r in filled]
    if sum(1 for w in widths if w > 1) > len(filled) * 0.3:
        return "columns"
    hits = 0
    for r in filled:
        first = next((clean_str(c) for c in r if not _blank(c)), "")
        if SINGLE_CELL_RE.match(first.replace("\xa0", " ")):
            hits += 1
    return "single_cell" if hits >= max(3, len(filled) * 0.3) else "columns"


def classify_tab(rows, tab_name=""):
    """people | show | empty | non_crew"""
    if NON_CREW_TAB.match((tab_name or "").strip()):
        return "non_crew"
    filled = [r for r in rows if any(not _blank(c) for c in r)]
    if not filled:
        return "empty"
    head = " ".join(
        clean_str(c).lower() for r in filled[:3] for c in r if not _blank(c))
    marker_hits = sum(1 for m in SHOW_MARKERS if m in head)
    if marker_hits >= 2 and ("venue" in head or "address" in head):
        return "show"
    return "people"


def parse_single_cell(rows):
    """Yield {name, phone} from a single-cell tab."""
    out = []
    for r in rows:
        first = next((clean_str(c) for c in r if not _blank(c)), "")
        m = SINGLE_CELL_RE.match(first.replace("\xa0", " "))
        if not m:
            continue
        name = re.sub(r"\s+", " ", m.group("name")).strip(" -_./")
        if name:
            out.append({"name": name, "phone": m.group("phone")})
    return out


def assign_columns(profiles):
    """Greedy one-column-per-role assignment, strongest signal first.

    Order matters: email and phone are near-unambiguous, so they claim their
    column before the fuzzier name/role/city tests get a chance to.

    Scores are weighted by SUPPORT (how many cells back the fraction up).
    Without that, a stray column holding one name scores 1.00 and beats the
    real name column's 0.97 over 153 cells -- which is exactly what it did.
    """
    taken = set()
    out = {}
    max_filled = max((p.get("filled", 0) for p in profiles), default=0) or 1

    def claim(role, key, threshold, sparse=False, min_cells=3):
        """sparse=True judges a column on PURITY alone.

        Grades, roles and cities are filled in for only some people -- a grade
        column that is 100% grade-shaped over 163 of 380 rows is still the
        grade column. Weighting those by density hid 13 real grade columns,
        including one with 163 grades in it. Identity columns (name/phone/
        email) stay density-weighted, where a near-empty column really is junk.
        """
        best, best_score = None, threshold
        for i, p in enumerate(profiles):
            if i in taken or not p.get("filled"):
                continue
            if sparse:
                if p["filled"] < min_cells:
                    continue
                score = p.get(key, 0) - 0.001 * i
            else:
                support = p["filled"] / max_filled
                # Mild leftward bias breaks ties: crew sheets read name-first.
                score = p.get(key, 0) * support - 0.001 * i
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            taken.add(best)
            out[role] = best
        return best

    claim("email_col", "email", 0.45)
    claim("phone_col", "phone", 0.45)
    claim("grade_col", "grade", 0.80, sparse=True, min_cells=5)
    claim("name_col", "name", 0.40)
    claim("roles_col", "role", 0.50, sparse=True, min_cells=4)
    claim("city_col", "city", 0.50, sparse=True, min_cells=4)
    # Notes = the widest remaining free-text column.
    claim("notes_col", "long", 0.10)
    return out


def confidence(assigned, profiles):
    """How much we trust this tab's mapping. name+phone|email is the floor."""
    has_name = "name_col" in assigned
    has_contact = "phone_col" in assigned or "email_col" in assigned
    if has_name and has_contact:
        return "high"
    if has_name or has_contact:
        return "medium"
    return "low"


def build_city_vocab(workbook_names, extra=()):
    """Cities we recognise, derived from the workbook names themselves plus
    whatever the caller adds. Data-driven so a new region needs no code."""
    vocab = set(x.lower() for x in extra)
    for name in workbook_names:
        stem = re.sub(r"(?i)\s*rolly.*|\s*roladex.*|\(\d+\)|\.xlsx$", "", name)
        for part in re.split(r"[_/&]| and ", stem):
            part = part.strip().lower()
            if len(part) > 2:
                vocab.add(part)
    return vocab


def survey(rollies_dir, resolver=None):
    """Profile every tab. Returns (spec_rows, stats)."""
    resolver = resolver or RoleResolver()
    files = sorted(f for f in os.listdir(rollies_dir)
                   if f.lower().endswith(".xlsx") and not f.startswith("~$"))
    city_vocab = build_city_vocab(files)

    spec_rows = []
    stats = {"workbooks": len(files), "people": 0, "show": 0, "empty": 0}

    for fn in files:
        wb = openpyxl.load_workbook(os.path.join(rollies_dir, fn),
                                    read_only=True, data_only=True)
        for ws in wb.worksheets:
            rows = _cells(ws)
            kind = classify_tab(rows, ws.title)
            stats[kind] = stats.get(kind, 0) + 1
            row = {f: "" for f in SPEC_FIELDS}
            row.update({
                "workbook": fn, "tab": ws.title, "kind": kind,
                "market": market_for(fn, ws.title),
                "rows": sum(1 for r in rows
                            if any(not _blank(c) for c in r)),
                "status": tab_status(ws.title),
            })
            if kind == "people":
                row["parse_mode"] = parse_mode_for(rows)
            if kind == "people" and row["parse_mode"] == "single_cell":
                found = parse_single_cell(rows)
                row["confidence"] = "high" if found else "low"
                row["review_note"] = "name+phone in one cell (%d found)" % len(found)
            elif kind == "people":
                header, hdr_idx = detect_header(rows)
                body = rows[hdr_idx + 1:] if hdr_idx >= 0 else rows
                profiles = profile_columns(body, resolver, city_vocab)
                assigned = assign_columns(profiles)
                if header:
                    # An explicit header always beats inference.
                    assigned.update(header)
                    row["review_note"] = "header row used"
                for role, idx in assigned.items():
                    if role in SPEC_FIELDS:
                        row[role] = idx
                row["header_rows"] = hdr_idx + 1
                row["confidence"] = ("high" if header
                                     else confidence(assigned, profiles))
                if row["confidence"] != "high":
                    row["review_note"] = "no %s detected" % (
                        "name column" if "name_col" not in assigned
                        else "phone/email column")
            spec_rows.append(row)
        wb.close()
    return spec_rows, stats


def write_spec(spec_rows, path=ROLLY_SPEC_CSV):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SPEC_FIELDS)
        w.writeheader()
        w.writerows(spec_rows)
    return len(spec_rows)


def load_spec(path=ROLLY_SPEC_CSV):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
