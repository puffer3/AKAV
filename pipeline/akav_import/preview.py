"""Terminal preview of a parsed batch before upload."""

from .normalize import norm_email, norm_name, norm_phone


def _table(headers, rows):
    widths = [len(h) for h in headers]
    srows = []
    for r in rows:
        sr = [str(c) if c is not None else "" for c in r]
        srows.append(sr)
        for i, c in enumerate(sr):
            widths[i] = max(widths[i], len(c))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "-" * len(line)]
    for sr in srows:
        out.append("  ".join(sr[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(out)


def match_person(person, roster):
    """Mirror of the Apps Script findPersonRow: email → phone → name."""
    if not roster:
        return "n/a (offline)"
    e = norm_email(person.get("email"))
    if e:
        for r in roster:
            if r.get("email") == e:
                return "EMAIL -> row %s" % r["row"]
    p = norm_phone(person.get("phoneDigits"))
    if p:
        for r in roster:
            if r.get("phoneDigits") == p:
                return "PHONE -> row %s" % r["row"]
    n = norm_name(person.get("name"))
    if n:
        for r in roster:
            if norm_name(r.get("name")) == n:
                return "NAME? -> row %s" % r["row"]
    return "NEW"


def render(batch, roster=None):
    show = batch["show"]
    lines = []
    lines.append("=" * 72)
    lines.append("SHOW    %s   (id: %s)" % (show["showLabel"], show["showId"]))
    meta = "  ".join(
        "%s: %s" % (k, show[k]) for k in ("venue", "client", "po", "pm")
        if show.get(k))
    if meta:
        lines.append("        " + meta)
    lines.append("        dates %s -> %s   |   %d work records   |   %d people"
                 % (show.get("firstDate", "?"), show.get("lastDate", "?"),
                    len(batch["records"]), len(batch["people"])))
    lines.append("        source file: %s" % batch["sourceFile"])
    lines.append("=" * 72)

    for i in batch.get("info", []):
        lines.append("  . %s" % i)
    lines.append("")

    rows = []
    for p in batch["people"]:
        rate = ""
        if p.get("rateMin") is not None:
            rate = ("%g" % p["rateMin"] if p["rateMin"] == p["rateMax"]
                    else "%g-%g" % (p["rateMin"], p["rateMax"]))
        # Crew Status "$ due" is the actual amount paid (incl. OT
        # adjustments) — prefer it over the day-rate sum.
        if p.get("total") is not None:
            total = "%g*" % p["total"]
        elif p.get("rateSum") is not None:
            total = "%g" % p["rateSum"]
        else:
            total = ""
        rows.append([
            p["name"], p.get("email", ""), p.get("phoneDigits", ""),
            p.get("days", 0), ", ".join(p.get("positions", [])[:3]),
            rate, total, p.get("grade", ""),
            match_person(p, roster),
        ])
    lines.append(_table(
        ["NAME", "EMAIL", "PHONE", "DAYS", "POSITIONS", "RATE", "TOTAL",
         "GRADE", "MATCH"], rows))
    lines.append("  (* = total from Crew Status '$ due' [actual paid]; "
                 "otherwise sum of day rates)")
    lines.append("")

    if batch.get("gradeDetections"):
        lines.append("GRADE COLUMNS DETECTED — confirm these really are grades:")
        for d in batch["gradeDetections"]:
            lines.append("  ! sheet %r column %s: %d grades, samples %s"
                         % (d["sheet"], d["column"], d["count"],
                            ", ".join(d["samples"])))
        lines.append("")

    warns = batch.get("warnings", [])
    if warns:
        lines.append("!" * 72)
        lines.append("WARNINGS (%d):" % len(warns))
        for w in warns:
            lines.append("  ! %s" % w)
        lines.append("!" * 72)
    else:
        lines.append("no warnings")

    new_count = 0
    if roster:
        new_count = sum(
            1 for p in batch["people"] if match_person(p, roster) == "NEW")
        lines.append("")
        lines.append("roster: %d existing rows; %d people would be NEW rows"
                     % (len(roster), new_count))
    return "\n".join(lines)
