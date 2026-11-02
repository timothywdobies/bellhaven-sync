# Bellhaven CRM Sync

## What I built and why

Sales needs to know who owns each long term care facility, because corporate-owned facilities sell differently. Facilities change hands often, and the CRM falls behind. This system compares the Bellhaven website to the CRM every day and proposes fixes. A person reviews each one, and billing history is protected by the CHOW rule.

Flow: website scrape, then matching, then a review queue, then approved writes, then an independent check.

| File | Job |
|---|---|
| `config.py` | Every rule, threshold, and mapping |
| `scrape.py` | Reads all communities from the website |
| `crm.py` | Talks to the CRM API |
| `match.py` | Matches, classifies, and proposes. Never writes |
| `store.py` | Saves proposals and decisions as JSON in `data/` |
| `apply.py` | The only code that writes to the CRM |
| `app.py` | Local review app. The only caller of `apply.py` |
| `pipeline.py` | The daily run. Read only |
| `verify.py` | Independent check of the CRM end state |

## How to run it

Put your token in `.env`, using `.env.example` as the pattern. Then:

```
./run.sh
```

This installs dependencies, refreshes the queue, and starts the app at http://127.0.0.1:5050.

Other commands:

```
.venv/bin/python pipeline.py            # Refresh the queue only
.venv/bin/python verify.py              # Check the CRM end state
.venv/bin/python tests/test_match.py    # Matching and rule tests
.venv/bin/python tests/test_flow.py     # Full rehearsal on a fake CRM
```

The daily schedule is `.github/workflows/daily.yml`. It needs a repository secret named `CRM_API_TOKEN`. It reads only, then commits the refreshed queue to `data/`.

## Matching approach

1. Compare street and city after normalizing spelling, so "Pk" equals "Pike". Zip is not in the key, so a bad zip can still match and be fixed.
2. If no address matches, try a fallback: same city, name score of at least 0.85, and a phone or administrator match. This catches the Ashtabula PO Box.
3. Name alone never matches. "Amberly Manor" exists in Colorado, and that account stays untouched.

Several accounts at one address are duplicates. The survivor is chosen in this order: has revenue or AR, more contacts, most complete record. Ties then go to the account under Bellhaven, then one with no parent, then the one whose phone matches the website.

## CHOW rule

An account moving to a new parent with lifetime revenue above 0 and outstanding AR above 0 stays exactly as it is. A new account is created under the correct parent, and `chow_current_account` on the old account points to it. Otherwise the existing account is re-parented directly. Accounts already under Bellhaven are not moving, so the rule does not apply to them.

## Re-runs

Each proposal has an id built from what it would change. Approved and rejected ids are stored in `data/decisions.json` and never return. If the facts change, the id changes, and a new proposal appears. Accounts already marked as duplicates or CHOW are skipped. Writes save progress after each step, so a retry cannot create a second account.

## ASSUMPTIONS

- **The website is the source of truth for current Bellhaven locations.** It is the only ownership evidence available.
- **Sandusky links to the existing Millstone account instead of a new one.** The SOP says to create an account, but one already exists there, and another would be a duplicate.
- **Alliance and Coldwater become Needs Review with their parent unchanged.** Leaving the directory does not prove closure or a sale, and no new owner is visible.
- **The Ashtabula PO Box is replaced by the street address and saved in the note.** The CRM holds one address, and the PO Box may still be a real billing address.
- **Phones are match evidence only.** The assignment does not list phone as a scraped field. Set `UPDATE_PHONE = True` to change this.
- **Union Square gets a new account, and the Juniper account stays.** The address, phone, and administrator all differ.
- **The first care offering on the website is the primary one.** The website gives no other order.
- **Two care types are stored as "Assisted Living; Memory Care".** The API accepted this value for Erie and Findlay. If it had refused, the app would have stored the primary and listed the rest in the note.
- **A rebrand is a name score under 0.80.** Spelling corrections do not need a "Formerly" note.
- **Losing duplicates keep their old parent.** They are Inactive, and the duplicate link carries the meaning.
- **A contact on a losing duplicate moves to the survivor.** Otherwise the contact is stranded on an Inactive account.
- **New accounts start with zero revenue and take the website phone.** Billing history stays on the old account.

## How it is verified

Result on the live CRM after review: 30 proposals approved, 0 rejected, and 11 of 11 checks passed. A second pipeline run proposed nothing.


`tests/test_match.py` checks the classification of the real data against a hand count, plus each rule at its edges.

`tests/test_flow.py` approves everything on a fake CRM, then proves that a second run proposes nothing and all checks pass. It also breaks a correct end state on purpose to prove `verify.py` catches it.

`verify.py` runs against the live CRM. It does not import the matcher, it uses a simpler address key of house number plus city, and it compares to `data/baseline_accounts.json`, saved before any write. It checks:

- Exactly one Bellhaven parent exists.
- Row counts reconcile to the snapshot plus approved creations.
- Every website location has one correct current account under Bellhaven.
- No two current accounts share an address.
- Duplicates are Inactive and point to a current survivor.
- CHOW accounts are otherwise unchanged and link to the right place.
- The SOP held on every move, using the snapshot's numbers.
- Accounts dropped from the website are resolved.
- Only approved accounts changed.
- No contact is stranded on a duplicate.
- The review queue is empty.

## Limitations

- The API docs do not list create or update fields. Field names came from read responses, and every write was read back to confirm it saved.
- There is no delete, so an approved bad create can only be set Inactive.
- Ownership comes from one website. A sale it has not published yet is invisible.
- `verify.py` shares the scraper with the pipeline, so a scraper mistake could affect both. The scraper stops if it finds fewer locations than the site states.
- Administrator differences between the website and CRM contacts are shown as evidence but not fixed.
- New accounts get no contacts, and CHOW contacts stay on the old account.
- Rejected items that leave a real gap show as failures in `verify.py`. This is deliberate.

## What I'd do next

- Add a Slack alert when a new proposal or flag appears.
- Sync administrators into contacts.
- Run this across all parent companies, driven by a list of operator websites.
- Replace JSON files with a small database once several reviewers share the queue.
