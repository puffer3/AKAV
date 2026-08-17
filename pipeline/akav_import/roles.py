"""Canonical job titles + the alias map that collapses the rolly shorthand.

AK & KB's rule: the rolodex shows a FULL job title, never an abbreviation.
The rollies are almost entirely abbreviations ('avt' x948, 'sh' x107, 'gav'
x1268) and the contracts are almost entirely full titles ('Utility Tech',
'Lighting Assist', 'Video Utility') -- so the contracts seed the canonical
list and this module maps the shorthand onto it.

The map lives in `role_map.csv` next to this file so it is reviewable and
editable without touching code. `CANONICAL` below is the seed used to write
that file the first time; after that the CSV wins.

Anything unmapped is NEVER guessed -- it goes to the UNMAPPED report and
becomes a `role_unmapped` row on the Conflicts tab.
"""

import csv
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
ROLE_MAP_CSV = os.path.join(_HERE, "role_map.csv")

# Qualifiers AK & KB append to a role rather than making a new role.
# ' - Half' is dropped from rate stats entirely (their instruction) but we still
# record that the person has done the role.
QUALIFIERS = {
    "half": re.compile(r"(?i)\s*[-(]?\s*half(\s*day)?\s*\)?\s*$"),
    "float": re.compile(r"(?i)\s*[-(]?\s*float(er)?\s*\)?\s*$"),
    "breakout": re.compile(r"(?i)[\s(-]*breakout\s*\)?\s*$"),
    "lead": re.compile(r"(?i)\s*-\s*lead\s*$"),
    "set": re.compile(r"(?i)\s*-\s*set(\s*up)?\s*$"),
    "strike": re.compile(r"(?i)\s*-\s*strike\s*$"),
    # Job workbooks number the slots: 'Stagehand - 1', 'Monitor Tech 2',
    # 'Video Utility - 3'. The number is a headcount position, not a job.
    #
    # A SEPARATOR is required before the digit. Without it this eats the job
    # codes themselves -- 'A1' becomes 'A', 'V1' becomes 'V' -- which silently
    # unmapped every A1/A2/V1/L2 in the data.
    "slot": re.compile(r"(?i)(?:(?<=[A-Za-z0-9])\s+[-#]?\s*\d+|\s*[-#]\s*\d+)\s*$"),
    "zone": re.compile(r"(?i)\s*[-–]?\s*zone\s*\d*\s*$"),
}

# A trailing '(GS)', '(General Session)', '(in house)' is room/context, not a
# distinct job. Captured so it is never silently lost, then dropped before
# alias lookup -- otherwise 'Stagehand (GS)' misses the map as 'stagehand (gs'.
PAREN_RE = re.compile(r"\(([^)]*)\)")

