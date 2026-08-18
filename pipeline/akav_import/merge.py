"""Merge every source into one row per human.

Six sources, each knowing different things about the same people:

    rollies      name, phone, email, roles, market, grade, notes, shortlist
    vCard export do-not-hire flags
    vendor list  home address (the only trustworthy residence evidence)
    contracts    positions actually worked + real day rates
    job tabs     positions worked in the ShowPhaze era (no rates/grades)
    War Room     the job registry the work records point at

Identity follows the same precedence the Apps Script upsert uses -- email,
then phone, then name -- via union-find, so a person known by email in one
source and by phone in another still collapses to one row. Name-only links
are made ONLY when the name is unambiguous: if one normalized name spans two
different strong keys, that is two humans, and merging them would silently
fuse two people's work history.

Nothing here guesses. Where two sources disagree the row keeps the
higher-precedence value and the disagreement becomes a Conflicts row.
"""

import re
from collections import Counter, defaultdict

from .conflicts import ConflictLog, source_ref
from .normalize import norm_email, norm_name, norm_phone

# Which source wins for a given field, best first. Evidence beats hearsay:
# an address on a tax form outranks a city typed into a rolodex.
FIELD_PRECEDENCE = {
    "home_base": ["vendor", "onboarding"],
    "address":   ["vendor", "onboarding"],
    "state":     ["vendor"],
    "zip":       ["vendor"],
    "email":     ["onboarding", "vendor", "contract", "rolly", "jobtab", "vcard"],
    "phoneDigits": ["onboarding", "vendor", "rolly", "jobtab", "vcard", "contract"],
    "name":      ["contract", "vendor", "onboarding", "rolly", "jobtab", "vcard"],
}

# A market is additive (someone works several); a home_base is singular.
MULTI_FIELDS = ("markets", "jobTitles", "claimedSkills", "aka", "sources",
                "rollyLists")


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


def keys_for(rec):
    """Identity keys, strongest first. Mirrors the Apps Script's order."""
    out = []
    e, p = norm_email(rec.get("email")), norm_phone(rec.get("phoneDigits"))
    n = norm_name(rec.get("name"))
    if e:
        out.append("email:" + e)
    if p and len(p) >= 10:
        out.append("phone:" + p)
    # Single-token names are not identities -- 'Adam' is not a person, it is
    # a first name shared by several. Matching on one fuses unrelated crew.
    if n and " " in n:
        out.append("name:" + n)
    return out


def resolve_identities(records):
    """Union-find over every source record. Returns (clusters, warnings).

    `records` are dicts with at least name/email/phoneDigits plus a
    'source' tag.
    """
    uf = _UF()
    keyed = []
    for rec in records:
        ks = keys_for(rec)
        keyed.append((rec, ks))

    # Pass 1 -- strong keys only (email, phone). Never name at this stage.
    for _, ks in keyed:
        strong = [k for k in ks if not k.startswith("name:")]
        for k in strong[1:]:
            uf.union(strong[0], k)

    # Pass 2 -- a name links only when it maps to exactly one strong cluster.
    warnings = []
    by_name = defaultdict(list)
    for i, (_, ks) in enumerate(keyed):
        n = next((k for k in ks if k.startswith("name:")), None)
        if n:
            by_name[n].append(i)
    ambiguous = set()
    for n, idxs in by_name.items():
        roots = set()
        for i in idxs:
            strong = [k for k in keyed[i][1] if not k.startswith("name:")]
            if strong:
                roots.add(uf.find(strong[0]))
        if len(roots) > 1:
            ambiguous.add(n)
            warnings.append(
                "AMBIGUOUS NAME %r spans %d people with different "
                "email/phone -- name-only rows NOT merged" % (n[5:], len(roots)))
        elif roots:
            uf.union(roots.pop(), n)

    clusters = defaultdict(list)
    for rec, ks in keyed:
        if not ks:
            warnings.append("dropped %s row with no name/email/phone"
                            % rec.get("source", "?"))
            continue
        clusters[uf.find(ks[0])].append(rec)
    return clusters, warnings, ambiguous


def _pick(field, recs, conflicts, person_key, display_name):
    """Highest-precedence non-empty value; disagreements become conflicts."""
    order = FIELD_PRECEDENCE.get(field, [])
    seen = []
    for rec in recs:
        v = str(rec.get(field) or "").strip()
        if v:
            seen.append((rec.get("source", "?"), v, rec))
    if not seen:
        return ""

    def rank(item):
        src = item[0]
        return order.index(src) if src in order else len(order)

    seen.sort(key=rank)
    best = seen[0]

    # Only a genuine disagreement is a conflict -- same value from two
    # sources is corroboration, not contradiction.
    distinct = {v.lower(): (s, v, r) for s, v, r in seen}
    if len(distinct) > 1 and field in ("home_base", "email", "phoneDigits"):
        others = [x for x in distinct.values() if x[1].lower() != best[1].lower()]
        o = others[0]
        conflicts.add(
            person_key, display_name,
            {"home_base": "residence"}.get(field, field),
            field=field,
            value_a=best[1], source_a=best[0],
            value_b=o[1], source_b=o[0],
            severity="medium" if field == "home_base" else "high",
            source_file=o[2].get("source_file", ""),
            source_tab=o[2].get("source_tab", ""),
            source_cell=o[2].get("source_cell", ""),
            source_excerpt=o[2].get("source_excerpt", ""))
    return best[1]


