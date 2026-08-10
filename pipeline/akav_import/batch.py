"""Build a normalized import batch from one workbook."""

import datetime
import os

import openpyxl

from . import grades as grades_mod
from . import parse_status, parse_tidy, parse_wb
from .models import ShowMeta, to_dict
from .normalize import slugify
from .workbook import (
    ROLE_STATUS, ROLE_TIDY, ROLE_WB, classify_sheets, pick_person_day_source,
)


def build_batch(xlsx_path, grades_csv=None, show_label=None, exclude=None):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=False)
    roles, class_log = classify_sheets(wb)
    warnings, info = [], list(class_log)

    source_sheet, why = pick_person_day_source(roles)
    if source_sheet is None:
        raise SystemExit(
            "ERROR: %s\nSheets seen:\n  %s" % (why, "\n  ".join(class_log)))
    info.append("person-day source: %r (%s)" % (source_sheet, why))

    # Show metadata comes from the WB sheet when there is one
    wb_sheets = [s for s, r in roles.items() if r == ROLE_WB]
    fallback_label = os.path.splitext(os.path.basename(xlsx_path))[0]
    if wb_sheets:
        show = parse_wb.extract_show_meta(wb[wb_sheets[0]], fallback_label)
    else:
        show = ShowMeta(showLabel=fallback_label,
                        showId=slugify(fallback_label))
    if show_label:
        show.showLabel = show_label
        show.showId = slugify(show_label)

    # Person-day records. The tidy sheet is primary (it has rates), but it
    # can cover only part of the show (AnaheimPRG's Scratch Paper is BO-only
    # while the GS crew lives in 'Workbook'), so WB_GRID records are unioned
    # in as well; recordHash dedupe drops the overlap, tidy wins.
    ws = wb[source_sheet]
    if roles[source_sheet] == ROLE_TIDY:
        records, colmap = parse_tidy.parse(ws)
    else:
        records, colmap = parse_wb.parse(ws)
    info.append("column roles in %r: %s" % (source_sheet, colmap))

    if roles[source_sheet] == ROLE_TIDY:
        for sname in wb_sheets:
            try:
                extra, wb_colmap = parse_wb.parse(wb[sname])
            except ValueError as e:
                warnings.append(str(e))
                continue
            records.extend(extra)
            info.append("unioned %d records from %r (column roles: %s)"
                        % (len(extra), sname, wb_colmap))

    # Crew Status rows + grade detection across status AND tidy sheets
    status_rows, sheet_grades, detections = [], {}, []
    for sname, role in roles.items():
        if role == ROLE_STATUS:
            try:
                srows, sinfo = parse_status.parse(wb[sname])
                status_rows.extend(srows)
                g, det = grades_mod.detect(
                    wb[sname], sinfo["name_col"], sinfo["header_idx"],
                    sinfo["data_start"])
                sheet_grades.update(g)
                detections.extend(det)
            except ValueError as e:
                warnings.append(str(e))
        elif role == ROLE_TIDY and "name" in colmap and sname == source_sheet:
            g, det = grades_mod.detect(wb[sname], colmap["name"], None, 1)
            sheet_grades.update(g)
            detections.extend(det)

    # Manual grades side input
    manual_by_name, manual_by_email = {}, {}
    if grades_csv:
        manual_by_name, manual_by_email, bad = grades_mod.load_manual(grades_csv)
        for row in bad:
            warnings.append("grades.csv: could not parse row %r" % (row,))

    from .identity import resolve
    people, id_warnings = resolve(
        records, status_rows, sheet_grades, manual_by_name, manual_by_email)
    warnings.extend(id_warnings)

    # Drop duplicate person-day records (tidy + WB overlap). recordHash is
    # (personKey|date|position|callStart); tidy records come first and win.
    seen_hashes = set()
    deduped = []
    for r in records:
        if r.recordHash in seen_hashes:
            continue
        seen_hashes.add(r.recordHash)
        deduped.append(r)
    if len(deduped) != len(records):
        info.append("deduped %d overlapping tidy/WB records"
                    % (len(records) - len(deduped)))
    records = deduped

    # Manual grade names that matched nobody
    matched_names = {p.name.lower() for p in people}
    from .normalize import norm_name
    matched_norm = {norm_name(p.name) for p in people}
    matched_emails = {p.email for p in people if p.email}
    for nm in manual_by_name:
        if nm not in matched_norm:
            warnings.append("grades side-input name %r matched nobody" % nm)
    for em in manual_by_email:
        if em not in matched_emails:
            warnings.append("grades side-input email %r matched nobody" % em)

    # Drop excluded pseudo-people ('AK Travel' reimbursement rows, etc.)
    if exclude:
        from .normalize import norm_name as _nn
        excl = {_nn(x) for x in exclude}
        before = len(people)
        keep_keys = {p.personKey for p in people if _nn(p.name) not in excl}
        people = [p for p in people if p.personKey in keep_keys]
        records = [r for r in records if r.personKey in keep_keys]
        if len(people) != before:
            info.append("excluded %d people by --exclude" % (before - len(people)))

    # Show date range from records
    dates = sorted({r.date for r in records if r.date})
    if dates:
        show.firstDate, show.lastDate = dates[0], dates[-1]

    # Sanity asserts — catch template drift before it hits the sheet
    if not records:
        raise SystemExit("ERROR: 0 work records parsed from %r" % source_sheet)
    if dates:
        d0 = datetime.date.fromisoformat(dates[0])
        d1 = datetime.date.fromisoformat(dates[-1])
        if (d1 - d0).days > 366:
            warnings.append(
                "date range spans more than a year (%s → %s) — check the "
                "date column mapping" % (dates[0], dates[-1]))

    return {
        "version": 1,
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "sourceFile": os.path.basename(xlsx_path),
        "show": to_dict(show),
        "records": [to_dict(r) for r in records],
        "people": [to_dict(p) for p in people],
        "gradeDetections": detections,
        "warnings": warnings,
        "info": info,
    }
