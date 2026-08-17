"""Corpus regression tests -- run against the real source files.

These pin the numbers the build established. Their job is to fail loudly when
a parser change silently drops data: a qualifier that eats a whole job title,
a scrubber that blanks valid postcodes, a column detector that misses sparse
columns. Every one of those happened, and none of them raised an error.

Skipped automatically when the private source tree isn't present, so the unit
tests still run on a machine without the client's data.

Run:  python3 -m unittest discover -s pipeline/tests -v
"""

import csv
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from akav_import import config, parse_sow, parse_vcf, parse_vendors  # noqa: E402
from akav_import import jobs as jobsmod  # noqa: E402
from akav_import import parse_jobtab  # noqa: E402
from akav_import.roles import RoleResolver  # noqa: E402

HAVE_SOURCE = os.path.isdir(config.rollies_dir())
SPEC = config.source_path("rolly_spec.csv")

skip = unittest.skipUnless(
    HAVE_SOURCE, "source files not present at %s" % config.source_dir())


def load_spec():
    with open(SPEC, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@skip
class TestContracts(unittest.TestCase):
    """Signed SOWs -- the only trustworthy day-rate source."""

    @classmethod
    def setUpClass(cls):
        cls.resolver = RoleResolver()
        cls.days, cls.skipped, cls.problems = parse_sow.parse_folder(
            config.contracts_dir(), cls.resolver)

    def test_day_row_count(self):
        self.assertEqual(len(self.days), 220)

    def test_people_count(self):
        self.assertEqual(len({d["person"] for d in self.days}), 87)

    def test_every_position_maps(self):
        """Contracts speak in full titles, so 100% is the standard here.
        Anything unmapped means the vocabulary lost a title."""
        self.assertEqual(self.resolver.unmapped_report(), [])

    def test_no_parse_problems(self):
        self.assertEqual(self.problems, [])

    def test_templates_skipped(self):
        self.assertEqual(len(self.skipped), 14)

    def test_half_days_never_reach_a_rate(self):
        """Henry's rule: half days are excluded from every rate statistic."""
        summary = parse_sow.summarize_rates(self.days)
        half_only = [s for s in summary
                     if s["half_days"] and not s["rated_days"]]
        for s in half_only:
            self.assertIsNone(s["typical_day_rate"])
        self.assertTrue(any(s["half_days"] for s in summary),
                        "no half days in corpus -- test is vacuous")

    def test_rates_within_plausible_band(self):
        rates = [d["rate"] for d in self.days if d["rate"] is not None]
        self.assertTrue(rates)
        self.assertGreaterEqual(min(rates), 150)
        self.assertLessEqual(max(rates), 800)


@skip
class TestVendorExport(unittest.TestCase):
    """QuickBooks vendor list -- addresses in, tax IDs never."""

    @classmethod
    def setUpClass(cls):
        cls.people, cls.stats = parse_vendors.parse(config.vendor_csv())
        cls.blob = json.dumps(cls.people)

    def test_people_count(self):
        self.assertEqual(len(self.people), 1881)

    def test_addresses_present(self):
        with_city = sum(1 for p in self.people if p["home_base"])
        self.assertGreaterEqual(with_city, 1700)

    def test_zip_plus_four_preserved(self):
        """Regression: the SSN scrubber destroyed 54 nine-digit postcodes."""
        nine = [p for p in self.people if len(p["zip"]) == 9]
        self.assertGreaterEqual(len(nine), 40)

    def test_tax_id_column_dropped(self):
        self.assertIn("Tax ID", self.stats["dropped_columns"])
        self.assertIn("Account #", self.stats["dropped_columns"])

    def test_no_tax_field_in_output(self):
        for p in self.people[:50]:
            for key in p:
                self.assertFalse(config.is_forbidden_field(key))

    def test_no_ssn_shaped_value_escapes(self):
        """The zip field legitimately holds nine digits; nothing else may."""
        without_zip = json.dumps(
            [{k: v for k, v in p.items() if k != "zip"} for p in self.people])
        self.assertEqual(re.findall(r'"\s*\d{3}-?\d{2}-?\d{4}\s*"', without_zip), [])


@skip
class TestContactExport(unittest.TestCase):
    """The phone export -- do-not-hire flags only."""

    @classmethod
    def setUpClass(cls):
        cls.rows = parse_vcf.extract_flagged(config.contacts_vcf())

    def test_do_not_hire_count(self):
        self.assertEqual(len(self.rows), 244)

    def test_all_flagged(self):
        self.assertTrue(all(r["do_not_hire"] for r in self.rows))

    def test_most_have_a_phone(self):
        self.assertGreaterEqual(sum(1 for r in self.rows if r["phones"]), 220)

    def test_commentary_split_from_name(self):
        noted = [r for r in self.rows if r["name_note"]]
        self.assertTrue(noted, "no name/commentary splits found")
        for r in noted:
            self.assertTrue(r["needs_review"])


@skip
class TestJobCalendar(unittest.TestCase):
    """War Room -- the canonical job registry."""

    @classmethod
    def setUpClass(cls):
        cls.jobs = jobsmod.load(jobsmod.find_war_room(config.source_dir()))

    def test_job_count(self):
        self.assertEqual(len(self.jobs), 421)

    def test_book_codes_present(self):
        coded = [j for j in self.jobs if jobsmod.is_book_code(j["book"])]
        self.assertGreaterEqual(len(coded), 380)

    def test_concurrency_is_high(self):
        """Jobs overlap heavily, which is why date alone can never identify
        one. If this ever drops, the matcher's confidence gate can relax."""
        overlapping = 0
        for i, a in enumerate(self.jobs):
            if any(a["start"] <= b["end"] and b["start"] <= a["end"]
                   for b in self.jobs[i + 1:]):
                overlapping += 1
        self.assertGreater(overlapping / len(self.jobs), 0.5)

    def test_audit_flags_duplicates_and_gaps(self):
        rows = jobsmod.audit(self.jobs, {})
        kinds = {f for r in rows for f in r["discrepancy"].split(", ") if f}
        self.assertIn("duplicate_book", kinds)
        self.assertIn("no_book_code", kinds)


@skip
class TestEmbeddedJobTabs(unittest.TestCase):
    """Job workbooks living as tabs inside the rolodex files."""

    @classmethod
    def setUpClass(cls):
        import openpyxl
        cls.resolver = RoleResolver()
        cls.jobs, cls.records = [], []
        for s in load_spec():
            if s["kind"] != "show":
                continue
            wb = openpyxl.load_workbook(
                os.path.join(config.rollies_dir(), s["workbook"]),
                read_only=True, data_only=True)
            job, recs = parse_jobtab.parse_tab(wb[s["tab"]], s["tab"], cls.resolver)
            wb.close()
            if job:
                cls.jobs.append(job)
                cls.records.extend(recs)

    def test_counts(self):
        self.assertEqual(len(self.jobs), 20)
        self.assertEqual(len(self.records), 505)

    def test_showphaze_era_labelled(self):
        sp = [j for j in self.jobs if j["era"] == "showphaze"]
        self.assertEqual(len(sp), 19)
        for j in sp:
            self.assertTrue(j["label"].startswith(parse_jobtab.SHOWPHAZE_PREFIX))

    def test_no_rates_or_grades_read(self):
        """AK did not run payroll then and no grades existed. The money on
        those sheets must never reach a rate field."""
        for j in self.jobs:
            self.assertFalse(j["rates_read"])
            self.assertFalse(j["grades_read"])
        for r in self.records:
            for banned in ("rate", "grade", "due", "total"):
                self.assertNotIn(banned, r)

    def test_positions_mostly_map(self):
        typed = [r for r in self.records if r["position_raw"]]
        mapped = [r for r in typed if r["positions"]]
        self.assertGreaterEqual(len(mapped) / len(typed), 0.90)


@skip
class TestRollySpec(unittest.TestCase):
    """The per-tab mapping spec -- the fragile heart."""

    @classmethod
    def setUpClass(cls):
        cls.spec = load_spec()

    def test_tab_totals(self):
        kinds = {}
        for s in self.spec:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        self.assertEqual(len(self.spec), 234)
        self.assertEqual(kinds.get("people"), 181)
        self.assertEqual(kinds.get("show"), 20)
        self.assertEqual(kinds.get("non_crew"), 1)

    def test_people_tabs_have_a_name_column(self):
        missing = [s for s in self.spec
                   if s["kind"] == "people" and s["name_col"] == ""]
        self.assertLessEqual(len(missing), 3, [s["tab"] for s in missing])

    def test_grade_columns_detected(self):
        """Regression: density-weighting hid 13 real grade columns, including
        one with 163 grades in it. Sparse columns are judged on purity."""
        graded = [s for s in self.spec if s["grade_col"] != ""]
        self.assertGreaterEqual(len(graded), 45)

    def test_markets_are_canonical(self):
        markets = {s["market"] for s in self.spec if s["market"]}
        lowered = [m.lower() for m in markets]
        self.assertEqual(len(lowered), len(set(lowered)),
                         "markets differing only by case")
        self.assertIn("New Mexico", markets)
        self.assertNotIn("Mexico", markets)

    def test_shortlist_tabs_present(self):
        sl = [s for s in self.spec if s["status"] == "Short List"]
        self.assertGreaterEqual(len(sl), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
