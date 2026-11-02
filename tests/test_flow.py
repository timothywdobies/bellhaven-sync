"""End-to-end rehearsal on the fake CRM: approvals, re-runs, failures, and verification."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import apply
import match
import verify
from app import create_app
from crm import CRMError
from fake_crm import FakeCRM
from store import Store, build_queue

FIX = ROOT / "tests" / "fixtures"
SITE = json.loads((FIX / "site.json").read_text())
ACCOUNTS = json.loads((FIX / "accounts.json").read_text())
CONTACTS = json.loads((FIX / "contacts.json").read_text())


class Base(unittest.TestCase):
    def setUp(self, **options):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.crm = FakeCRM(ACCOUNTS, CONTACTS, **options)
        self.client = create_app(self.crm, self.store).test_client()
        self.pipeline()

    def pipeline(self):
        props, confirmed, warnings = match.classify(SITE, self.crm.list_accounts(), self.crm.list_contacts())
        queue = build_queue(props, self.store.decisions())
        self.store.save_proposals(queue)
        self.store.write("confirmed.json", confirmed)
        return queue

    def approve_all(self):
        for item in self.store.proposals():
            self.client.post(f"/approve/{item['id']}", data={"ack": "1"})

    def item(self, words):
        return next(p for p in self.store.proposals() if words in p["title"])

    def checks(self):
        return verify.run_checks(SITE, self.crm.list_accounts(), self.crm.list_contacts(), ACCOUNTS,
                                 self.store.decisions(), self.store.proposals_pending())

    def failures(self):
        return {name: problems for name, problems, _ in self.checks() if problems}


class EndState(Base):
    def test_nothing_writes_until_approved(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/confirmed").status_code, 200)
        self.assertEqual(self.client.get("/history").status_code, 200)
        self.assertEqual(self.crm.calls, [])

    def test_approving_everything_passes_every_check(self):
        self.approve_all()
        self.assertEqual(self.pipeline(), [])
        self.assertEqual(self.failures(), {})
        self.assertEqual(len(self.crm.accounts), 127)

    def test_second_run_after_approval_proposes_nothing(self):
        self.approve_all()
        self.pipeline()
        self.assertEqual(self.pipeline(), [])

    def test_chow_end_state(self):
        old_before = next(a for a in ACCOUNTS if a["name"] == "Bellhaven of Tiffin")
        self.client.post(f"/approve/{self.item('CHOW: Bellhaven of Tiffin')['id']}")
        old = self.crm.get_account(old_before["account_id"])
        new = self.crm.get_account(old["chow_current_account"])
        self.assertEqual(old["parent_id"], old_before["parent_id"])
        self.assertEqual({k: v for k, v in old.items() if k not in ("chow_current_account",)},
                         {k: v for k, v in old_before.items() if k not in ("chow_current_account",)})
        self.assertEqual(new["parent_name"], "Bellhaven Senior Living (Parent Account)")
        self.assertEqual(new["lifetime_revenue"], 0)

    def test_rejected_item_is_not_reproposed(self):
        item = self.item("Field fix: Bellhaven of Portsmouth")
        self.client.post(f"/reject/{item['id']}", data={"reason": "test"})
        self.assertEqual(self.crm.calls, [])
        self.assertNotIn(item["id"], [p["id"] for p in self.pipeline()])
        self.assertNotIn(item["id"], [p["id"] for p in self.pipeline()])

    def test_rejected_item_returns_if_the_facts_change(self):
        item = self.item("Field fix: Bellhaven of Portsmouth")
        self.client.post(f"/reject/{item['id']}")
        site = copy.deepcopy(SITE)
        next(l for l in site if l["slug"] == "bellhaven-of-portsmouth")["zip"] = "45663"
        props, _, _ = match.classify(site, self.crm.list_accounts(), self.crm.list_contacts())
        queue = build_queue(props, self.store.decisions())
        self.assertTrue([p for p in queue if "Portsmouth" in p["title"]])

    def test_reopen_returns_a_rejected_item(self):
        item = self.item("Field fix: Bellhaven of Portsmouth")
        self.client.post(f"/reject/{item['id']}")
        self.pipeline()
        self.client.post(f"/reopen/{item['id']}")
        self.assertIn(item["id"], [p["id"] for p in self.store.proposals_pending()])

    def test_verify_reports_rejected_gap(self):
        self.client.post(f"/reject/{self.item('Field fix: Bellhaven of Portsmouth')['id']}")
        self.approve_all()
        self.assertEqual(list(self.failures()), ["Every website location has one correct Bellhaven account"])


class Failures(Base):
    def test_stale_proposal_stops_before_writing(self):
        item = self.item("Field fix: Bellhaven of Portsmouth")
        self.crm.accounts[item["account_ids"][0]]["billing_zip"] = "00000"
        self.client.post(f"/approve/{item['id']}")
        self.assertEqual(self.crm.calls, [])
        self.assertEqual(self.store.decisions()[item["id"]]["decision"], "failed")

    def test_crash_mid_chow_resumes_without_a_second_account(self):
        item = self.item("CHOW: Bellhaven of Marietta")
        self.crm.fail_on_call = 2  # Create works, then the link write fails.
        self.client.post(f"/approve/{item['id']}")
        self.assertEqual(len(self.crm.accounts), 122)
        queue = self.pipeline()
        self.assertEqual([p["id"] for p in queue if "Marietta" in p["title"]], [item["id"]])
        self.crm.fail_on_call = None
        self.client.post(f"/approve/{item['id']}")
        self.assertEqual(len(self.crm.accounts), 122)
        self.assertEqual(self.store.decisions()[item["id"]]["decision"], "approved")

    def test_flagged_item_needs_the_checkbox(self):
        accounts = copy.deepcopy(ACCOUNTS)
        for row in accounts:
            if row["account_id"].endswith("2ZTRE6"):
                row.update(lifetime_revenue=900, outstanding_ar=400)
            if row["account_id"].endswith("15UA71"):
                row.update(lifetime_revenue=100, outstanding_ar=100)
        self.crm = FakeCRM(accounts, CONTACTS)
        self.client = create_app(self.crm, self.store).test_client()
        self.pipeline()
        item = self.item("Duplicate of Bellhaven of Owosso")
        self.client.post(f"/approve/{item['id']}")
        self.assertEqual(self.crm.calls, [])
        self.client.post(f"/approve/{item['id']}", data={"ack": "1"})
        self.assertIn("patch_account", self.crm.calls)


class CareTypes(Base):
    ERIE = "Field fix: Bellhaven Shores of Erie"

    def test_two_care_types_stored_when_accepted(self):
        item = self.item(self.ERIE)
        self.client.post(f"/approve/{item['id']}")
        self.assertEqual(self.crm.get_account(item["account_ids"][0])["care_type"], "Assisted Living; Memory Care")

    def run_refused(self, **options):
        Base.setUp(self, **options)
        item = self.item(self.ERIE)
        self.client.post(f"/approve/{item['id']}")
        row = self.crm.get_account(item["account_ids"][0])
        self.assertEqual(row["care_type"], "Assisted Living")
        self.assertIn("Also offers: Memory Care", row["note"])
        self.assertFalse([p for p in self.pipeline() if p["title"] == self.ERIE])

    def test_refused_falls_back_to_primary_and_note(self):
        self.run_refused(refuse_two_care_types=True)

    def test_silently_dropped_falls_back_too(self):
        self.run_refused(drop_care_silently=True)

    def test_all_pass_when_two_care_types_are_refused(self):
        Base.setUp(self, refuse_two_care_types=True)
        self.approve_all()
        self.assertEqual(self.pipeline(), [])
        self.assertEqual(self.failures(), {})


class VerifyCatchesErrors(Base):
    """Break a correct end state on purpose. Each break must be caught."""

    def setUp(self):
        super().setUp()
        self.approve_all()
        self.pipeline()

    def row(self, name, status="Active"):
        return next(a for a in self.crm.accounts.values() if a["name"] == name and a["chow_current_account"] == "" and a["status"] == status)

    def test_catches_moved_chow_account(self):
        old = next(a for a in self.crm.accounts.values() if a["name"] == "Bellhaven of Tiffin" and a["chow_current_account"])
        old["parent_id"] = "0015QAPLGS3FVYEEEM"
        self.assertIn("CHOW SOP followed on every move", self.failures())

    def test_catches_duplicate_left_active(self):
        self.crm.accounts[next(a for a in self.crm.accounts if a.endswith("15UA71"))]["status"] = "Active"
        self.assertIn("Duplicates are Inactive and point to a current survivor", self.failures())

    def test_catches_unapproved_change(self):
        self.crm.accounts[next(a for a in self.crm.accounts if a.endswith("10RGY5"))]["phone"] = "1"
        self.assertIn("Only approved accounts changed", self.failures())

    def test_catches_lost_account(self):
        del self.crm.accounts[next(a for a in self.crm.accounts if a.endswith("10RGY5"))]
        names = self.failures()
        self.assertIn("Row counts reconcile", names)


if __name__ == "__main__":
    unittest.main()