def _collect(field, recs):
    """Union of a multi-valued field, order preserved, deduped."""
    out = []
    for rec in recs:
        v = rec.get(field)
        if not v:
            continue
        for item in (v if isinstance(v, (list, tuple, set)) else [v]):
            item = str(item).strip()
            if item and item not in out:
                out.append(item)
    return out


def build_people(records, conflicts=None):
    """Six sources in, one row per human out.

    Returns (people, conflicts, warnings).
    """
    conflicts = conflicts or ConflictLog()
    clusters, warnings, ambiguous = resolve_identities(records)

    people = []
    for root, recs in clusters.items():
        emails = [norm_email(r.get("email")) for r in recs if r.get("email")]
        phones = [norm_phone(r.get("phoneDigits")) for r in recs
                  if norm_phone(r.get("phoneDigits"))]
        email = Counter([e for e in emails if e]).most_common(1)
        phone = Counter([p for p in phones if len(p) >= 10]).most_common(1)
        email = email[0][0] if email else ""
        phone = phone[0][0] if phone else ""

        names = [str(r.get("name") or "").strip() for r in recs if r.get("name")]
        display = max(names, key=len) if names else ""

        if email:
            person_key = "email:" + email
        elif phone:
            person_key = "phone:" + phone
        else:
            person_key = "name:" + norm_name(display)

        worked = _collect("jobTitles", recs)
        claimed = [c for c in _collect("claimedSkills", recs) if c not in worked]

        # Do-not-hire is sticky and always wins -- the flag is the most
        # recent judgment, even against a signed contract.
        blocking = [r for r in recs if r.get("doNotHire")]
        status = "Do Not Hire" if blocking else ""
        advisory = _collect("advisoryFlags", recs)

        person = {
            "personKey": person_key,
            "name": display,
            "aka": _collect("aka", recs),
            "email": email,
            "phoneDigits": phone,
            "home_base": _pick("home_base", recs, conflicts, person_key, display),
            "address": _pick("address", recs, conflicts, person_key, display),
            "state": _pick("state", recs, conflicts, person_key, display),
            "zip": _pick("zip", recs, conflicts, person_key, display),
            "markets": _collect("markets", recs),
            "jobTitles": worked,
            "claimedSkills": claimed,
            "dayRate": _day_rate(recs),
            "status": status,
            "advisory": advisory,
            # Do Not Hire clears the shortlist star. Someone was starred in
            # a rolodex and flagged later; showing both leaves a gold star on
            # a red row. The flag is the more recent judgment, so it wins --
            # the old shortlist membership stays visible in Rolly Lists.
            "shortlisted": (not status) and any(r.get("shortlisted") for r in recs),
            "grade": _pick_grade(recs),
            "notes": _collect("notes", recs),
            "rollyLists": _collect("rollyLists", recs),
            "sources": sorted({r.get("source", "?") for r in recs}),
            "nameAmbiguous": ("name:" + norm_name(display)) in ambiguous,
        }

        if blocking and any(r.get("source") in ("contract", "jobtab") for r in recs):
            # Worked AND flagged: auto-resolved (they messed up on the last
            # show), recorded so the history stays visible.
            conflicts.add(person_key, display, "do_not_hire",
                          field="status",
                          value_a="Do Not Hire", source_a="contact export",
                          value_b="has worked jobs", source_b="contracts/job sheets",
                          severity="low")
        was_shortlisted = any(r.get("shortlisted") for r in recs)
        if blocking and was_shortlisted:
            # Someone was a first-call in a rolodex and flagged do-not-hire
            # later. The star is cleared automatically, but a person good
            # enough to shortlist is worth a human confirming the flag.
            lists = ", ".join(_collect("rollyLists", recs)[:3])
            conflicts.add(
                person_key, display, "do_not_hire",
                field="shortlisted",
                value_a="Do Not Hire", source_a="contact export",
                value_b="was shortlisted", source_b=lists or "rolodex",
                severity="high",
                question=("Shortlisted in the rolodex but flagged do-not-hire "
                          "in the contact list. Star has been cleared - "
                          "is the flag right?"))
        if person["nameAmbiguous"]:
            conflicts.add(person_key, display, "name_ambiguous",
                          field="name", value_a=display, source_a="multiple",
                          severity="high")
        people.append(person)

    people.sort(key=lambda p: norm_name(p["name"]))
    return people, conflicts, warnings


def _day_rate(recs):
    """Median contracted day rate. Only contract-sourced rates count --
    payroll totals and ShowPhaze-era money are never rates."""
    rates = []
    for r in recs:
        v = r.get("dayRate")
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 100 <= f <= 2000:
            rates.append(f)
    if not rates:
        return ""
    rates.sort()
    n = len(rates)
    return rates[n // 2] if n % 2 else (rates[n // 2 - 1] + rates[n // 2]) / 2


def _pick_grade(recs):
    """Worst grade wins, and X (do-not-rehire) beats everything."""
    order = ["X", "F", "D", "C", "B", "A"]
    seen = []
    for r in recs:
        g = str(r.get("grade") or "").strip().upper()
        if g and g[0] in order:
            seen.append(g)
    if not seen:
        return ""
    return sorted(seen, key=lambda g: order.index(g[0]))[0]
