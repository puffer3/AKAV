"""Splitting a raw name field into a primary name + AKA (nicknames).

Crew are booked by nickname as often as by legal name, so the AKA is a
front-of-card field, not a footnote. All three sources carry nicknames in
different shapes, and every shape below is a real example from Source Files:

    'Kiki (Katarina Lindqvist)'     paren is the FULLER name  -> primary=paren
    'Bee (Bethany) Sample'          paren replaces a token    -> Bethany Sample / Bee Sample
    'Casper (Matthew) Doe'          same                      -> Matthew Doe / Casper Doe
    'pat sample "jiggy"'            quoted nickname           -> Pat Sample / jiggy
    'Blake Doe "Creature"'          quoted nickname
    'Yoshi - Jamie Sample'          dash-separated (filenames)
    'Ana Lopez Reyes'               no nickname               -> unchanged

The rule for a trailing parenthetical is deliberately conservative: a
MULTI-WORD parenthetical is treated as the formal name (the head being a
nickname), a single word is treated as the nickname. Anything that trips that
boundary is returned with `ambiguous=True` so it lands on the Conflicts tab
instead of being silently guessed wrong.
"""

import re

# Double quotes are unambiguous nickname delimiters.
_QUOTED = re.compile(r"[\"“”]([^\"“”]{2,})[\"“”]")

# Single quotes are NOT: an apostrophe inside a surname looks identical.
# Treating them as delimiters shredded real names --
#   "Sean O'Brien D'Amato" -> ('Sean O Amato', ['Brien D'])
# which then broke identity matching, since norm_name() of a mangled name
# matches nothing. So a single-quoted nickname must be flanked by
# word boundaries on the OUTSIDE and not sit between two letters.
_QUOTED_SINGLE = re.compile(r"(?<![A-Za-z])['‘’]([^'‘’]{2,}?)['‘’](?![A-Za-z])")

_PAREN = re.compile(r"\(([^)]+)\)")


def _tidy(s):
    return re.sub(r"\s+", " ", str(s or "")).strip(" -_,/")


def split_aka(raw):
    """Return (primary_name, [aka...], ambiguous).

    Never loses text: anything removed from the primary comes back as an AKA.
    """
    text = _tidy(raw)
    if not text:
        return "", [], False

    akas = []
    ambiguous = False

    # 1. Quoted nicknames anywhere: pat sample "jiggy"
    for pat in (_QUOTED, _QUOTED_SINGLE):
        for m in pat.finditer(text):
            akas.append(_tidy(m.group(1)))
        text = _tidy(pat.sub(" ", text))

    # 2. Parenthetical
    m = _PAREN.search(text)
    if m:
        inner = _tidy(m.group(1))
        head = _tidy(text[: m.start()])
        tail = _tidy(text[m.end():])

        if tail:
            # Mid-name: 'Bee (Bethany) Sample'. The paren replaces the first
            # token; both readings are kept as full names.
            primary = _tidy(" ".join([inner, tail]))
            akas.append(_tidy(" ".join([head, tail])) if head else inner)
            text = primary
        elif len(inner.split()) > len(head.split()):
            # Trailing and fuller: 'Kiki (Katarina Lindqvist)'.
            akas.append(head)
            text = inner
        else:
            # Trailing and shorter: 'Ian Hennessy (propaganda)'.
            akas.append(inner)
            text = head
            # A single-token head with a parenthetical is genuinely unclear
            # about which is the person's name.
            ambiguous = len(head.split()) < 2

    text = _tidy(text)
    seen, out = {_norm(text)}, []
    for a in akas:
        a = _tidy(a)
        if a and _norm(a) not in seen:
            seen.add(_norm(a))
            out.append(a)
    return text, out, ambiguous


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", str(s or "").lower()).strip()


def split_segments(raw, drop_re=None):
    """Split a dash-separated filename-ish name into segments.

    'Yoshi - Jamie Sample - AKAV - Statement of Work' with the boilerplate
    filter applied -> ['Yoshi', 'Jamie Sample']. The longest segment is
    the primary name; the rest are AKAs.
    """
    segs = [_tidy(s) for s in re.split(r"\s+-\s+|\s+–\s+", str(raw or ""))]
    segs = [s for s in segs if s and not (drop_re and drop_re.match(s))]
    if not segs:
        return "", []
    segs.sort(key=lambda s: (-len(s.split()), -len(s)))
    return segs[0], segs[1:]
