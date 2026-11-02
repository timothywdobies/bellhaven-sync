"""An in-memory stand-in for the CRM, so tests never touch the real one."""
import copy
import itertools

from crm import CRMError


class FakeCRM:
    def __init__(self, accounts, contacts, refuse_two_care_types=False, drop_care_silently=False):
        self.accounts = {a["account_id"]: copy.deepcopy(a) for a in accounts}
        self.contacts = {c["contact_id"]: copy.deepcopy(c) for c in contacts}
        self.refuse = refuse_two_care_types
        self.drop = drop_care_silently
        self.fail_on_call = None
        self.calls = []
        self.ids = itertools.count(1)

    def list_accounts(self, **filters):
        rows = list(self.accounts.values())
        if filters.get("parent_id"):
            rows = [r for r in rows if r["parent_id"] == filters["parent_id"]]
        if filters.get("city"):
            rows = [r for r in rows if r["billing_city"] == filters["city"]]
        return copy.deepcopy(rows)

    def list_contacts(self):
        return copy.deepcopy(list(self.contacts.values()))

    def get_account(self, account_id):
        return copy.deepcopy(self.accounts[account_id])

    def get_contact(self, contact_id):
        return copy.deepcopy(self.contacts[contact_id])

    def _count(self, name):
        self.calls.append(name)
        if self.fail_on_call == len(self.calls):
            raise CRMError(500, "simulated outage")

    def _name_parent(self, row):
        parent = self.accounts.get(row.get("parent_id"))
        row["parent_name"] = parent["name"] if parent else ""

    def patch_account(self, account_id, fields):
        self._count("patch_account")
        care = fields.get("care_type", "")
        if ";" in care and self.refuse:
            raise CRMError(422, "care_type must be a single value")
        row = self.accounts[account_id]
        row.update(fields)
        if ";" in care and self.drop:
            row["care_type"] = care.split(";")[0]
        self._name_parent(row)
        return copy.deepcopy(row)

    def create_account(self, fields):
        self._count("create_account")
        new_id = f"NEW{next(self.ids):04d}"
        row = {"account_id": new_id, "lifetime_revenue": 0, "outstanding_ar": 0,
               "chow_current_account": "", "duplicate_of_account": "", "created_by_candidate": True,
               "updated_at": "", **fields}
        self._name_parent(row)
        self.accounts[new_id] = row
        return copy.deepcopy(row)

    def patch_contact(self, contact_id, fields):
        self._count("patch_contact")
        self.contacts[contact_id].update(fields)
        return copy.deepcopy(self.contacts[contact_id])
