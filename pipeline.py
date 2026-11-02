"""Daily run: read the website and CRM, then write proposals. It never writes to the CRM."""
import json
import sys
from collections import Counter

import match
import scrape
from crm import CRM
from store import Store, build_queue


def main():
    store = Store()
    try:
        locations = scrape.scrape_all()
    except (scrape.ScrapeError, OSError) as error:
        sys.exit(f"Stopped before matching: {error}")
    crm = CRM()
    accounts, contacts = crm.list_accounts(), crm.list_contacts()

    if not store.read("baseline_accounts.json", None):
        store.write("baseline_accounts.json", accounts)
    store.write("site_latest.json", locations)

    proposals, confirmed, warnings = match.classify(locations, accounts, contacts)
    decisions = store.decisions()
    queue = build_queue(proposals, decisions)
    store.save_proposals(queue)
    store.write("confirmed.json", confirmed)

    print(f"Website locations: {len(locations)}. CRM accounts: {len(accounts)}.")
    print(f"Confirmed with no change: {len(confirmed)}.")
    print(f"Found this run: {len(proposals)}. Already decided: {len(proposals) - len(queue)}.")
    print(f"Waiting for review: {len(queue)}.")
    print(json.dumps(Counter(p["type"] for p in queue), sort_keys=True))
    for warning in warnings:
        print("WARNING:", warning)


if __name__ == "__main__":
    main()
