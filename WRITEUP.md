# Writeup

**Matching approach.** I matched on normalized street and city, because a facility's address outlasts its name. Bellhaven rebrands and acquisitions make names unreliable, and one name appears in Colorado for an unrelated facility. A stricter fallback needs the same city, a strong name score, and a phone or administrator match. Name alone never matches. The directory hid one community, so the scraper also follows homepage links and stops if it finds fewer locations than the site claims.

**Judgment calls.** Marietta and Tiffin moved to Bellhaven with revenue and outstanding AR, so per the billing SOP I left the old accounts untouched, created new accounts under Bellhaven, and linked them. Lima, Findlay, Zanesville, and Kettering had no AR, so I re-parented them directly. For duplicates, the surviving account is the one with billing history, then the most contacts, then the most complete record. Sandusky was the hardest call. It has revenue and AR, and a Millstone account already sits at its address. I linked the old account to that Millstone account instead of creating a duplicate. Alliance and Coldwater went to Needs Review, because absence from a website does not prove closure.

**How I used AI.** I directed Claude Code through the build. I set the rules, chose the priorities, and reviewed each proposal. I did not trust its output without checks. A hand count of the data exposed an early bug that made 27 locations look new. A separate verification script, which does not use the matcher, checks the CRM end state against a snapshot from before any writes. Tests also break a correct end state on purpose to prove the checks catch errors.

**Result.** I approved all 30 proposals after review. The verifier passed 11 of 11 checks on the live CRM, and a second run proposed nothing new.

**What I'd build next.** Slack alerts for new proposals, administrator sync into contacts, and the same pipeline across all parent companies.
