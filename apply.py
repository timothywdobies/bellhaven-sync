"""Runs the actions of one approved proposal against the CRM.

This is the only file that writes to the CRM, and the review app is its only caller.
"""
import match
from crm import CRMError


class StaleProposal(Exception):
    pass


class WriteMismatch(Exception):
    pass


def check_guards(crm, proposal):
    """Stop if an account changed after the proposal was made."""
    for account_id, expected in proposal["guards"].items():
        live = crm.get_account(account_id)
        changed = [field for field, value in expected.items() if live.get(field) != value]
        if changed:
            raise StaleProposal(f"{live['name']} changed since this proposal: {changed}. Re-run the pipeline.")


def confirm_write(live, fields):
    """Read back what was saved, so a silently ignored field is caught."""
    wrong = [field for field, value in fields.items() if live.get(field) != value]
    if wrong:
        raise WriteMismatch(f"The CRM did not store {wrong}.")


def find_created(crm, fields):
    """Find an account an earlier interrupted attempt already created."""
    for row in crm.list_accounts(parent_id=fields["parent_id"], city=fields["billing_city"]):
        if match.norm_street(row["billing_street"]) == match.norm_street(fields["billing_street"]):
            return row["account_id"]
    return None


def create(crm, fields):
    existing = find_created(crm, fields)
    if existing:
        return existing, "Reused account created by an earlier attempt."
    created = crm.create_account(fields)
    new_id = created.get("account_id") or find_created(crm, fields)
    if not new_id:
        raise WriteMismatch("The API did not return the new account id.")
    confirm_write(crm.get_account(new_id), fields)
    return new_id, f"Created account {new_id}."


def patch(crm, action, new_id):
    account_id = action["account_id"]
    fields = {k: (new_id if v == "$new" else v) for k, v in action["fields"].items()}
    try:
        crm.patch_account(account_id, fields)
        confirm_write(crm.get_account(account_id), fields)
        return f"Updated {account_id}: {sorted(fields)}."
    except (CRMError, WriteMismatch) as error:
        fallback = action.get("fallback_fields")
        if not fallback:
            raise
        crm.patch_account(account_id, fallback)
        confirm_write(crm.get_account(account_id), fallback)
        return f"Updated {account_id} with one care type. Two were refused: {error}"


def run(crm, proposal, record, save):
    """Run remaining actions. Progress saves after each one, so a retry resumes safely."""
    if not record.get("done"):
        check_guards(crm, proposal)
    log = record.setdefault("log", [])
    for index in range(record.get("done", 0), len(proposal["actions"])):
        action = proposal["actions"][index]
        if action["op"] == "create_account":
            record["new_id"], line = create(crm, action["fields"])
        elif action["op"] == "patch_account":
            line = patch(crm, action, record.get("new_id"))
        elif action["op"] == "patch_contact":
            crm.patch_contact(action["contact_id"], action["fields"])
            confirm_write(crm.get_contact(action["contact_id"]), action["fields"])
            line = f"Moved contact {action['contact_name']}."
        else:
            raise ValueError(f"Unknown action {action['op']}")
        log.append(line)
        record["done"] = index + 1
        save(record)
    return record