# canonical title -> aliases seen in the source material.
# The parenthetical on A1/A2/V1/V2/L1/L2 is deliberate: those ARE the job
# titles crew use, so we keep the code and spell out what it means.
CANONICAL = {
    "General AV": ["gav", "general av", "g av", "gen av"],
    "AV Tech": ["avt", "av tech", "avtech", "a/v tech", "av technician"],
    "Stagehand": ["sh", "stagehand", "stage hand", "hand", "hands"],
    "Utility Tech": ["utility", "utility tech", "utillity", "util", "ut"],
    "Video Utility": ["video utility", "video util", "v util"],
    "Audio Utility": ["audio utility", "audio util"],
    "Audio Engineer (A1)": ["a1", "a-1", "audio 1", "audio lead"],
    "Audio Assistant (A2)": ["a2", "a-2", "audio 2"],
    "Audio Assist": ["audio assist", "audio assistant"],
    "Video Engineer (V1)": ["v1", "v-1", "video 1", "video lead"],
    "Video Assistant (V2)": ["v2", "v-2", "video 2"],
    "Video Assist": ["video assist"],
    "Lighting Engineer (L1)": ["l1", "l-1", "lighting 1"],
    "Lighting Assistant (L2)": ["l2", "l-2", "lighting 2"],
    "Lighting Assist": ["lighting assist", "lx assist", "light assist"],
    "Lighting Director": ["ld", "lighting director", "lx", "lighting"],
    "Camera Operator": ["cam op", "cam", "camop", "camera op", "camera",
                        "cam ops", "cam op i", "cam operator"],
    "Breakout Operator": ["bo op", "bo", "b/o", "breakout", "breakouts",
                          "bo tech", "breakout op", "bo ops", "bo techs"],
    "Breakout Lead": ["bo lead", "breakout lead"],
    "Technical Director": ["td", "technical director", "tech director"],
    "Master Electrician": ["me", "master electrician"],
    "Electrician": ["electrician", "electrics"],
    "Carpenter": ["carp", "carpentry", "carpenter", "carps"],
    "Rigger": ["rigger", "rigging", "riggers"],
    "Up Rigger": ["up rigger", "uprigger", "high rigger"],
    "Down Rigger": ["down rigger", "downrigger", "ground rigger"],
    "Forklift Operator": ["fork", "forklift", "forklift op", "fork op"],
    "Floater": ["floater", "float"],
    # Playback/records is its own position in the source data ('Plackback /
    # Records'), not a flavour of graphics -- kept separate, matching the
    # deployed UI which also lists it on its own.
    "Graphics Operator": ["gfx", "graphics", "graphics op", "graphics operator"],
    "Playback Operator": ["playback", "pbo", "plackback"],
    "Projectionist": ["pj", "projection", "projectionist", "projector",
                      "video projectionist"],
    "LED Tech": ["led", "led tech", "led wall"],
    "Loader": ["loader", "loaders", "load in", "load out"],
    "Truck Loader": ["trucks", "truck", "truck loader", "trux", "trx"],
    "Crew Lead": ["crew lead", "lead", "leads", "crew chief"],
    "Multisource": ["multisource", "multi source", "multi-source"],
    "Stream Tech": ["stream tech", "streaming", "stream"],
    "Strike": ["strike"],
    "Audio Technician": ["audio", "audio tech", "sound"],
    "Video Technician": ["video", "video tech", "video op",
                         "video operator"],
    "Assistant": ["assist", "assistant", "asst"],
    # Absorbed from pipeline/out/job-types-candidates.md -- the position names
    # compiled from the show workbooks for CLIENT_QUESTIONS #6.
    "Carpenter Assist": ["carp assist", "carpenter assist", "carp asst"],
    "Boom Operator": ["boom op", "boom operator", "boom"],
    "LED Assist": ["led assist", "led asst"],
    "Audio Assist (A3)": ["a3", "audio assist a3"],
    "Lighting Assist (L3)": ["l3", "lighting assist l3"],
    "Loader / Pusher": ["loader pusher", "pusher", "loader / pusher"],
    # Seen in the embedded job workbooks.
    "Monitor Tech": ["monitor tech", "monitor technician", "monitors"],
    "Steward": ["steward", "production steward", "production - steward",
                "house steward"],
    "Video Breakout Tech": ["video bo", "video bos", "video breakout",
                            "v breakout tech", "video breakout tech"],
    "Audio Breakout Tech": ["audio bo", "audio bos", "audio breakout"],
    # 'Records' is its own specific job, not a flavour of playback. The source
    # string 'Plackback / Records' is two positions, not one.
    "Record Technician": ["records", "record tech", "record technician",
                          "recording", "record op", "recordings"],
    # Further position names found in the embedded job workbooks.
    "Breakout Room Tech": ["breakout room tech", "breakout room technician",
                           "br tech"],
    "Setup Tech": ["setup tech", "set up tech", "set tech", "setup technician"],
    # Area leads are differentiated BY AREA. Only the video one is actually
    # used in the data; audio/lighting variants were dropped as unobserved.
    # If they appear later they surface in the UNMAPPED report rather than
    # being folded into a generic bucket.
    "Video Area Lead": ["video area lead", "area lead video", "v area lead"],
    "LED Lead": ["led lead"],
    "BO Record Tech": ["bo record tech", "breakout record tech"],
    "General Labor": ["general labor", "labor", "labour", "general labour"],
}


def split_claimed_vs_worked(claimed_cells, worked_titles, resolver=None):
    """Separate what someone HAS DONE from what they SAY they can do.

    Returns (worked, claimed_only) as canonical titles.

    Two different facts that must not be mixed. `worked` comes from contracts
    and job sheets -- positions AKAV actually booked and can stand behind.
    `claimed_only` comes from rolodex skill lists and has no such backing:
    'Teleprompt' appears 7 times in skill lists and zero times in any contract
    or job sheet.

    Kept as a separate FIELD rather than a note, because the question it
    answers ("who might be able to run a teleprompter?") is a search, and free
    text buried in a notes column can't be searched reliably.

    A claimed skill is promoted out of `claimed_only` the moment the person
    works it -- comparison is on canonical titles, so claiming 'V1' and working
    'Video Engineer (V1)' counts as proved.
    """
    resolver = resolver or RoleResolver()
    worked = []
    for w in worked_titles or []:
        for t in (resolver.resolve_cell(w) or []):
            if t not in worked:
                worked.append(t)
    done = set(worked)

    # Resolve TOKEN BY TOKEN, not cell by cell. A skill list like
    # 'V1, PJ, Teleprompt, VSWCH' mostly maps, and resolving the whole cell
    # would silently swallow the two tokens that don't -- which are exactly
    # the niche skills someone would search for.
    claimed_only = []
    for cell in claimed_cells or []:
        for part in re.split(r"[/,;|]|\band\b|\+", str(cell or "")):
            token = re.sub(r"\s+", " ", part).strip(" -,/")
            if not token or len(token) > 40:
                continue
            titles = resolver.resolve_cell(token)
            if titles:
                for t in titles:
                    if t not in done and t not in claimed_only:
                        claimed_only.append(t)
            elif token not in claimed_only and token not in done:
                # Outside AKAV's vocabulary ('Teleprompt', 'VSWCH', 'Millumin')
                # -- kept as the person's own words, not discarded.
                claimed_only.append(token)
    return worked, claimed_only


