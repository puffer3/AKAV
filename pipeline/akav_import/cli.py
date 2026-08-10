"""CLI: python -m akav_import <parse|preview|upload> ...

Endpoint/token come from --endpoint/--token or the AKAV_ENDPOINT /
AKAV_TOKEN environment variables. The production URL is never baked in.
"""

import argparse
import json
import os
import sys

from . import batch as batch_mod
from . import preview as preview_mod
from . import uploader
from .normalize import slugify

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "out")


def _endpoint(args):
    ep = args.endpoint or os.environ.get("AKAV_ENDPOINT", "")
    tok = args.token or os.environ.get("AKAV_TOKEN", "")
    return ep, tok


def cmd_parse(args):
    b = batch_mod.build_batch(args.xlsx, grades_csv=args.grades,
                              show_label=args.show_label,
                              exclude=args.exclude)
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(
        args.out_dir, "%s-batch.json" % slugify(b["show"]["showLabel"]))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(b, f, indent=1, ensure_ascii=False)
    print(preview_mod.render(b))
    print("\nbatch written: %s" % out_path)
    print("next: python -m akav_import preview %s   (add --endpoint/--token "
          "or set AKAV_ENDPOINT/AKAV_TOKEN to match against the live roster)"
          % out_path)
    return 0


def _load_batch(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_preview(args):
    b = _load_batch(args.batch)
    roster = None
    ep, tok = _endpoint(args)
    if ep and tok:
        print("fetching roster from endpoint...")
        roster = uploader.fetch_roster(ep, tok)
    elif ep or tok:
        print("NOTE: need BOTH endpoint and token for roster matching — "
              "running offline.")
    print(preview_mod.render(b, roster))
    return 0


def cmd_upload(args):
    ep, tok = _endpoint(args)
    if not ep or not tok:
        print("ERROR: upload needs --endpoint and --token "
              "(or AKAV_ENDPOINT / AKAV_TOKEN).", file=sys.stderr)
        return 2
    b = _load_batch(args.batch)
    roster = uploader.fetch_roster(ep, tok)
    print(preview_mod.render(b, roster))

    n_new = sum(1 for p in b["people"]
                if preview_mod.match_person(p, roster) == "NEW")
    print("\nUPLOAD: %d work records, %d people (%d new rows) -> %s"
          % (len(b["records"]), len(b["people"]), n_new, ep))
    if not args.yes:
        answer = input("type 'yes' to upload: ").strip().lower()
        if answer != "yes":
            print("aborted.")
            return 1

    receipt = uploader.upload(b, ep, tok, chunk_size=args.chunk_size)
    os.makedirs(OUT_DIR, exist_ok=True)
    rec_path = os.path.join(
        OUT_DIR, "%s-receipt.json" % slugify(b["show"]["showLabel"]))
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1)
    print("done. receipt: %s" % rec_path)
    return 0


def cmd_rolly(args):
    import openpyxl

    from . import parse_rolly
    from .preview import _table, match_person

    wb = openpyxl.load_workbook(args.xlsx, data_only=True)
    contacts, info = parse_rolly.parse(
        wb, fallback_city=args.default_city, skip_sheets=args.skip_sheet)
    for i in info:
        print("  . %s" % i)

    cities = {}
    for c in contacts:
        cities[c["city"]] = cities.get(c["city"], 0) + 1
    print("\n%d unique contacts | cities: %s"
          % (len(contacts),
             ", ".join("%s: %d" % kv for kv in sorted(cities.items()))))

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "rolly-contacts.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(contacts, f, indent=1, ensure_ascii=False)
    print("full parse (incl. grades/notes, not uploaded): %s" % out_path)

    ep, tok = _endpoint(args)
    roster = None
    if ep and tok:
        print("\nfetching roster...")
        roster = uploader.fetch_roster(ep, tok)
    rows = [[c["name"], c["email"], c["phoneDigits"], c["city"],
             "explicit" if c["cityExplicit"] else "default",
             (c["noteText"][:34] + "…") if len(c["noteText"]) > 35
             else c["noteText"],
             match_person(c, roster)]
            for c in contacts]
    print(_table(["NAME", "EMAIL", "PHONE", "CITY", "SRC", "NOTES->",
                  "MATCH"], rows))

    if not ep or not tok:
        print("\n(no endpoint/token — parsed only; set AKAV_ENDPOINT/"
              "AKAV_TOKEN and re-run to upload)")
        return 0
    n_new = sum(1 for c in contacts if match_person(c, roster) == "NEW")
    print("\nUPLOAD: %d contacts (%d new rows; City fills only blank cells)"
          % (len(contacts), n_new))
    if not args.yes:
        if input("type 'yes' to upload: ").strip().lower() != "yes":
            print("aborted.")
            return 1
    receipt = uploader.upload_contacts(contacts, ep, tok)
    rec_path = os.path.join(OUT_DIR, "rolly-receipt.json")
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1)
    print("done. receipt: %s" % rec_path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="akav_import",
        description="Parse AKAV job workbooks and import them into the "
                    "master Google Sheet via the Apps Script endpoint.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse", help="parse a .xlsx workbook into a batch JSON")
    p.add_argument("xlsx")
    p.add_argument("--grades", help="manual grades CSV (name_or_email,grade)")
    p.add_argument("--show-label", help="override the detected show label")
    p.add_argument("--exclude", action="append", default=[],
                   help="drop a pseudo-person by name (repeatable), "
                        "e.g. --exclude 'AK Travel'")
    p.add_argument("--out-dir", default=OUT_DIR)
    p.set_defaults(fn=cmd_parse)

    p = sub.add_parser("preview", help="preview a batch (+roster match)")
    p.add_argument("batch")
    p.add_argument("--endpoint")
    p.add_argument("--token")
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("rolly", help="parse a crew rolodex and upsert "
                                     "contacts (city + notes) into the master")
    p.add_argument("xlsx")
    p.add_argument("--default-city",
                   help="city for sheets whose tab name has no region "
                        "(single-city rolly files)")
    p.add_argument("--skip-sheet", action="append", default=[],
                   help="sheet name to ignore entirely (repeatable)")
    p.add_argument("--endpoint")
    p.add_argument("--token")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_rolly)

    p = sub.add_parser("upload", help="upload a batch to the endpoint")
    p.add_argument("batch")
    p.add_argument("--endpoint")
    p.add_argument("--token")
    p.add_argument("--chunk-size", type=int, default=uploader.CHUNK_SIZE)
    p.add_argument("--yes", action="store_true",
                   help="skip the confirmation prompt")
    p.set_defaults(fn=cmd_upload)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
