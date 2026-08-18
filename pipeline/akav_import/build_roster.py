"""Build the whole rolodex: every source -> merged people -> upload.

Run in three steps so nothing surprising reaches a Sheet:

    python3 -m akav_import.build_roster build      # parse + merge, write JSON
    python3 -m akav_import.build_roster preview    # what would change
    python3 -m akav_import.build_roster upload     # send it

`upload` refuses to run without AKAV_ENDPOINT and AKAV_TOKEN, and prints
the endpoint it is about to write to so a test copy can't be confused with
the live Sheet.
"""

import json
import os
import sys
from collections import Counter

from . import collect, config, merge, uploader
from .conflicts import ConflictLog
from .roles import RoleResolver

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
PEOPLE_JSON = os.path.join(OUT, "roster-people.json")
CONFLICTS_CSV = os.path.join(OUT, "roster-conflicts.csv")


def build():
    os.makedirs(OUT, exist_ok=True)
    resolver = RoleResolver()
    print("collecting sources...")
    # The live roster joins as an identity source when we can reach it, so
    # our people line up 1:1 with existing rows.
    recs, counts = collect.collect_all(
        resolver,
        endpoint=os.environ.get("AKAV_ENDPOINT"),
        token=os.environ.get("AKAV_TOKEN"))
    print("\nmerging %d source records..." % len(recs))
    people, conflicts, warnings = merge.build_people(recs, ConflictLog())

    with open(PEOPLE_JSON, "w", encoding="utf-8") as fh:
        json.dump(people, fh, indent=1, ensure_ascii=False)
    conflicts.write(CONFLICTS_CSV)

    print("\nUNIQUE PEOPLE: %d" % len(people))
    filled = lambda k: sum(1 for p in people if p.get(k))
    for k in ("email", "phoneDigits", "home_base", "markets", "jobTitles",
              "claimedSkills", "dayRate", "grade", "notes"):
        print("  %-16s %5d" % (k, filled(k)))
    print("  %-16s %5d" % ("shortlisted", sum(1 for p in people if p["shortlisted"])))
    print("  %-16s %5d" % ("do not hire", sum(1 for p in people if p["status"])))

    # Two merged people landing on one existing row means the upload's
    # second write would overwrite the first. The merge kept them apart on
    # purpose (same name, different phone/email); the Sheet has them
    # together. A human decides, so record it rather than guess.
    ep, tok = os.environ.get("AKAV_ENDPOINT"), os.environ.get("AKAV_TOKEN")
    if ep and tok:
        _flag_row_collisions(people, conflicts, ep, tok)
        conflicts.write(CONFLICTS_CSV)

    unmapped = resolver.unmapped_report(min_count=3)
    if unmapped:
        print("\nunmapped role tokens (>=3 uses) -- add to role_map.csv:")
        for tok, n in unmapped[:15]:
            print("    %4d  %r" % (n, tok))

    print("\nconflicts: %d (%d awaiting an answer)"
          % (len(conflicts.rows), len(conflicts.open_rows())))
    for (t, resolved), n in sorted(conflicts.counts_by_type().items()):
        print("    %-22s %4d %s" % (t, n, "(auto-resolved)" if resolved else ""))
    if warnings:
        print("\n%d identity warnings (first 5):" % len(warnings))
        for w in warnings[:5]:
            print("    " + w)
    print("\nwrote %s\n      %s" % (PEOPLE_JSON, CONFLICTS_CSV))
    return people


def _flag_row_collisions(people, conflicts, endpoint, token):
    from collections import defaultdict
    from .normalize import norm_email, norm_name, norm_phone
    roster = uploader.fetch_roster(endpoint, token) or []
    by_email, by_phone, by_name = {}, {}, {}
    for r in roster:
        if r.get("email"):
            by_email.setdefault(norm_email(r["email"]), r)
        if r.get("phoneDigits"):
            by_phone.setdefault(norm_phone(r["phoneDigits"]), r)
        n = norm_name(r.get("name") or "")
        if n and " " in n:
            by_name.setdefault(n, r)
    claim = defaultdict(list)
    for p in people:
        n = norm_name(p["name"])
        hit = (by_email.get(norm_email(p["email"]))
               or by_phone.get(norm_phone(p["phoneDigits"]))
               or (by_name.get(n) if " " in n else None))
        if hit:
            claim[hit["row"]].append(p)
    for row, ps in claim.items():
        if len(ps) < 2:
            continue
        first = ps[0]
        for other in ps[1:]:
            conflicts.add(
                first["personKey"], first["name"], "name_ambiguous",
                field="sheet row",
                value_a=first["name"], source_a=first["personKey"],
                value_b=other["name"], source_b=other["personKey"],
                severity="high",
                source_cell="row %s" % row,
                question=("Two people in the import match this one Sheet row. "
                          "Are they the same person? If not, the row needs "
                          "splitting before importing."))
    return claim


