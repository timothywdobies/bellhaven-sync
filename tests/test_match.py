"""Proves matching and rules on a saved copy of the real data, plus edge cases."""
import copy
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import config
import match

FIX = ROOT / "tests" / "fixtures"
SITE = json.loads((FIX / "site.json").read_text())
ACCOUNTS = json.loads((FIX / "accounts.json").read_text())
CONTACTS = json.loads((FIX / "contacts.json").read_text())


def tail(account_id):
    return account_id[-6:]


def find(rows, end):
    return next(r for r in rows if r["account_id"].endswith(end))


def run(accounts=None, site=None):
    return match.classify(site or SITE, accounts or ACCOUNTS, CONTACTS)


class RealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.props, cls.confirmed, cls.warnings = run()
        cls.by_type = {}
        for p in cls.props:
            cls.by_type.setdefault(p["type"], []).append(p)

    def test_counts_match_hand_review(self):
        self.assertEqual(Counter(p["type"] for p in self.props),
                         Counter(field_fix=10, duplicate=7, new=4, reparent=4, chow=3, missing=2))
        self.assertEqual(len(self.confirmed), 15)
        self.assertEqual(self.warnings, [])

    def test_every_location_is_accounted_for(self):
        slugs = [c["slug"] for c in self.confirmed]
        slugs += [p["slug"] for p in self.props if p["type"] in ("field_fix", "reparent", "new") or (p["type"] == "chow" and p["slug"])]
        self.assertEqual(sorted(slugs), sorted(s["slug"] for s in SITE))

    def test_name_traps_become_new_accounts(self):
        names = {p["site"]["name"] for p in self.by_type["new"]}
        self.assertEqual(names, {"Amberly Manor", "Bellhaven at Union Square",
                                 "Bellhaven of Batavia", "Bellhaven of Carlisle"})

    def test_chow_goes_to_marietta_and_tiffin_only(self):
        with_create = [p for p in self.by_type["chow"] if p["actions"][0]["op"] == "create_account"]
        self.assertEqual({tail(p["account_ids"][0]) for p in with_create}, {"CRBLFT", "WSXZZB"})
        for p in with_create:
            self.assertEqual(p["actions"][1]["fields"], {"chow_current_account": "$new"})

    def test_sandusky_links_to_existing_millstone_account(self):
        p = next(p for p in self.by_type["chow"] if "Sandusky" in p["title"])
        self.assertEqual(len(p["actions"]), 1)
        self.assertEqual(p["actions"][0]["fields"], {"chow_current_account": find(ACCOUNTS, "63BVK3")["account_id"]})

    def test_survivors(self):
        expected = {"LA9HTX": "AGG1S5", "KM4FHM": "AGG1S5", "SZLZHH": "YHN220", "15UA71": "2ZTRE6",
                    "LTSN24": "KB8932", "CAE2X4": "RXS09J", "ALS9GP": "RXS09J"}
        actual = {tail(p["account_ids"][0]): tail(p["actions"][-1]["fields"]["duplicate_of_account"])
                  for p in self.by_type["duplicate"]}
        self.assertEqual(actual, expected)

    def test_owosso_contact_moves_with_the_duplicate(self):
        p = next(p for p in self.by_type["duplicate"] if p["account_ids"][0].endswith("15UA71"))
        self.assertEqual(p["actions"][0]["op"], "patch_contact")
        self.assertEqual(p["actions"][0]["contact_name"], "Tricia Lindqvist")

    def test_rebrands_get_a_formerly_note_and_corrections_do_not(self):
        notes = [p for p in self.props if "Formerly" in json.dumps(p["actions"])]
        self.assertEqual({tail(p["account_ids"][0]) for p in notes}, {"C1Q0G7", "Z8DXEG", "DS8WQZ", "6D5VUF", "RXS09J"})

    def test_formatting_differences_create_no_street_fix(self):
        changes = [p for p in self.props if "billing_street" in json.dumps(p["actions"]) and p["type"] != "new" and p["actions"][0]["op"] == "patch_account"]
        self.assertEqual([tail(p["account_ids"][0]) for p in changes], ["EPSLSV"])

    def test_two_care_types_have_a_fallback(self):
        rows = [p for p in self.props if "fallback_fields" in p["actions"][-1]]
        self.assertEqual({tail(p["account_ids"][0]) for p in rows}, {"YHN220", "YCZ4YM"})
        for p in rows:
            fallback = p["actions"][-1]["fallback_fields"]
            self.assertEqual(fallback["care_type"], "Assisted Living")
            self.assertIn("Also offers: Memory Care", fallback["note"])

    def test_no_writes_without_a_proposal_guard(self):
        for p in self.props:
            if p["type"] != "new":
                self.assertTrue(p["guards"], p["title"])