def duplicate_aliases():
    """Aliases claimed by more than one canonical title.

    An alias can only map one way, so a collision means one title silently
    loses. Checked in the tests below rather than discovered in the output.
    """
    seen, dupes = {}, {}
    for title, aliases in CANONICAL.items():
        for a in aliases:
            k = norm_token(a)
            if k in seen and seen[k] != title:
                dupes.setdefault(k, {seen[k]}).add(title)
            seen[k] = title
    return {k: sorted(v) for k, v in dupes.items()}


def split_qualifiers(raw):
    """'AV Tech - Half (Breakout)' -> ('AV Tech', {'half', 'breakout'}).

    Runs to a fixed point so stacked qualifiers all come off, then removes any
    remaining parenthetical context ('Stagehand (GS)' -> 'Stagehand').
    """
    text = str(raw or "").strip()
    found = set()
    changed = True
    while changed:
        changed = False
        for name, pat in QUALIFIERS.items():
            new = pat.sub("", text).strip(" -/,")
            # A qualifier may only MODIFY a role, never consume it whole.
            # Without this, bare 'Floater' matches the 'float' qualifier and
            # reduces to '', silently dropping 196 real occurrences; likewise
            # 'Breakout' and 'Strike' as standalone job titles.
            if not new:
                continue
            if new != text:
                text, changed = new, True
                found.add(name)
    stripped = PAREN_RE.sub(" ", text)
    if stripped != text:
        text = re.sub(r"\s+", " ", stripped).strip(" -/,")
    return text, found


def norm_token(raw):
    """Loose key for alias lookup: lowercase, punctuation-light."""
    t = str(raw or "").lower().strip()
    t = re.sub(r"[.–—]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -/,()")
    return t


def build_seed_rows():
    """Rows for the initial role_map.csv, sorted for a readable diff."""
    rows = []
    for canonical, aliases in CANONICAL.items():
        for alias in sorted(set(aliases)):
            rows.append({"raw_token": alias, "canonical_title": canonical})
    rows.sort(key=lambda r: (r["canonical_title"], r["raw_token"]))
    return rows


def write_seed(path=ROLE_MAP_CSV):
    rows = build_seed_rows()
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["raw_token", "canonical_title"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def load_map(path=ROLE_MAP_CSV):
    """alias -> canonical title. The CSV is authoritative once it exists."""
    if not os.path.exists(path):
        write_seed(path)
    out = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            alias = norm_token(row.get("raw_token"))
            title = (row.get("canonical_title") or "").strip()
            if alias and title:
                out[alias] = title

    # Every canonical title must resolve to ITSELF, including with its
    # parenthetical stripped -- 'Video Engineer (V1)' reduces to 'Video
    # Engineer' before lookup, which was in no alias list, so feeding a title
    # back in returned nothing. Explicit aliases still win.
    for title in CANONICAL:
        for form in (title, split_qualifiers(title)[0]):
            key = norm_token(form)
            if key:
                out.setdefault(key, title)
    return out


class RoleResolver:
    """Maps raw role text to canonical titles, recording what it couldn't."""

    def __init__(self, path=ROLE_MAP_CSV):
        self.map = load_map(path)
        self.unmapped = {}          # token -> count
        self.qualifiers = {}        # canonical title -> set(qualifiers)

    def resolve_cell(self, cell):
        """A cell like 'GAV / AVT, Cam Op' -> ['General AV', 'AV Tech',
        'Camera Operator']. Unknown tokens are recorded, never guessed."""
        titles = []
        for chunk in re.split(r"[/,;|]|\band\b|\+", str(cell or "")):
            # Try the RAW token against the map FIRST. Some real job codes
            # look exactly like a qualifier -- 'A-1', 'V-2', 'L-1' -- and
            # stripping them first reduces 'A-1' to 'A' and loses the job.
            # An explicit alias always beats qualifier parsing.
            raw_key = norm_token(chunk)
            if raw_key and raw_key in self.map:
                title = self.map[raw_key]
                if title not in titles:
                    titles.append(title)
                continue

            base, quals = split_qualifiers(chunk)
            key = norm_token(base)
            if not key:
                continue
            title = self.map.get(key)
            if title is None and "-" in key:
                # 'Department - Role' compounds: 'A/V - AV Technician',
                # 'V3 - Utility Tech', 'Stagehand -CREW CHIEF'. The role is
                # usually the trailing half; fall back to the leading half.
                segs = [norm_token(p) for p in re.split(r"-", base) if p.strip()]
                for cand in (segs[-1:] + segs[:1]) if segs else ():
                    if cand and cand in self.map:
                        title = self.map[cand]
                        break
            if title is None:
                self.unmapped[key] = self.unmapped.get(key, 0) + 1
                continue
            if title not in titles:
                titles.append(title)
            if quals:
                self.qualifiers.setdefault(title, set()).update(quals)
        return titles

    def unmapped_report(self, min_count=1):
        return sorted(
            ((t, c) for t, c in self.unmapped.items() if c >= min_count),
            key=lambda x: -x[1])
