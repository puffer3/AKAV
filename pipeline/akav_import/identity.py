"""Person identity resolution within one workbook.

Union-find over work records and crew-status rows: entries sharing a
strong key (email, then phone digits) merge into one person; name-only
entries merge only when the normalized name is unambiguous. Each cluster
gets a personKey ('email:…' > 'phone:…' > 'name:…') used for recordHash
dedupe and by the Apps Script upsert.
"""

import hashlib
from collections import Counter

from .models import Person
from .normalize import norm_email, norm_name, norm_phone


class _UF:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _keys(name, email, phone):
    out = []
    e, p, n = norm_email(email), norm_phone(phone), norm_name(name)
    if e:
        out.append("email:" + e)
    if p:
        out.append("phone:" + p)
    if n:
        out.append("name:" + n)
    return out


def resolve(records, status_rows, sheet_grades, manual_by_name,
            manual_by_email):
    """Mutates records in place (personKey, recordHash) and returns
    (people, warnings)."""
    uf = _UF()
    entries = []          # (kind, obj, keys)

    for rec in records:
        ks = _keys(rec.name, rec.email, rec.phoneDigits)
        entries.append(("record", rec, ks))
    for sr in status_rows:
        ks = _keys(sr.name, sr.email, sr.phoneDigits)
        entries.append(("status", sr, ks))

    warnings = []

    # Pass 1: merge on strong keys only (email, phone)
    for _, _, ks in entries:
        strong = [k for k in ks if not k.startswith("name:")]
        for k in strong[1:]:
            uf.union(strong[0], k)

    # Pass 2: merge name keys only when unambiguous. If one normalized
    # name spans multiple strong-key clusters, two humans share the name —
    # warn and leave name-only entries in their own cluster.
    name_map = {}
    for idx, (_, _, ks) in enumerate(entries):
        n = next((k for k in ks if k.startswith("name:")), None)
        if n:
            name_map.setdefault(n, []).append(idx)
    for n, idxs in name_map.items():
        roots = set()
        for i in idxs:
            strong = [k for k in entries[i][2] if not k.startswith("name:")]
            if strong:
                roots.add(uf.find(strong[0]))
        if len(roots) > 1:
            warnings.append(
                "AMBIGUOUS NAME %r spans %d different people (distinct "
                "email/phone) — name-only rows for it were NOT merged"
                % (n[5:], len(roots)))
        elif roots:
            uf.union(roots.pop(), n)

    # Build clusters
    clusters = {}
    for kind, obj, ks in entries:
        if not ks:
            warnings.append("dropped %s row with no name/email/phone" % kind)
            continue
        root = uf.find(ks[0])
        clusters.setdefault(root, []).append((kind, obj, ks))

    people = []
    for root, members in clusters.items():
        emails = [k[6:] for _, _, ks in members for k in ks
                  if k.startswith("email:")]
        phones = [k[6:] for _, _, ks in members for k in ks
                  if k.startswith("phone:")]
        names_raw = []
        for kind, obj, _ in members:
            if obj.name:
                names_raw.append(obj.name.strip())
        best_name = max(names_raw, key=len) if names_raw else ""
        email = Counter(emails).most_common(1)[0][0] if emails else ""
        phone = Counter(phones).most_common(1)[0][0] if phones else ""

        if email:
            person_key = "email:" + email
        elif phone:
            person_key = "phone:" + phone
        else:
            person_key = "name:" + norm_name(best_name)

        recs = [obj for kind, obj, _ in members if kind == "record"]
        stats = [obj for kind, obj, _ in members if kind == "status"]

        # Key + hash every record, then aggregate over the deduped set so
        # tidy/WB overlap doesn't double-count rates.
        for rec in recs:
            rec.personKey = person_key
            rec.recordHash = record_hash(rec)
        uniq = {}
        for rec in recs:
            uniq.setdefault(rec.recordHash, rec)
        recs = list(uniq.values())

        rates = [r.rate for r in recs if r.rate is not None]
        amount = None
        for s in stats:
            if s.amount is not None:
                amount = (amount or 0.0) + s.amount

        nname = norm_name(best_name)
        grade = (manual_by_email.get(email, "")
                 or manual_by_name.get(nname, "")
                 or sheet_grades.get(nname, ""))

        notes = "; ".join(sorted({s.notes for s in stats if s.notes}))

        people.append(Person(
            personKey=person_key,
            name=best_name,
            email=email,
            phoneDigits=phone,
            grade=grade,
            notes=notes,
            total=amount,
            days=len({r.date for r in recs}),
            positions=[p for p, _ in Counter(
                r.position for r in recs if r.position).most_common()],
            rateMin=min(rates) if rates else None,
            rateMax=max(rates) if rates else None,
            rateSum=sum(rates) if rates else None,
        ))

    # Status-only people (worked nothing we parsed) are still returned —
    # the uploader skips them but the preview shows them as a warning.
    for p in people:
        if p.days == 0:
            warnings.append(
                "%s appears in Crew Status but has no parsed work records"
                % (p.name or p.personKey))

    people.sort(key=lambda p: norm_name(p.name))
    return people, warnings


def record_hash(rec):
    """Stable dedupe key. callStart included so two same-day same-position
    calls (split shifts) don't collapse."""
    raw = "|".join([
        rec.personKey, rec.date, rec.position or "", rec.callStart or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