class Rules(unittest.TestCase):
    def setUp(self):
        self.accounts = copy.deepcopy(ACCOUNTS)

    def lima(self, revenue, ar):
        find(self.accounts, "9MB6KL").update(lifetime_revenue=revenue, outstanding_ar=ar)
        props, _, _ = run(self.accounts)
        return next(p for p in props if p["slug"] == "bellhaven-crossings-of-lima")

    def test_chow_needs_both_revenue_and_ar(self):
        self.assertEqual(self.lima(100, 50)["type"], "chow")
        self.assertEqual(self.lima(100, 0)["type"], "reparent")
        self.assertEqual(self.lima(0, 50)["type"], "reparent")
        self.assertEqual(self.lima(0, 0)["type"], "reparent")

    def test_config_threshold_changes_the_rule(self):
        original = config.CHOW_MIN_AR
        config.CHOW_MIN_AR = 5000
        try:
            self.assertEqual(self.lima(100, 3000)["type"], "reparent")
            self.assertEqual(self.lima(100, 6000)["type"], "chow")
        finally:
            config.CHOW_MIN_AR = original

    def test_account_already_under_bellhaven_never_chows(self):
        props, _, _ = run()
        self.assertFalse([p for p in props if p["slug"] in ("bellhaven-of-defiance", "bellhaven-terrace-of-akron")])

    def test_billing_history_beats_being_under_the_right_parent(self):
        find(self.accounts, "LA9HTX").update(lifetime_revenue=500)
        props, _, _ = run(self.accounts)
        dups = {tail(p["account_ids"][0]) for p in props if p["type"] == "duplicate" and p["slug"] == "bellhaven-gardens-of-monroe"}
        self.assertEqual(dups, {"AGG1S5", "KM4FHM"})

    def test_contacts_beat_completeness(self):
        find(self.accounts, "AGG1S5").update(phone="")
        props, _, _ = run(self.accounts)
        dups = {tail(p["account_ids"][0]) for p in props if p["type"] == "duplicate" and p["slug"] == "bellhaven-gardens-of-monroe"}
        self.assertEqual(dups, {"LA9HTX", "KM4FHM"})

    def test_duplicate_with_ar_is_flagged(self):
        # Both copies carry AR, so one must lose. The loser needs a flag.
        find(self.accounts, "2ZTRE6").update(lifetime_revenue=900, outstanding_ar=400)
        find(self.accounts, "15UA71").update(lifetime_revenue=100, outstanding_ar=100)
        props, _, _ = run(self.accounts)
        p = next(p for p in props if p["type"] == "duplicate" and p["slug"] == "bellhaven-of-owosso")
        self.assertTrue(tail(p["account_ids"][0]) == "15UA71" and "outstanding AR" in p["flags"][0])

    def test_second_parent_is_reported(self):
        extra = copy.deepcopy(find(self.accounts, "VYEEEM"))
        extra.update(account_id="001SECONDPARENT000", name="Bellhaven Senior Living LLC (Parent Account)")
        self.accounts.append(extra)
        props, _, warnings = run(self.accounts)
        self.assertEqual(len(warnings), 1)
        self.assertEqual([p["type"] for p in props if p["type"] == "duplicate_parent"], ["duplicate_parent"])

    def test_partial_scrape_stops_instead_of_flagging_everything(self):
        with self.assertRaises(ValueError):
            run(site=SITE[:5])


if __name__ == "__main__":
    unittest.main()
