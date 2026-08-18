"""Unit tests for the import pipeline -- no source data required.

Every test here pins a bug that actually happened during the build. They all
shared one shape: the code SILENTLY DROPPED OR MANGLED DATA rather than
failing, so nothing surfaced until someone eyeballed a count and thought "that
number looks wrong". These tests turn that eyeball into a check.

Run:  python3 -m unittest discover -s pipeline/tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from akav_import import config, flags, names, notes, parse_vendors  # noqa: E402
from akav_import import jobs as jobsmod  # noqa: E402
from akav_import.roles import (  # noqa: E402
    CANONICAL, RoleResolver, duplicate_aliases, split_claimed_vs_worked,
    split_qualifiers,
)


class TestRoleAliases(unittest.TestCase):
    def setUp(self):
        self.r = RoleResolver()

    def test_no_alias_claimed_by_two_titles(self):
        """An alias maps one way only; a collision silently loses a title.

        Regression: 'records' was claimed by both Playback Operator and
        Record Technician, so whichever loaded last won and the other was
        unreachable.
        """
        self.assertEqual(duplicate_aliases(), {})

    def test_job_codes_survive_slot_stripping(self):
        """Regression: the slot-number stripper turned 'A1' into 'A'.

        Job workbooks number slots ('Stagehand - 1'), but A1/A2/V1/L2 ARE the
        job names. Stripping trailing digits unmapped every one of them.
        """
        self.assertEqual(self.r.resolve_cell("A1"), ["Audio Engineer (A1)"])
        self.assertEqual(self.r.resolve_cell("A2"), ["Audio Assistant (A2)"])
        self.assertEqual(self.r.resolve_cell("V1"), ["Video Engineer (V1)"])
        self.assertEqual(self.r.resolve_cell("L2"), ["Lighting Assistant (L2)"])

    def test_slot_numbers_still_stripped(self):
        self.assertEqual(self.r.resolve_cell("Stagehand - 1"), ["Stagehand"])
        self.assertEqual(self.r.resolve_cell("Monitor Tech 2"), ["Monitor Tech"])
        self.assertEqual(self.r.resolve_cell("A1 1"), ["Audio Engineer (A1)"])

    def test_qualifier_never_consumes_the_whole_title(self):
        """Regression: bare 'Floater' matched the 'float' qualifier and
        reduced to '', dropping 255 real occurrences to zero."""
        self.assertEqual(self.r.resolve_cell("Floater"), ["Floater"])
        self.assertEqual(self.r.resolve_cell("Strike"), ["Strike"])
        self.assertEqual(self.r.resolve_cell("Breakout"), ["Breakout Operator"])

    def test_qualifier_still_applies_when_something_remains(self):
        base, quals = split_qualifiers("AV Tech - Half")
        self.assertEqual(base, "AV Tech")
        self.assertIn("half", quals)
        self.assertEqual(self.r.resolve_cell("GAV - Float"), ["General AV"])

    def test_parenthetical_context_is_stripped(self):
        """'Stagehand (GS)' must not miss the map as 'stagehand (gs'."""
        self.assertEqual(self.r.resolve_cell("Stagehand (GS)"), ["Stagehand"])

    def test_department_role_compounds(self):
        self.assertEqual(self.r.resolve_cell("A/V - AV Technician 1"), ["AV Tech"])
        self.assertEqual(self.r.resolve_cell("V3 - Utility Tech 1"), ["Utility Tech"])
        self.assertEqual(self.r.resolve_cell("Stagehand -CREW CHIEF"), ["Crew Lead"])

    def test_records_is_its_own_job(self):
        self.assertEqual(self.r.resolve_cell("records"), ["Record Technician"])
        self.assertEqual(self.r.resolve_cell("playback"), ["Playback Operator"])
        self.assertEqual(
            set(self.r.resolve_cell("Plackback / Records")),
            {"Playback Operator", "Record Technician"})

    def test_every_canonical_title_resolves_to_itself(self):
        """Feeding a canonical title back in must return that title.

        Four are knowingly lossy and excluded: their paren-stripped form is a
        real alias of a *different, more generic* title.
        """
        known_lossy = {"Audio Assistant (A2)", "Audio Assist (A3)",
                       "Lighting Assist (L3)", "Loader / Pusher"}
        for title in CANONICAL:
            if title in known_lossy:
                continue
            with self.subTest(title=title):
                self.assertEqual(self.r.resolve_cell(title), [title])

    def test_hyphenated_job_codes_map(self):
        """Regression (review #2): the slot stripper's second alternative ate
        'A-1' -> 'A'. These hyphenated forms are explicit aliases, so an exact
        map hit must be tried BEFORE any qualifier parsing."""
        for raw, want in (("A-1", "Audio Engineer (A1)"),
                          ("A-2", "Audio Assistant (A2)"),
                          ("V-1", "Video Engineer (V1)"),
                          ("V-2", "Video Assistant (V2)"),
                          ("L-1", "Lighting Engineer (L1)"),
                          ("L-2", "Lighting Assistant (L2)")):
            with self.subTest(raw=raw):
                self.assertEqual(self.r.resolve_cell(raw), [want])
        self.assertEqual(self.r.unmapped_report(), [])

    def test_unmapped_is_recorded_not_guessed(self):
        r = RoleResolver()
        r.resolve_cell("Millumin Op")
        self.assertTrue(any(k for k, _ in r.unmapped_report()))


class TestClaimedVsWorked(unittest.TestCase):
    def test_worked_promotes_out_of_claimed(self):
        """Claiming 'V1' and having worked 'Video Engineer (V1)' is proof;
        comparing raw strings would wrongly call it unconfirmed."""
        worked, claimed = split_claimed_vs_worked(
            ["V1, PJ, Cam Op"], ["Video Engineer (V1)", "Camera Operator"])
        self.assertIn("Video Engineer (V1)", worked)
        self.assertNotIn("Video Engineer (V1)", claimed)
        self.assertNotIn("Camera Operator", claimed)
        self.assertIn("Projectionist", claimed)

    def test_unmapped_claims_survive_as_raw_text(self):
        """A skill list is resolved TOKEN BY TOKEN. Resolving the whole cell
        swallowed the tokens that didn't map -- exactly the niche skills
        someone would search for."""
        _, claimed = split_claimed_vs_worked(
            ["V1, Teleprompt, VSWCH, Millumin"], [])
        for niche in ("Teleprompt", "VSWCH", "Millumin"):
            self.assertIn(niche, claimed)


class TestNames(unittest.TestCase):
    def test_aka_splitting(self):
        cases = {
            "Kiki (Katarina Lindqvist)": ("Katarina Lindqvist", "Kiki"),
            "Bee (Bethany) Sample": ("Bethany Sample", "Bee Sample"),
            'pat sample "jiggy"': ("pat sample", "jiggy"),
            "Devendra (Robin) Sample": ("Robin Sample", "Devendra Sample"),
        }
        for raw, (want_name, want_aka) in cases.items():
            with self.subTest(raw=raw):
                name, akas, _ = names.split_aka(raw)
                self.assertEqual(name, want_name)
                self.assertIn(want_aka, akas)

    def test_apostrophe_surnames_survive(self):
        """Regression (review #1): treating ' as a nickname delimiter tore
        "Sean O'Brien D'Amato" into ('Sean O Amato', ["Brien D"]). The mangled
        name then failed to match itself across sources, because norm_name()
        is the identity key."""
        for raw in ("Sean O'Brien D'Amato", "D'Angelo O'Neal", "Dee O'Hara",
                    "Jordan Smythe-Jones"):
            with self.subTest(raw=raw):
                name, akas, _ = names.split_aka(raw)
                self.assertEqual(name, raw)
                self.assertEqual(akas, [])

    def test_single_quoted_nickname_still_works(self):
        name, akas, _ = names.split_aka("Brooke Smith 'Creature'")
        self.assertEqual(name, "Brooke Smith")
        self.assertIn("Creature", akas)

    def test_plain_name_untouched(self):
        name, akas, ambiguous = names.split_aka("Ana Lopez Reyes")
        self.assertEqual(name, "Ana Lopez Reyes")
        self.assertEqual(akas, [])
        self.assertFalse(ambiguous)


class TestAddresses(unittest.TestCase):
    def test_city_parsed_from_flat_address(self):
        """Regression: matching forwards produced street fragments as cities
        ('Walton Creek D', 'Breeze Ter Aus')."""
        cases = {
            "2785 Walton Creek Dr Colorado Springs CO 80922 USA": ("Colorado Springs", "CO"),
            "3202 Breeze Ter Austin TX 78722 US": ("Austin", "TX"),
            "P.O. BOX 15372 Middle River MD 21220 USA": ("Middle River", "MD"),
            "1635 Bryson Cove Apt 3108 Thompson's Station TN 37179 USA": ("Thompson's Station", "TN"),
            "9615 Rockaway Beach Blvd # 2 96-15 Far Rockaway NY 11693 USA": ("Far Rockaway", "NY"),
            "2453 Yates Ave Grove City OH 431231844 USA": ("Grove City", "OH"),
        }
        for raw, (city, state) in cases.items():
            with self.subTest(raw=raw[:30]):
                got = parse_vendors.split_address(raw)
                self.assertEqual(got["city"], city)
                self.assertEqual(got["state"], state)

    def test_st_prefixed_cities_keep_their_st(self):
        """Regression: 'St.' reduced to the street suffix 'st', so the
        backwards walk stopped and 'St. Louis' became 'Louis'.

        Counter-cases matter more: 28 corpus addresses write the STREET
        suffix with a period ('Gloucester Gate St. Las Vegas'), so blanket
        'St.-means-Saint' logic manufactured cities like 'St. Las Vegas'.
        Only a known Saint city may claim the token."""
        cases = {
            "1420 Market St St. Louis MO 63103 USA": ("St. Louis", "MO"),
            "456 Oak Ave St. Paul MN 55101 USA": ("St. Paul", "MN"),
            "200 Central Ave St. Petersburg FL 33701 US": ("St. Petersburg", "FL"),
            "3567 Gloucester Gate St. Las Vegas NV 89122 USA": ("Las Vegas", "NV"),
            "8964 Sperry St. Orlando FL 32827 US": ("Orlando", "FL"),
            "123 Main St Louisville KY 40203": ("Louisville", "KY"),
        }
        for raw, (city, state) in cases.items():
            with self.subTest(raw=raw[:30]):
                got = parse_vendors.split_address(raw)
                self.assertEqual(got["city"], city)
                self.assertEqual(got["state"], state)


class TestSensitiveFields(unittest.TestCase):
    def test_forbidden_columns_recognised(self):
        for h in ("Tax ID", "SSN", "Account #", "Routing Number", "EIN"):
            with self.subTest(h=h):
                self.assertTrue(config.is_forbidden_field(h))

    def test_ordinary_columns_allowed(self):
        for h in ("Full name", "Email", "Billing address", "Phone numbers"):
            with self.subTest(h=h):
                self.assertFalse(config.is_forbidden_field(h))

    def test_scrub_row_drops_sensitive(self):
        clean, dropped = config.scrub_row(
            {"Full name": "A Person", "Tax ID": "123456789", "Email": "a@b.c"})
        self.assertNotIn("Tax ID", clean)
        self.assertIn("Tax ID", dropped)
        self.assertEqual(clean["Email"], "a@b.c")

    def test_zip_plus_four_is_not_an_ssn(self):
        """Regression: the SSN scrubber blanked 54 valid ZIP+4 codes, which
        have the same nine-digit shape. Detection is fine; applying it to a
        postcode field is the bug."""
        self.assertTrue(config.looks_like_ssn("782104340"))
        got = parse_vendors.split_address("1547 Hicks Ave San Antonio TX 782104340 USA")
        self.assertEqual(got["zip"], "782104340")


class TestFlags(unittest.TestCase):
    def test_colour_alone_never_blocks(self):
        """A row can be dark red because the person called out sick. The NOTE
        decides the flag; colour is only evidence."""
        r = flags.classify("PINKEYE - called out day 2", "FF980000")
        self.assertEqual(r["blocking"], [])
        self.assertIn("called_out", r["flags"])
        self.assertTrue(r["needs_review"])

    def test_explicit_dnh_blocks(self):
        r = flags.classify("DNH - threatened me", "FF980000")
        self.assertEqual(r["blocking"], ["do_not_hire"])

    def test_unexplained_colour_goes_to_review(self):
        r = flags.classify("", "FF980000")
        self.assertEqual(r["blocking"], [])
        self.assertTrue(r["needs_review"])

    def test_nah_is_advisory_not_blocking(self):
        """Regression (review #8): 'nah' sat in do_not_hire, the only BLOCKING
        flag, and conflicts.AUTO_RESOLVED pre-answered it — so "nah, he's solid
        now" permanently barred someone and the client never saw the question."""
        for note in ("nah, hes solid now", "nah", "nah - moved to Denver"):
            with self.subTest(note=note):
                r = flags.classify(note)
                self.assertEqual(r["blocking"], [])
                self.assertIn("negative_remark", r["flags"])
        self.assertEqual(flags.classify("DNH - threatened me")["blocking"],
                         ["do_not_hire"])

    def test_cell_ref(self):
        self.assertEqual(flags.cell_ref(0, 0), "A1")
        self.assertEqual(flags.cell_ref(1, 41), "B42")
        self.assertEqual(flags.cell_ref(26, 0), "AA1")


class TestNotes(unittest.TestCase):
    LEGACY = ("[InfoSys Connect 2026] Confirm MP\n"
              "GAV; Stagehand; from a lead - do not rehire; Decent attitude")

    def test_legacy_mash_is_split_not_duplicated(self):
        """Regression: re-importing added the split observations ALONGSIDE the
        mashed original, so the card said everything twice."""
        incoming = (notes.make("GAV; Stagehand", workbook="Atlanta Rolly.xlsx", tab="ATL")
                    + notes.make("Decent attitude", job="Cisco Live 2026"))
        out = notes.merge(self.LEGACY, incoming)
        lines = out.split("\n")
        self.assertEqual(len(lines), len(set(lines)), "duplicate note lines")
        self.assertEqual(sum(1 for l in lines if "Decent attitude" in l), 1)

    def test_source_fills_in_on_existing_untagged_note(self):
        out = notes.merge(self.LEGACY,
                          notes.make("GAV", workbook="Atlanta Rolly.xlsx", tab="ATL"))
        self.assertIn("[Atlanta Rolly › ATL] GAV", out)

    def test_merge_is_idempotent(self):
        incoming = notes.make("solid on breakouts", workbook="Vegas Rolly.xlsx", tab="ALL")
        once = notes.merge("", incoming)
        self.assertEqual(notes.merge(once, incoming), once)

    def test_merge_never_drops_an_existing_note(self):
        out = notes.merge("something nobody re-imported", [])
        self.assertIn("something nobody re-imported", out)


class TestMarkets(unittest.TestCase):
    def test_new_prefixed_markets_survive(self):
        """Regression (review #3): market_for()'s cleanup regex stripped 'new',
        so a 'New Orleans' tab in Florida Rolly was filed under 'Florida' and
        every person on it got the wrong market."""
        from akav_import import survey
        for tab, want in (("New Orleans", "New Orleans"),
                          ("New York", "New York"),
                          ("New Haven", "New Haven")):
            with self.subTest(tab=tab):
                self.assertEqual(survey.market_for("Florida Rolly.xlsx", tab), want)

    def test_bookkeeping_words_still_stripped(self):
        from akav_import import survey
        self.assertEqual(survey.market_for("Atlanta Rolly.xlsx", "New Short List"),
                         "Atlanta")
        self.assertEqual(survey.market_for("Atlanta Rolly.xlsx", "Copy of ATL"),
                         "Atlanta")


class TestJobCodes(unittest.TestCase):
    def test_real_book_codes(self):
        for code in ("ATLOns21", "BaltAVFX1", "DayBS-1", "AustinCT", "BethesdaAVFX"):
            with self.subTest(code=code):
                self.assertTrue(jobsmod.is_book_code(code))

    def test_placeholders_are_not_codes(self):
        """The BOOK column also holds prose. Treating 'lo mebbe' as an
        identity made 25 unrelated jobs look like duplicates of each other."""
        for junk in ("lo mebbe", "ak only", "fuk this", "3 a2s", ""):
            with self.subTest(junk=junk):
                self.assertFalse(jobsmod.is_book_code(junk))

    def test_joined_and_referenced_codes(self):
        self.assertTrue(jobsmod.is_book_code("ATLOns14 & ATLOns15"))
        self.assertTrue(jobsmod.is_book_code("ATLCT1 (CTNY001226)"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
