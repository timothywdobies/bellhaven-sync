"""Links website locations to CRM accounts and proposes changes.

This file only decides. It never writes to the CRM. Each proposal lists the exact
actions that will run if a reviewer approves it, plus the evidence behind it.
"""
import difflib
import hashlib
import json
import re
from collections import defaultdict

import config

CHOW_SOP = "CHOW SOP: lifetime revenue above {r:,} and outstanding AR above {a:,}"


# ---------- Small helpers ----------

def norm_street(text):
    words = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
    return " ".join(config.STREET_WORDS.get(word, word) for word in words)


def digits(text):
    return re.sub(r"\D", "", text or "")


def name_score(a, b):
    return round(difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio(), 2)


def address_key(street, city, state):
    return (norm_street(street), (city or "").lower(), (state or "").lower())


def account_key(account):
    return address_key(account["billing_street"], account["billing_city"], account["billing_state"])


def add_note(existing, text):
    if not existing:
        return text
    return existing if text in existing else f"{existing} | {text}"


def needs_chow(account):
    return (account["lifetime_revenue"] > config.CHOW_MIN_REVENUE
            and account["outstanding_ar"] > config.CHOW_MIN_AR)


def money(account):
    return f"Lifetime revenue {account['lifetime_revenue']:,}, outstanding AR {account['outstanding_ar']:,}"


def is_linked(account):
    """True once an account was already resolved as a duplicate or a CHOW."""
    return bool(account["duplicate_of_account"] or account["chow_current_account"])


def is_facility(account):
    return bool(account["billing_street"])


def proposal(kind, title, slug, accounts, evidence, actions, guards=None, flags=None, site=None):
    guards = guards or {}
    signature = json.dumps([kind, slug, actions], sort_keys=True)
    return {
        "id": hashlib.sha1(signature.encode()).hexdigest()[:12],
        "type": kind, "title": title, "slug": slug,
        "account_ids": [a["account_id"] for a in accounts],
        "accounts": [snapshot(a) for a in accounts],
        "site": site, "evidence": evidence, "actions": actions,
        "guards": guards, "flags": flags or [],
    }


def snapshot(account):
    keep = ("account_id", "name", "parent_name", "billing_street", "billing_city", "billing_state",
            "billing_zip", "care_type", "status", "phone", "lifetime_revenue", "outstanding_ar", "note")
    return {key: account[key] for key in keep}


def guard_for(account):
    """The fields a change depends on. Approval stops if any changed since the proposal."""
    keep = ("name", "parent_id", "status", "note", "billing_street", "billing_zip", "care_type",
            "lifetime_revenue", "outstanding_ar", "chow_current_account", "duplicate_of_account")
    return {account["account_id"]: {key: account[key] for key in keep}}


# ---------- The operator's parent account ----------

def find_parents(accounts):
    """Return the operator's parent account and any second one."""
    child_counts = defaultdict(int)
    for account in accounts:
        if account["parent_id"]:
            child_counts[account["parent_id"]] += 1
    found = [a for a in accounts
             if config.PARENT_KEYWORD in a["name"].lower()
             and (child_counts.get(a["account_id"]) or "parent account" in a["name"].lower())]
    if not found:
        raise ValueError(f"No parent account contains '{config.PARENT_KEYWORD}'.")
    found.sort(key=lambda a: (-child_counts.get(a["account_id"], 0), a["account_id"]))
    return found[0], found[1:], child_counts


def duplicate_parent_proposal(extra, parent, child_counts):
    fields = {"status": config.STATUS_INACTIVE, "duplicate_of_account": parent["account_id"],
              "note": add_note(extra["note"], config.DUPLICATE_NOTE.format(survivor_id=parent["account_id"]))}
    return proposal(
        "duplicate_parent", f"Second parent account: {extra['name']}", "", [extra],
        [f"Keeps {parent['name']} with {child_counts.get(parent['account_id'], 0)} child accounts.",
         f"This parent has {child_counts.get(extra['account_id'], 0)} child accounts, which are not moved here."],
        [{"op": "patch_account", "account_id": extra["account_id"], "fields": fields}],
        guard_for(extra),
        ["Second parent account found. Review before approving."])


# ---------- Duplicate survivor rule ----------

