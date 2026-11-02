"""Independent check of the CRM end state.

This does not import the matcher. It uses its own simpler address key of house number
plus city, and compares the live CRM to the snapshot saved before any writes.
"""
import sys

import config
import scrape
from crm import CRM
from store import Store

IGNORED = ("updated_at", "parent_name")


def place(street, city):
    words = (street or "").lower().split()
    return (words[0] if words else "", (city or "").lower())


def is_current(row):
    return (row["status"] == config.STATUS_ACTIVE and row["billing_street"]
            and not row["duplicate_of_account"] and not row["chow_current_account"])


def changed_fields(before, after):
    return [k for k in after if k not in IGNORED and before.get(k) != after[k]]


def run_checks(site, accounts, contacts, baseline, decisions, queue):
    """Return a list of (check name, problems, info)."""
    results = []
    now = {a["account_id"]: a for a in accounts}
    base = {a["account_id"]: a for a in baseline}

    def check(name, problems, info=""):
        results.append((name, problems, info))

    # Parent
    parents = [a for a in accounts if not a["billing_street"] and config.PARENT_KEYWORD in a["name"].lower()]
    check("Exactly one Bellhaven parent account", [] if len(parents) == 1 else [a["name"] for a in parents])
    parent_id = parents[0]["account_id"]

    # Row counts
    created = [a for a in accounts if a["account_id"] not in base]
    approved = [r for r in decisions.values() if r["decision"] == "approved"]
    expected_new = sum(1 for r in approved for a in r["proposal"]["actions"] if a["op"] == "create_account")
    problems = []
    if len(accounts) != len(baseline) + len(created):
        problems.append("Accounts disappeared since the snapshot.")
    problems += [f"{base[i]['name']} no longer exists" for i in base if i not in now]
    if len(created) != expected_new:
        problems.append(f"{len(created)} accounts were created but {expected_new} were approved.")
    check("Row counts reconcile", problems, f"{len(baseline)} at start + {len(created)} created = {len(accounts)}")

    # Website coverage
    problems = []
    for loc in site:
        hits = [a for a in accounts if is_current(a) and a["parent_id"] == parent_id
                and place(a["billing_street"], a["billing_city"]) == place(loc["street"], loc["city"])]
        if len(hits) != 1:
            problems.append(f"{loc['name']}: {len(hits)} current Bellhaven accounts")
        elif hits[0]["name"] != loc["name"] or hits[0]["billing_zip"] != loc["zip"]:
            problems.append(f"{loc['name']}: CRM has '{hits[0]['name']}', zip {hits[0]['billing_zip']}")
    check("Every website location has one correct Bellhaven account", problems, f"{len(site)} locations")

    # No active duplicates
    seen, problems = {}, []
    for a in accounts:
        if is_current(a):
            key = place(a["billing_street"], a["billing_city"])
            if key in seen:
                problems.append(f"{a['name']} and {seen[key]} share {key}")
            seen[key] = a["name"]
    check("No two current accounts share an address", problems)

    # Duplicates
    problems, losers = [], [a for a in accounts if a["duplicate_of_account"]]
    for a in losers:
        target = now.get(a["duplicate_of_account"])
        if a["status"] != config.STATUS_INACTIVE:
            problems.append(f"{a['name']} is a duplicate but not Inactive")
        if not target or not is_current(target):
            problems.append(f"{a['name']} points to an account that is not current")
    check("Duplicates are Inactive and point to a current survivor", problems, f"{len(losers)} duplicates")

    # CHOW
    problems, old_accounts = [], [a for a in accounts if a["chow_current_account"]]
    for a in old_accounts:
        different = [f for f in changed_fields(base.get(a["account_id"], {}), a) if f != "chow_current_account"]
        target = now.get(a["chow_current_account"])
        if different:
            problems.append(f"{a['name']} changed: {different}")
        if not target or target["parent_id"] == a["parent_id"]:
            problems.append(f"{a['name']} CHOW target is missing or has the same parent")
        elif place(target["billing_street"], target["billing_city"]) != place(a["billing_street"], a["billing_city"]):
            problems.append(f"{a['name']} CHOW target is at a different address")
    check("CHOW accounts are preserved and linked", problems, f"{len(old_accounts)} CHOW links")

    # The SOP, checked from the snapshot's numbers
    problems = []
    for a in accounts:
        before = base.get(a["account_id"])
        if before and before["parent_id"] != a["parent_id"] and before["lifetime_revenue"] > 0 and before["outstanding_ar"] > 0:
            problems.append(f"{a['name']} changed parent but has revenue and AR")
    for loc in site:
        for before in baseline:
            same = place(before["billing_street"], before["billing_city"]) == place(loc["street"], loc["city"])
            if same and before["parent_id"] != parent_id and before["lifetime_revenue"] > 0 and before["outstanding_ar"] > 0:
                if not now.get(before["account_id"], {}).get("chow_current_account"):
                    problems.append(f"{before['name']} needs a CHOW link")
    check("CHOW SOP followed on every move", problems)

    # Accounts the website dropped
    on_site = {place(l["street"], l["city"]) for l in site}
    problems = [a["name"] for a in accounts
                if a["parent_id"] == parent_id and is_current(a)
                and place(a["billing_street"], a["billing_city"]) not in on_site]
    check("Accounts dropped from the website are resolved", problems)

    # Nothing unapproved changed
    allowed = set()
    for record in approved + [r for r in decisions.values() if r["decision"] == "failed"]:
        allowed |= {a["account_id"] for a in record["proposal"]["actions"] if "account_id" in a}
    problems = [now[i]["name"] for i in base if i in now and i not in allowed and changed_fields(base[i], now[i])]
    check("Only approved accounts changed", problems)

    # Contacts
    problems = [f"{c['name']} is on Inactive {now[c['account_id']]['name']}" for c in contacts
                if c["account_id"] in now and now[c["account_id"]]["duplicate_of_account"]]
    problems += [f"{c['name']} has no account" for c in contacts if c["account_id"] not in now]
    check("No contact is stranded on a duplicate", problems, f"{len(contacts)} contacts")

    # Review state
    failed = [r["proposal"]["title"] for r in decisions.values() if r["decision"] == "failed"]
    rejected = [r["proposal"]["title"] for r in decisions.values() if r["decision"] == "rejected"]
    check("Review queue is empty", [p["title"] for p in queue] + failed, f"{len(rejected)} rejected")
    return results


def main():
    store = Store()
    crm = CRM()
    site = scrape.scrape_all()
    results = run_checks(site, crm.list_accounts(), crm.list_contacts(),
                         store.read("baseline_accounts.json", []), store.decisions(), store.proposals_pending())
    failures = 0
    for name, problems, info in results:
        print(f"{'PASS' if not problems else 'FAIL'}  {name}{'  (' + info + ')' if info else ''}")
        for problem in problems[:40]:
            print(f"        {problem}")
        failures += bool(problems)
    print(f"\n{len(results) - failures} of {len(results)} checks passed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
