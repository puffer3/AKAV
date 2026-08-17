"""Person notes that keep their SOURCE.

The master sheet's Notes column already tags job-import notes with the job:

    [InfoSys Connect 2026] Confirm MP, 2 OT 2 DT 5/18

but rolodex-imported notes go in with an empty label (`mergeNotes(.., '')`
in google-apps-script.gs), so they arrive unattributed AND semicolon-mashed:

    GAV; Stagehand; from a lead - do not rehire, needy, late;
    Gav; Decent attitude

Five separate observations from different sheets on one line. When one says
"do not rehire" and another says "Decent attitude", there is no way to
tell who said which, or which is more recent. So: one observation per line,
every line tagged with where it came from.

Format is unchanged from what the sheet already uses -- '[label] text', joined
by newline, merge-only -- so existing notes stay valid and `mergeNotes` keeps
working. Only the label gets filled in where it was previously blank.
"""

import re

# '[Cisco Live 2026] strong cam op'
TAGGED_RE = re.compile(r"^\s*\[(?P<label>[^\]]+)\]\s*(?P<text>.*)$")

# Rolodex cells often hold several observations in one cell.
SPLIT_RE = re.compile(r"\s*;\s*|\s*\|\s*|\n+")

# Fragments that carry no information and just add noise to a card.
NOISE = {"", "-", "--", "n/a", "na", "none", "notes", "note", "?", "??",
         "yes", "no", "x", "tbd", "idk"}


# Claimed-but-unworked skills are NOT a note. They live in their own
# `Claimed Skills` field -- see roles.split_claimed_vs_worked -- because the
# question they answer is a search, and prose in a notes column doesn't
# survive filtering or hand-editing.


def source_label(workbook="", tab="", job=""):
    """The tag that goes in front of a note.

    A job note is labelled with the job ('Cisco Live 2026'); a rolodex note
    with the workbook and tab it was read from ('Atlanta Rolly › ATL'), which
    is enough to go and look at the actual row.
    """
    if job:
        return str(job).strip()
    book = re.sub(r"(?i)\.xlsx$", "", str(workbook or "").strip())
    parts = [p for p in (book, str(tab or "").strip()) if p]
    return " › ".join(parts)


def split_observations(cell):
    """One cell -> the distinct observations inside it."""
    out = []
    for chunk in SPLIT_RE.split(str(cell or "")):
        t = re.sub(r"\s+", " ", chunk).strip(" .,-")
        if t and t.lower() not in NOISE:
            out.append(t)
    return out


def make(cell, workbook="", tab="", job=""):
    """Return [{'label','text'}] for one source cell."""
    label = source_label(workbook, tab, job)
    return [{"label": label, "text": t} for t in split_observations(cell)]


def parse_existing(blob):
    """Read a Notes column value back into entries, preserving untagged ones."""
    out = []
    for line in str(blob or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = TAGGED_RE.match(line)
        if m:
            out.append({"label": m.group("label").strip(),
                        "text": m.group("text").strip()})
        else:
            # Legacy unattributed note -- keep it, never discard.
            out.append({"label": "", "text": line})
    return out


def _key(entry):
    return re.sub(r"[^a-z0-9]", "", entry["text"].lower())


def merge(existing_blob, new_entries):
    """Merge-only, matching the sheet's contract: nothing is ever clobbered.

    Deduped on note TEXT, so re-importing the same rolodex doesn't stack up
    copies, and a note that already exists untagged gets its source filled in
    rather than being added a second time.
    """
    # Split legacy mashed lines FIRST. A stored note like
    # 'GAV; Stagehand; from a lead - ...' must become its own observations
    # before deduping, or re-importing adds the split versions alongside the
    # mashed original and the card says everything twice.
    entries = split_legacy_blob(existing_blob)
    by_key = {}
    order = []
    for i, e in enumerate(entries):
        # An EXISTING note is never dropped, even when _key() reduces it to ''
        # (a note made only of symbols or emoji -- plausible in this data).
        # Falling through to `continue` here silently clobbered it, breaking
        # the merge-only contract.
        k = _key(e) or ("raw:%d:%s" % (i, e["text"]))
        if k not in by_key:
            by_key[k] = e
            order.append(k)

    for e in new_entries or []:
        k = _key(e)
        if not k:
            continue
        if k in by_key:
            # Same observation seen again -- fill in a source if it had none.
            if not by_key[k]["label"] and e["label"]:
                by_key[k]["label"] = e["label"]
            continue
        by_key[k] = dict(e)
        order.append(k)

    lines = []
    for k in order:
        e = by_key[k]
        lines.append("[%s] %s" % (e["label"], e["text"]) if e["label"]
                     else e["text"])
    return "\n".join(lines)


def split_legacy_blob(blob, label=""):
    """Repair an already-mashed note: 'a; b; c' -> three entries.

    Used once against the live sheet's 260 unattributed notes. Without a
    label the observations still get separated onto their own lines, which is
    an improvement even when the source is unrecoverable.
    """
    entries = []
    for e in parse_existing(blob):
        if e["label"]:
            entries.append(e)
            continue
        for t in split_observations(e["text"]):
            entries.append({"label": label, "text": t})
    return entries
