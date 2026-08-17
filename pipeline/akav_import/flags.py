"""Flags carried by cell COLOUR and by note text -- kept separate on purpose.

The rollies mark people two ways at once: a font colour / fill, and a free-text
note. CLIENT_QUESTIONS #4 documents the colours (dark red = do-not-hire, bright
red = union only, light green = active) but they are *not* reliable on their
own. A row can be coloured because the person called out sick -- "PINKEYE",
"called out day 2" -- which is an incident, not a blacklisting.

So: colour is recorded as EVIDENCE, never as a verdict. The note text decides
the flag, and anything coloured-but-unexplained goes to Review with its exact
source cell so a human can look at the actual row.
"""

import re

# openpyxl ARGB -> what CLIENT_QUESTIONS #4 says it means. 'meaning' is the
# documented reading; 'confirmed' tracks whether the client ever answered.
COLOR_MEANINGS = {
    "FF980000": {"name": "dark red", "meaning": "do_not_hire", "confirmed": False},
    "FFA61C00": {"name": "dark red", "meaning": "do_not_hire", "confirmed": False},
    "FFCC0000": {"name": "dark red", "meaning": "do_not_hire", "confirmed": False},
    "FFFF0000": {"name": "bright red", "meaning": "union_only", "confirmed": False},
    "FFD9EAD3": {"name": "light green fill", "meaning": "active", "confirmed": False},
    "FFFFF2CC": {"name": "light yellow fill", "meaning": "unknown", "confirmed": False},
    "FFFFFF00": {"name": "yellow fill", "meaning": "unknown", "confirmed": False},
    "FF45818E": {"name": "teal", "meaning": "unknown", "confirmed": False},
    "FF0000FF": {"name": "blue", "meaning": "unknown", "confirmed": False},
}

# Colours that are just Google Sheets' default-ish greys, not a marking.
IGNORE_COLORS = {"FF000000", "00000000", "FFFFFFFF", "FF434343", "FF333333",
                 "FF313131", "FF666666", "FF999999"}

# Note text -> flag. Ordered: the first match wins, so the hard verdicts are
# tested before the softer incident language.
NOTE_FLAGS = [
    ("do_not_hire", re.compile(
        r"(?i)\bDNH\b|\bdo\s*not\s*hire\b|\bdont\s*hire\b|\bnever\s*again\b|"
        r"\bblacklist|\bfired\b|\bbanned\b|❌")),
    ("no_call_no_show", re.compile(
        r"(?i)no\s*[-/]?\s*call\s*[-/]?\s*no\s*[-/]?\s*show|\bNCNS\b|"
        r"\bghosted?\b|\bdidn'?t\s*show\b")),
    ("bailed", re.compile(
        r"(?i)\bbail(ed)?\b|\bwalked\s*off\b|\bleft\s*early\b|"
        r"\bquit\s*mid\b|\breplaced\s*himself\b")),
    ("called_out", re.compile(
        r"(?i)\bcall(ed)?\s*out\b|\bsick\b|\bpink\s*eye\b|\bpinkeye\b|"
        r"\binjur|\bcovid\b|\bflu\b|\bhospital|\bemergency\b|\bfamily\b")),
    ("union_only", re.compile(r"(?i)\bunion\s*only\b|\bIATSE\s*only\b|\bunion\b")),
    ("rate_dispute", re.compile(
        r"(?i)\bhaggl|\brate\s*dispute\b|\bdisrespect|\bcomplain")),
    # 'nah' used to sit in do_not_hire, but it is far too weak to carry a
    # BLOCKING, auto-resolved verdict -- "nah, he's solid now" would have
    # permanently barred someone, and the auto-resolution meant the client
    # never saw the question. Advisory only.
    ("negative_remark", re.compile(r"(?i)^\s*nah\b|\bnah\b\s*$")),
    ("unresponsive", re.compile(
        r"(?i)\bno\s*response\b|\bunreachable\b|\bnever\s*replied\b|"
        r"\bno\s*answer\b|\bnot\s*responding\b")),
]

# Which flags actually stop someone being booked. A call-out is NOT one:
# somebody who had pinkeye once is still hireable.
BLOCKING = {"do_not_hire"}

# Flags worth showing on the card but which do not block.
ADVISORY = {"no_call_no_show", "bailed", "rate_dispute", "unresponsive",
            "union_only", "called_out", "negative_remark"}

# Human-readable, for the Review tab.
FLAG_LABELS = {
    "do_not_hire": "Do Not Hire",
    "no_call_no_show": "No Call / No Show",
    "bailed": "Bailed on a job",
    "called_out": "Called out (illness/emergency)",
    "union_only": "Union only",
    "rate_dispute": "Rate dispute",
    "unresponsive": "Unresponsive",
    "negative_remark": "Negative remark — unclear if blocking",
    "unknown": "Marked, reason unclear",
}


def cell_ref(col_idx, row_idx):
    """0-based (col, row) -> 'B42', so a Review row points at the exact cell."""
    n, letters = col_idx + 1, ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return "%s%d" % (letters, row_idx + 1)


def color_of(cell):
    """Return (argb, kind) for a marked cell, or (None, None).

    Font colour is checked before fill: the dark-red DNH marking is font
    colour, and a row can carry both.
    """
    try:
        fc = cell.font.color
        if fc is not None and isinstance(getattr(fc, "rgb", None), str):
            rgb = fc.rgb.upper()
            if rgb not in IGNORE_COLORS:
                return rgb, "font"
    except Exception:
        pass
    try:
        fl = cell.fill
        if fl is not None and fl.fgColor is not None:
            rgb = getattr(fl.fgColor, "rgb", None)
            if isinstance(rgb, str):
                rgb = rgb.upper()
                if rgb not in IGNORE_COLORS:
                    return rgb, "fill"
    except Exception:
        pass
    return None, None


def flags_from_note(text):
    """Every flag the note text supports, strongest first."""
    out = []
    for name, pat in NOTE_FLAGS:
        if pat.search(str(text or "")):
            out.append(name)
    return out


def classify(note_text, color_argb=None):
    """Decide a person-row's flags from note text + colour.

    Returns {flags, blocking, evidence, needs_review}.

    The rule that matters: colour NEVER creates a do_not_hire on its own. If a
    row is dark red but the note says "PINKEYE", the flag is called_out and the
    person stays bookable. A colour with no explaining note is surfaced for
    review rather than interpreted.
    """
    flags = flags_from_note(note_text)
    evidence = []
    needs_review = False

    info = COLOR_MEANINGS.get((color_argb or "").upper())
    if info:
        evidence.append("marked %s" % info["name"])
        documented = info["meaning"]
        if documented == "unknown":
            needs_review = True
        elif documented not in flags:
            if documented == "do_not_hire":
                # Colour alone is not enough. If the note explains the marking
                # some other way (called out sick), trust the note.
                if not flags:
                    needs_review = True
                    evidence.append("dark red but no note explaining why")
                elif not set(flags) & BLOCKING:
                    needs_review = True
                    evidence.append(
                        "dark red, but note reads as %s" % FLAG_LABELS.get(
                            flags[0], flags[0]))
            else:
                flags.append(documented)

    return {
        "flags": flags,
        "blocking": sorted(set(flags) & BLOCKING),
        "evidence": evidence,
        "needs_review": needs_review,
    }