def completeness(account):
    fields = ("name", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type", "phone")
    return sum(1 for field in fields if account[field])


def survivor_rank(account, location, parent, contact_map):
    """Order: billing history, contacts, completeness, then neutral tie-breaks."""
    money_total = account["lifetime_revenue"] + account["outstanding_ar"]
    return (
        money_total > 0,
        money_total,
        len(contact_map[account["account_id"]]),
        completeness(account),
        account["parent_id"] == parent["account_id"],
        not account["parent_id"],
        digits(account["phone"]) == digits(location["phone"]),
        account["account_id"],
    )


def duplicate_proposal(loser, survivor, location, contact_map):
    fields = {"status": config.STATUS_INACTIVE, "duplicate_of_account": survivor["account_id"],
              "note": add_note(loser["note"], config.DUPLICATE_NOTE.format(survivor_id=survivor["account_id"]))}
    actions = [{"op": "patch_contact", "contact_id": c["contact_id"], "contact_name": c["name"],
                "fields": {"account_id": survivor["account_id"]}}
               for c in contact_map[loser["account_id"]]]
    actions.append({"op": "patch_account", "account_id": loser["account_id"], "fields": fields})
    flags = []
    if loser["outstanding_ar"] > 0:
        flags.append(f"This duplicate has outstanding AR of {loser['outstanding_ar']:,}. "
                     "Confirm billing is covered before marking it Inactive.")
    evidence = [
        f"Same address as surviving account {survivor['name']}: {location['street']}, {location['city']}.",
        f"Losing copy: {money(loser)}, {len(contact_map[loser['account_id']])} contacts.",
        f"Survivor: {money(survivor)}, {len(contact_map[survivor['account_id']])} contacts.",
    ]
    if actions[:-1]:
        evidence.append("Contacts move to the survivor: " + ", ".join(a["contact_name"] for a in actions[:-1]) + ".")
    return proposal("duplicate", f"Duplicate of {location['name']}: {loser['name']}", location["slug"],
                    [loser, survivor], evidence, actions, guard_for(loser), flags, site=location)


# ---------- Field comparison ----------

def care_change(account, location):
    """Return (preferred value, fallback value, extra labels) or None if nothing changes."""
    wanted = [config.CARE_MAP[label] for label in location["care_offerings"]]
    primary, extras = wanted[0], wanted[1:]
    current = account["care_type"]
    joined = config.CARE_SEPARATOR.join(wanted)
    if current == joined:
        return None
    extras_in_note = extras and all(e in account["note"] for e in extras)
    if current == primary and (not extras or extras_in_note):
        return None
    return joined, primary, extras


def field_changes(account, location):
    """Compare one account to the website. Returns fields, display reasons, and a care fallback."""
    fields, reasons, fallback = {}, [], None
    note = account["note"]

    if account["name"] != location["name"]:
        score = name_score(account["name"], location["name"])
        fields["name"] = location["name"]
        if score < config.REBRAND_BELOW:
            reasons.append(f"Rebrand: name score {score} is under {config.REBRAND_BELOW}.")
            note = add_note(note, config.FORMERLY_NOTE.format(old_name=account["name"]))
        else:
            reasons.append(f"Name correction: score {score}.")

    if norm_street(account["billing_street"]) != norm_street(location["street"]):
        fields["billing_street"] = location["street"]
        reasons.append(f"Street differs: CRM '{account['billing_street']}', website '{location['street']}'.")
        if account["billing_street"].lower().startswith("po box"):
            note = add_note(note, config.PO_BOX_NOTE.format(old_street=account["billing_street"]))
    if account["billing_city"].lower() != location["city"].lower():
        fields["billing_city"] = location["city"]
    if account["billing_state"] != location["state"]:
        fields["billing_state"] = location["state"]
    if account["billing_zip"] != location["zip"]:
        fields["billing_zip"] = location["zip"]
        reasons.append(f"Zip differs: CRM {account['billing_zip']}, website {location['zip']}.")

    if config.UPDATE_PHONE and digits(account["phone"]) != digits(location["phone"]):
        fields["phone"] = location["phone"]
        reasons.append(f"Phone differs: CRM {account['phone']}, website {location['phone']}.")

    if account["status"] != config.STATUS_ACTIVE:
        fields["status"] = config.STATUS_ACTIVE
        reasons.append(f"Listed on the website but status is {account['status']}.")

    care = care_change(account, location)
    if care:
        joined, primary, extras = care
        fields["care_type"] = joined
        reasons.append(f"Care type: CRM '{account['care_type']}', website offers '{joined}'.")
        if extras:
            fallback = {"care_type": primary,
                        "note": add_note(note, config.CARE_EXTRA_NOTE + ", ".join(extras))}
            reasons.append("If the API refuses two care types, this stores the primary and notes the rest.")

    if note != account["note"]:
        fields["note"] = note
    if fallback:
        fallback = {**fields, **fallback}
    return fields, reasons, fallback


def match_evidence(account, location, contact_map, method):
    names = [c["name"] for c in contact_map[account["account_id"]]]
    phone_ok = digits(account["phone"]) == digits(location["phone"])
    admin_ok = location["administrator"] in names
    return [
        f"Match method: {method}.",
        f"Website: {location['street']}, {location['city']}, {location['state']} {location['zip']}.",
        f"CRM: {account['billing_street']}, {account['billing_city']}, {account['billing_state']} {account['billing_zip']}.",
        f"Name score: {name_score(account['name'], location['name'])}.",
        f"Phone {'matches' if phone_ok else 'differs'}: CRM {account['phone']}, website {location['phone']}.",
        f"Administrator {location['administrator']} " + ("is a CRM contact." if admin_ok else f"is not a CRM contact. CRM has {names or 'none'}."),
        money(account) + ".",
    ]


def new_account_fields(location, parent, note=""):
    return {
        "name": location["name"], "parent_id": parent["account_id"],
        "billing_street": location["street"], "billing_city": location["city"],
        "billing_state": location["state"], "billing_zip": location["zip"],
        "care_type": config.CARE_MAP[location["care_offerings"][0]],
        "status": config.STATUS_ACTIVE, "phone": location["phone"], "note": note,
    }


# ---------- Decision for one matched location ----------

def propose_for_survivor(account, location, parent, contact_map, method):
    """Return a proposal, or None if the account is already correct."""
    evidence = match_evidence(account, location, contact_map, method)
    slug = location["slug"]

    if account["parent_id"] != parent["account_id"] and needs_chow(account):
        fields = new_account_fields(location, parent, config.CHOW_NEW_NOTE.format(old_id=account["account_id"]))
        rule = CHOW_SOP.format(r=config.CHOW_MIN_REVENUE, a=config.CHOW_MIN_AR)
        return proposal(
            "chow", f"CHOW: {location['name']} moves from {account['parent_name'] or 'no parent'}", slug, [account],
            evidence + [f"{rule}. The old account stays as it is, and a new account is created under {parent['name']}."],
            [{"op": "create_account", "fields": fields},
             {"op": "patch_account", "account_id": account["account_id"], "fields": {"chow_current_account": "$new"}}],
            guard_for(account), site=location)

    fields, reasons, fallback = field_changes(account, location)
    if account["parent_id"] != parent["account_id"]:
        fields["parent_id"] = parent["account_id"]
        if fallback:
            fallback["parent_id"] = parent["account_id"]
        reasons.insert(0, f"Parent is {account['parent_name'] or 'blank'}. The website lists it under Bellhaven. "
                          "The CHOW rule does not apply, so the account moves directly.")
        kind, title = "reparent", f"Wrong parent: {location['name']}"
    elif fields:
        kind, title = "field_fix", f"Field fix: {location['name']}"
    else:
        return None

    action = {"op": "patch_account", "account_id": account["account_id"], "fields": fields}
    if fallback:
        action["fallback_fields"] = fallback
    return proposal(kind, title, slug, [account], reasons + evidence, [action], guard_for(account), site=location)


def near_misses(location, pool):
    rows = []
    for account in pool:
        score = name_score(account["name"], location["name"])
        same_city = account["billing_city"].lower() == location["city"].lower()
        if score >= config.NEAR_MISS_NAME_MIN or same_city:
            rows.append(f"Not matched: {account['name']} at {account['billing_street']}, "
                        f"{account['billing_city']}, {account['billing_state']} under "
                        f"{account['parent_name'] or 'no parent'} (name score {score}).")
    return rows


# ---------- The whole run ----------

def classify(locations, accounts, contacts):
    parent, extra_parents, child_counts = find_parents(accounts)
    pool = [a for a in accounts if is_facility(a) and not is_linked(a)]

    contact_map = defaultdict(list)
    for contact in sorted(contacts, key=lambda c: c["contact_id"]):
        contact_map[contact["account_id"]].append(contact)
    by_key = defaultdict(list)
    for account in pool:
        by_key[account_key(account)].append(account)

    proposals, confirmed, warnings, used = [], [], [], {}
    for extra in extra_parents:
        proposals.append(duplicate_parent_proposal(extra, parent, child_counts))
        warnings.append(f"Second Bellhaven parent found: {extra['name']} ({extra['account_id']}).")

    for location in sorted(locations, key=lambda l: l["slug"]):
        key = address_key(location["street"], location["city"], location["state"])
        hits, method = by_key[key], "street and city"
        if not hits:
            hits, method = fallback_hits(location, pool, contact_map), "same city, strong name, and phone or administrator"
        hits = [a for a in hits if a["account_id"] not in used]
        if not hits:
            fields = new_account_fields(location, parent)
            proposals.append(proposal(
                "new", f"New account: {location['name']}", location["slug"], [],
                [f"No CRM account at {location['street']}, {location['city']}, {location['state']} {location['zip']}."]
                + near_misses(location, pool),
                [{"op": "create_account", "fields": fields}], site=location))
            continue

        hits = sorted(hits, key=lambda a: survivor_rank(a, location, parent, contact_map), reverse=True)
        survivor, losers = hits[0], hits[1:]
        for account in hits:
            used[account["account_id"]] = location["slug"]
        result = propose_for_survivor(survivor, location, parent, contact_map, method)
        if result:
            proposals.append(result)
        else:
            confirmed.append({"slug": location["slug"], "name": location["name"],
                              "account_id": survivor["account_id"],
                              "evidence": match_evidence(survivor, location, contact_map, method)})
        for loser in losers:
            proposals.append(duplicate_proposal(loser, survivor, location, contact_map))

    missing = [a for a in pool if a["parent_id"] == parent["account_id"] and a["account_id"] not in used]
    if pool and len(missing) / max(1, len(locations)) > config.MAX_MISSING_SHARE:
        raise ValueError(f"{len(missing)} accounts look missing. Stopping, because the scrape may be wrong.")
    for account in missing:
        result = missing_proposal(account, by_key, parent)
        if result:
            proposals.append(result)

    proposals.sort(key=lambda p: (p["type"], p["title"], p["id"]))
    return proposals, confirmed, warnings


def fallback_hits(location, pool, contact_map):
    """Second try for accounts whose street is unusable, such as a PO Box."""
    hits = []
    for account in pool:
        same_place = (account["billing_city"].lower() == location["city"].lower()
                      and account["billing_state"] == location["state"])
        names = [c["name"] for c in contact_map[account["account_id"]]]
        backed = (digits(account["phone"]) == digits(location["phone"]) or location["administrator"] in names)
        if same_place and backed and name_score(account["name"], location["name"]) >= config.FALLBACK_NAME_MIN:
            hits.append(account)
    return hits


def missing_proposal(account, by_key, parent):
    """An account under the operator that the website no longer lists."""
    if account["status"] == config.STATUS_REVIEW:
        return None
    owners = [a for a in by_key[account_key(account)]
              if a["account_id"] != account["account_id"] and a["parent_id"] != parent["account_id"]]
    place = f"{account['billing_street']}, {account['billing_city']}, {account['billing_state']}"
    if owners:
        new_owner = owners[0]
        evidence = [f"Not on the website. {new_owner['name']} sits at the same address under {new_owner['parent_name']}.",
                    money(account) + "."]
        if needs_chow(account):
            evidence.append("The CHOW SOP applies. The old account stays as it is. It links to the existing "
                            "account, because a new account here would be a duplicate.")
            return proposal(
                "chow", f"CHOW out of Bellhaven: {account['name']}", "", [account, new_owner], evidence,
                [{"op": "patch_account", "account_id": account["account_id"],
                  "fields": {"chow_current_account": new_owner["account_id"]}}],
                guard_for(account))
        fields = {"status": config.STATUS_INACTIVE, "duplicate_of_account": new_owner["account_id"],
                  "note": add_note(account["note"], config.DUPLICATE_NOTE.format(survivor_id=new_owner["account_id"]))}
        flags = []
        if account["outstanding_ar"] > 0:
            flags.append(f"This duplicate has outstanding AR of {account['outstanding_ar']:,}. "
                         "Confirm billing is covered before marking it Inactive.")
        return proposal("duplicate", f"Sold, duplicate of {new_owner['name']}: {account['name']}", "",
                        [account, new_owner], evidence,
                        [{"op": "patch_account", "account_id": account["account_id"], "fields": fields}],
                        guard_for(account), flags)
    note = add_note(account["note"], config.MISSING_NOTE)
    return proposal(
        "missing", f"Missing from website: {account['name']}", "", [account],
        [f"No website location at {place}, and its name is not on the website.",
         "No other account shares this address, so the new owner is unknown.",
         "The directory can be incomplete, so absence does not prove closure.", money(account) + "."],
        [{"op": "patch_account", "account_id": account["account_id"],
          "fields": {"status": config.STATUS_REVIEW, "note": note}}],
        guard_for(account))