def _load():
    if not os.path.exists(PEOPLE_JSON):
        sys.exit("run `build` first -- no %s" % PEOPLE_JSON)
    with open(PEOPLE_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def preview():
    people = _load()
    ep, tok = os.environ.get("AKAV_ENDPOINT"), os.environ.get("AKAV_TOKEN")
    print("people to send: %d" % len(people))
    if not (ep and tok):
        print("(set AKAV_ENDPOINT + AKAV_TOKEN to compare against a live roster)")
        return
    print("comparing against: %s" % ep)
    roster = uploader.fetch_roster(ep, tok) or []
    from .normalize import norm_email, norm_name, norm_phone
    by_email = {norm_email(r.get("email")): r for r in roster if r.get("email")}
    by_phone = {norm_phone(r.get("phoneDigits")): r for r in roster if r.get("phoneDigits")}
    by_name = {norm_name(r.get("name")): r for r in roster if r.get("name")}
    hit = Counter()
    for p in people:
        if norm_email(p["email"]) in by_email:
            hit["email"] += 1
        elif norm_phone(p["phoneDigits"]) in by_phone:
            hit["phone"] += 1
        elif norm_name(p["name"]) in by_name:
            hit["name"] += 1
        else:
            hit["new"] += 1
    print("  existing rows matched by email: %d" % hit["email"])
    print("                        by phone: %d" % hit["phone"])
    print("                         by name: %d" % hit["name"])
    print("  NEW rows to be created        : %d" % hit["new"])
    print("\n  roster currently holds %d people" % len(roster))


def upload():
    people = _load()
    ep, tok = os.environ.get("AKAV_ENDPOINT"), os.environ.get("AKAV_TOKEN")
    if not (ep and tok):
        sys.exit("set AKAV_ENDPOINT and AKAV_TOKEN first")

    # --resume skips people already on the Sheet. Apps Script writes are
    # slow (~50 rows/min), so re-sending 3,800 rows that already landed
    # costs an hour for nothing. Upserts stay idempotent either way; this
    # only avoids the redundant work after an interrupted run.
    if "--resume" in sys.argv:
        from .normalize import norm_email, norm_name, norm_phone
        roster = uploader.fetch_roster(ep, tok) or []
        have_e = {norm_email(r.get("email")) for r in roster if r.get("email")}
        have_p = {norm_phone(r.get("phoneDigits")) for r in roster if r.get("phoneDigits")}
        have_n = {norm_name(r.get("name")) for r in roster
                  if r.get("name") and " " in norm_name(r.get("name"))}
        todo = []
        for p in people:
            n = norm_name(p["name"])
            on_sheet = (norm_email(p["email"]) in have_e
                        or (norm_phone(p["phoneDigits"]) in have_p
                            and norm_phone(p["phoneDigits"]))
                        or (" " in n and n in have_n))
            if not on_sheet:
                todo.append(p)
        print("resume: %d of %d already on the Sheet, sending %d"
              % (len(people) - len(todo), len(people), len(todo)))
        people = todo
        if not people:
            print("nothing left to send")
            return
    print("about to write %d people to:\n  %s\n" % (len(people), ep))
    if "--yes" not in sys.argv:
        if input("type 'yes' to continue: ").strip().lower() != "yes":
            sys.exit("aborted")
    receipt = uploader.upload_contacts(people, ep, tok)
    path = os.path.join(OUT, "roster-receipt.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=1)
    print("\nreceipt: %s" % path)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    {"build": build, "preview": preview, "upload": upload}.get(cmd, build)()
