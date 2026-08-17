"""Parse AK & KB's full phone contact export (All Contacts.vcf).

Two jobs, both driven by the emoji/symbol tags AK & KB put in contact NAMES:

  extract_flagged()  pull the do-not-hire cards (name contains U+274C) into
                     rolodex rows, flagged red.
  tag_inventory()    every symbol used in a name, with counts + samples, so
                     the meaning of each code can be pinned down before we
                     assign semantics to it (travel codes etc).

The export is a full personal address book (~6.8k cards), so nothing here
creates rolodex people on its own -- callers decide what to admit.
"""

import re
import unicodedata
from collections import Counter, defaultdict

RED_X = "❌"

# Emoji/symbol range we treat as a "tag" character in a name. Deliberately
# broad: we are inventorying unknown codes, not matching a known set.
_TAG_MIN = 0x2100
_VARIATION = {0xFE0E, 0xFE0F, 0x200D}  # VS15/VS16/ZWJ - presentation only


def _is_tag_char(ch):
    return ord(ch) >= _TAG_MIN


def _unfold(text):
    """RFC 6350 line unfolding: a leading space/tab continues the prior line."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _decode_qp(value):
    """Apple/Android exports sometimes quoted-printable encode non-ASCII."""
    try:
        import quopri

        return quopri.decodestring(value.encode("utf-8")).decode("utf-8", "replace")
    except Exception:
        return value


def iter_cards(path):
    """Yield {PROPERTY: [values]} for each vCard in the file."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    for chunk in _unfold(raw).split("BEGIN:VCARD")[1:]:
        card = defaultdict(list)
        for line in chunk.splitlines():
            if ":" not in line:
                continue
            head, _, value = line.partition(":")
            parts = head.split(";")
            prop = parts[0].upper()
            if any("QUOTED-PRINTABLE" in p.upper() for p in parts):
                value = _decode_qp(value)
            value = value.strip()
            if value:
                card[prop].append((head, value))
        if card:
            yield card


def _first(card, prop):
    vals = card.get(prop)
    return vals[0][1] if vals else ""


def _all(card, prop):
    return [v for _, v in card.get(prop, [])]


def split_tags(display_name):
    """Split a raw FN into (tag chars in order, remaining text).

    '\U0001f3b8❌Mason Lanius' -> (['\U0001f3b8', '❌'], 'Mason Lanius')
    """
    tags, rest = [], []
    for ch in display_name:
        if ord(ch) in _VARIATION:
            continue
        if _is_tag_char(ch):
            tags.append(ch)
        else:
            rest.append(ch)
    return tags, re.sub(r"\s+", " ", "".join(rest)).strip(" -/,")


# Commentary AK & KB type straight into the name field, e.g.
# "pat sample dud! dont hire! (l2?)". We never silently drop it -- we split it
# off into a note and flag the row for human review.
_NOTE_TRIGGER = re.compile(
    r"(?i)(\bdud\b|\bdo\s*n[o']?t\b|\bdont\b|\bno\s+hire\b|\breplaced\b|\bbail|"
    r"\bghost|\bfired\b|\bnever\b|\bsucks?\b|\bhorrible\b|\bwithout\b|[!?])"
)


def split_name_and_note(text):
    """Best-effort (name, note, needs_review) from a name field with commentary."""
    if not text:
        return "", "", True
    # Parenthetical trailing commentary: "Ian Hennessy(propaganda)"
    note_bits = []
    m = re.search(r"\(([^)]*)\)\s*$", text)
    if m:
        note_bits.append(m.group(1).strip())
        text = text[: m.start()].strip()

    trigger = _NOTE_TRIGGER.search(text)
    if trigger:
        # Cut at the word boundary before the trigger, keeping >=1 name token.
        head = text[: trigger.start()].strip()
        tail = text[trigger.start() :].strip()
        if head.split():
            note_bits.insert(0, tail)
            text = head
    name = text.strip(" -/,")
    note = "; ".join(b for b in note_bits if b)
    # A plausible person name is 1-3 tokens; more means we probably mis-split.
    needs_review = bool(note) or len(name.split()) > 3 or not name
    return name, note, needs_review


def _digits(phone):
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def _norm_name(name):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", n.lower()).strip()


def extract_flagged(path, marker=RED_X):
    """Return deduped rows for every card whose name contains `marker`."""
    rows = []
    for card in iter_cards(path):
        display = _first(card, "FN") or _first(card, "N").replace(";", " ").strip()
        if marker not in display:
            continue
        tags, cleaned = split_tags(display)
        name, name_note, needs_review = split_name_and_note(cleaned)
        phones = [p for p in (_digits(v) for v in _all(card, "TEL")) if p]
        emails = [e.lower() for e in _all(card, "EMAIL")]
        rows.append(
            {
                "name": name,
                "raw_name": display,
                "phones": sorted(set(phones)),
                "emails": sorted(set(emails)),
                "org": _first(card, "ORG").rstrip(";"),
                "name_note": name_note,
                "card_note": " ".join(_all(card, "NOTE")),
                # Tags other than the marker itself -- unknown codes, kept as-is.
                "other_tags": [t for t in tags if t != marker],
                "do_not_hire": True,
                "needs_review": needs_review,
                "source": "All Contacts.vcf",
            }
        )
    return _dedupe(rows)


def _dedupe(rows):
    """Merge cards that are the same human (shared phone/email, else name)."""
    by_key, order = {}, []
    for r in rows:
        key = None
        for cand in (
            *(("p", p) for p in r["phones"]),
            *(("e", e) for e in r["emails"]),
        ):
            if cand in by_key:
                key = by_key[cand]
                break
        if key is None:
            nname = _norm_name(r["name"])
            # Never key on an EMPTY name: a card whose FN is only tag
            # characters yields '', and every later nameless card without a
            # phone or email would merge into whichever claimed it first,
            # collapsing distinct do-not-hire people into one row.
            key = by_key.get(("n", nname)) if nname else None
        if key is None:
            key = len(order)
            order.append(dict(r, dup_count=1))
        else:
            tgt = order[key]
            tgt["dup_count"] += 1
            for f in ("phones", "emails", "other_tags"):
                tgt[f] = sorted(set(tgt[f]) | set(r[f]))
            for f in ("org", "name_note", "card_note"):
                if r[f] and r[f] not in tgt[f]:
                    tgt[f] = (tgt[f] + "; " + r[f]).strip("; ")
        for p in r["phones"]:
            by_key.setdefault(("p", p), key)
        for e in r["emails"]:
            by_key.setdefault(("e", e), key)
        nname = _norm_name(r["name"])
        if nname:
            by_key.setdefault(("n", nname), key)
    return order


def tag_inventory(path, min_count=1):
    """Count every symbol used in contact names, with sample names.

    This is the decode sheet: we do not guess what a code means.
    """
    counts = Counter()
    samples = defaultdict(list)
    pairs = Counter()
    for card in iter_cards(path):
        display = _first(card, "FN")
        if not display:
            continue
        tags, cleaned = split_tags(display)
        uniq = list(dict.fromkeys(tags))
        for t in uniq:
            counts[t] += 1
            if len(samples[t]) < 6 and cleaned:
                samples[t].append(cleaned)
        for i, a in enumerate(uniq):
            for b in uniq[i + 1 :]:
                pairs[(a, b)] += 1
    return [
        {
            "tag": t,
            "count": c,
            "name": unicodedata.name(t, "UNKNOWN"),
            "codepoint": f"U+{ord(t):04X}",
            "samples": samples[t],
        }
        for t, c in counts.most_common()
        if c >= min_count
    ], pairs
