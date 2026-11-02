"""Every business rule, threshold, and mapping lives here.

Change a value, re-run the pipeline, and the new rule applies.
"""

# --- Where things are (the token is never here, it comes from .env or a secret)
SITE_BASE_URL = "https://analyst-assessment-production.up.railway.app"
CRM_BASE_URL = SITE_BASE_URL + "/api/v1"

# --- The operator we are syncing
PARENT_KEYWORD = "bellhaven"  # A parent account with this word in its name is the operator.

# --- Website pages that may link to a community outside the directory
EXTRA_LINK_PAGES = ["/", "/about"]

# --- Website care label -> CRM care type
CARE_MAP = {
    "Assisted Living": "Assisted Living",
    "Memory Support": "Memory Care",
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
}
CARE_SEPARATOR = "; "       # How two care types are stored if the API accepts them.
CARE_EXTRA_NOTE = "Also offers: "  # Note text if the API accepts only one care type.

# --- Address spelling. Both sides are converted to these short forms before comparing.
STREET_WORDS = {
    "street": "st", "road": "rd", "avenue": "ave", "drive": "dr", "lane": "ln",
    "boulevard": "blvd", "pk": "pike", "north": "n", "south": "s", "east": "e",
    "west": "w", "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
}

# --- Matching thresholds (name similarity runs from 0.0 to 1.0)
FALLBACK_NAME_MIN = 0.85   # Different street: same city, this name score, and a phone or admin match.
NEAR_MISS_NAME_MIN = 0.60  # Shown as evidence on New proposals. Never used to match.

# --- Name fixes
REBRAND_BELOW = 0.80       # Name score under this is a rebrand and gets the note below.
FORMERLY_NOTE = "Formerly {old_name}"

# --- CHOW billing SOP: must exceed BOTH numbers to require a CHOW instead of a re-parent.
CHOW_MIN_REVENUE = 0
CHOW_MIN_AR = 0

# --- Who is recorded as the approver
REVIEWER = "Tim Dobies"

# --- Status values
STATUS_ACTIVE = "Active"
STATUS_INACTIVE = "Inactive"
STATUS_REVIEW = "Needs Review"

# --- Field updates that are optional
UPDATE_PHONE = False       # False: phones are match evidence only.

# --- Note text
PO_BOX_NOTE = "Previous billing street: {old_street}"
MISSING_NOTE = "Not on the Bellhaven website. No new owner found. Confirm status."
DUPLICATE_NOTE = "Duplicate of {survivor_id}"
CHOW_NEW_NOTE = "Created by CHOW from account {old_id}"

# --- Safety stop: if this share of accounts vanish from the site, assume the scrape broke.
MAX_MISSING_SHARE = 0.25
