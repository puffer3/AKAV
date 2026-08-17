"""The Conflicts tab: everything ambiguous, surfaced instead of guessed.

Nothing in the merge silently picks a winner. When two sources disagree about
a person -- which city they live in, whether they are do-not-hire, what grade
they got -- the rolodex row carries a best guess AND a conflict row lands here
with a plain-language question for the client.

The loop closes: `resolution` is filled in by the client on the sheet, pulled
back to `conflicts_resolved.csv`, and applied on the next run so a settled
question is never asked twice.
"""

import csv
import hashlib
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
RESOLVED_CSV = os.path.join(_HERE, "conflicts_resolved.csv")

FIELDNAMES = [
    "conflict_id", "person_key", "name", "type", "field",
    "value_a", "source_a", "value_b", "source_b",
    # Provenance: the exact place this came from, so a question can be
    # answered by looking at the actual row instead of taking our word for it.
    "source_file", "source_tab", "source_cell", "source_excerpt",
    "severity", "question_for_client", "resolution",
]


def source_ref(workbook="", tab="", cell=""):
    """'Atlanta Rolly.xlsx › ATL › B42' -- a pointer a human can follow."""
    return " › ".join(p for p in (workbook, tab, cell) if p)

# Ordered worst-first; drives sort order on the tab so the dangerous ones are
# the first thing seen.
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Rules we apply ourselves instead of asking. AK & KB's reasoning for the
# do_not_hire case: the ❌ in his phone is the MOST RECENT judgment -- if
# someone has both a signed contract and a ❌, they worked and then messed up
# badly on that last show. So the flag wins and the past work is just context.
AUTO_RESOLVED = {
    "do_not_hire": "Do Not Hire stands — flagged after the last show worked.",
}

QUESTIONS = {
    "do_not_hire": "Do Not Hire (flagged after the work below — no action "
                   "needed, listed for the record).",
    "residence": "Listed in more than one city. Where do they actually live?",
    "grade": "Graded differently on different sheets. Which grade stands?",
    "phone": "Two different phone numbers. Which one is current?",
    "email": "Two different email addresses. Which one is current?",
    "name_ambiguous": "One name appears to cover two different people. Are "
                      "these the same person?",
    "name_commentary": "Notes were typed into the name field. Is the name "
                       "split correctly?",
    "aka_ambiguous": "Unclear which is the real name and which is the "
                     "nickname. Which should the card show?",
    "role_unmapped": "This job title isn't in the standard list. What is it?",
    "rate_outlier": "Day rates for this role vary widely. Which is right?",
    "source_disagreement": "Two versions of the same rolodex disagree. Which "
                           "file is current?",
}


def conflict_id(person_key, ctype, field, value_a, value_b):
    """Stable across runs so a resolution stays attached to its conflict."""
    raw = "|".join([str(person_key), ctype, str(field),
                    str(value_a), str(value_b)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


class ConflictLog:
    def __init__(self, resolved_path=RESOLVED_CSV):
        self.rows = []
        self._seen = set()
        self.resolved = load_resolved(resolved_path)

    def add(self, person_key, name, ctype, field="", value_a="", source_a="",
            value_b="", source_b="", severity="medium", question=None,
            source_file="", source_tab="", source_cell="", source_excerpt=""):
        """Record a conflict. Returns the resolution if already settled.

        source_file/tab/cell pin the question to a real cell ('Atlanta
        Rolly.xlsx › ATL › B42') and source_excerpt carries the note text that
        triggered it, so "why is this person flagged" is answerable without
        re-deriving anything.
        """
        cid = conflict_id(person_key, ctype, field, value_a, value_b)
        # Client answers always outrank an automatic rule.
        prior = self.resolved.get(cid) or AUTO_RESOLVED.get(ctype, "")
        if cid in self._seen:
            return prior
        self._seen.add(cid)
        self.rows.append({
            "conflict_id": cid,
            "person_key": person_key,
            "name": name,
            "type": ctype,
            "field": field,
            "value_a": value_a,
            "source_a": source_a,
            "value_b": value_b,
            "source_b": source_b,
            "source_file": source_file,
            "source_tab": source_tab,
            "source_cell": source_cell,
            "source_excerpt": source_excerpt,
            "severity": severity,
            "question_for_client": question or QUESTIONS.get(ctype, ""),
            "resolution": prior,
        })
        return prior

    def open_rows(self):
        """Conflicts still awaiting a client answer."""
        return [r for r in self.rows if not r["resolution"]]

    def sorted_rows(self):
        return sorted(
            self.rows,
            key=lambda r: (bool(r["resolution"]),
                           SEVERITY_ORDER.get(r["severity"], 9),
                           r["type"], r["name"].lower()))

    def counts_by_type(self):
        out = {}
        for r in self.rows:
            key = (r["type"], bool(r["resolution"]))
            out[key] = out.get(key, 0) + 1
        return out

    def write(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(self.sorted_rows())
        return len(self.rows)


def load_resolved(path=RESOLVED_CSV):
    """conflict_id -> resolution text, from the client's answers."""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cid = (row.get("conflict_id") or "").strip()
            res = (row.get("resolution") or "").strip()
            if cid and res:
                out[cid] = res
    return out


def save_resolved(rows, path=RESOLVED_CSV):
    """Persist answered conflicts. Only id + resolution, so the checked-in
    file carries no contact details."""
    answered = [r for r in rows if r.get("resolution")]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["conflict_id", "type",
                                           "resolution"])
        w.writeheader()
        for r in sorted(answered, key=lambda x: x["conflict_id"]):
            w.writerow({"conflict_id": r["conflict_id"], "type": r["type"],
                        "resolution": r["resolution"]})
    return len(answered)
