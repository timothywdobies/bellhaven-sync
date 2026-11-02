"""Local review app. Nothing reaches the CRM unless a reviewer presses Approve."""
import datetime
from collections import Counter

from flask import Flask, flash, redirect, render_template_string, request, url_for

import apply
import config
from crm import CRM, CRMError
from store import Store, build_queue

LABELS = {"chow": "CHOW", "duplicate": "Duplicate", "duplicate_parent": "Duplicate parent",
          "field_fix": "Field fix", "missing": "Missing from website", "new": "New account",
          "reparent": "Wrong parent"}

PAGE = """<!doctype html><title>Bellhaven Review</title>
<style>
 body{font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif;margin:0;background:#f4f5f3;color:#23302b}
 header{background:#2E5D50;color:#fff;padding:14px 24px}header a{color:#fff;margin-right:18px}
 main{max-width:960px;margin:auto;padding:16px 24px 60px}
 .tabs a{display:inline-block;margin:0 6px 8px 0;padding:4px 10px;background:#fff;border-radius:14px;color:#23302b;text-decoration:none;border:1px solid #d8dcd6}
 .tabs a.on{background:#2E5D50;color:#fff}
 .card{background:#fff;border:1px solid #dfe3dd;border-radius:6px;padding:14px 18px;margin:12px 0}
 .type{font-size:12px;background:#e7efe9;padding:2px 8px;border-radius:4px}
 h3{margin:6px 0}table{border-collapse:collapse;width:100%;margin:8px 0}
 th,td{border-bottom:1px solid #eceee9;text-align:left;padding:4px 8px;vertical-align:top;font-size:14px}
 ul{margin:6px 0;padding-left:18px;font-size:14px}
 .flag{background:#fdeaea;border-left:4px solid #b3261e;padding:8px 12px;margin:8px 0;color:#7a1712}
 .msg{background:#e7efe9;padding:8px 12px;margin:8px 0;border-radius:4px}.err{background:#fdeaea}
 form{display:inline}button{padding:6px 14px;border-radius:4px;border:1px solid #888;cursor:pointer}
 .yes{background:#2E5D50;color:#fff;border-color:#2E5D50}.bar{margin-top:10px}
 input[type=text]{padding:5px;width:260px}.muted{color:#68736d;font-size:13px}
</style>
<header><b>Bellhaven Review</b> &nbsp; <a href="{{url_for('queue')}}">Queue ({{total}})</a>
<a href="{{url_for('history')}}">History</a><a href="{{url_for('confirmed')}}">Confirmed</a>
<a href="{{browser}}" target="_blank">CRM browser</a></header><main>
{% for category, message in get_flashed_messages(with_categories=true) %}
<div class="msg {{category}}">{{message}}</div>{% endfor %}
{{body|safe}}</main>"""

QUEUE = """<div class="tabs"><a class="{{'on' if not active}}" href="{{url_for('queue')}}">All {{total}}</a>
{% for kind, count in counts %}<a class="{{'on' if active==kind}}" href="{{url_for('queue',type=kind)}}">{{labels[kind]}} {{count}}</a>{% endfor %}</div>
{% if not items %}<p>Nothing is waiting for review.</p>{% endif %}
{% for p in items %}<div class="card" id="{{p.id}}">
<span class="type">{{labels[p.type]}}</span><h3>{{p.title}}</h3>
{% for f in p.flags %}<div class="flag"><b>Flag:</b> {{f}}</div>{% endfor %}
{% if p.done %}<div class="flag">An earlier attempt stopped after {{p.done}} of {{p.actions|length}} steps. Approve resumes it. {{p.error}}</div>{% endif %}
<table><tr><th>Field</th><th>Now</th><th>Proposed</th></tr>
{% for r in p.rows %}<tr><td>{{r[0]}}</td><td>{{r[1]}}</td><td>{{r[2]}}</td></tr>{% endfor %}</table>
<b>Evidence</b><ul>{% for e in p.evidence %}<li>{{e}}</li>{% endfor %}</ul>
<p class="muted">{% if p.site %}<a href="{{p.site.url}}" target="_blank">Website page</a> &nbsp;{% endif %}
{% for a in p.accounts %}<a href="{{browser}}/{{a.account_id}}" target="_blank">CRM: {{a.name}}</a> &nbsp;{% endfor %}</p>
<div class="bar"><form method="post" action="{{url_for('approve',pid=p.id)}}">
{% if p.flags %}<label><input type="checkbox" name="ack" value="1"> I reviewed the flag</label> {% endif %}
<button class="yes">Approve</button></form>
<form method="post" action="{{url_for('reject',pid=p.id)}}"> <input type="text" name="reason" placeholder="Reason for rejecting">
<button>Reject</button></form></div></div>{% endfor %}"""

HISTORY = """<h2>Decisions</h2>{% if not rows %}<p>No decisions yet.</p>{% endif %}
{% for pid, r in rows %}<div class="card"><span class="type">{{r.decision}}</span> <b>{{r.proposal.title}}</b>
<div class="muted">{{r.decided_at}} by {{r.reviewer}}{% if r.reason %}. Reason: {{r.reason}}{% endif %}</div>
{% if r.error %}<div class="flag">{{r.error}}</div>{% endif %}
<ul>{% for line in r.log %}<li>{{line}}</li>{% endfor %}</ul>
{% if r.decision == 'rejected' %}<form method="post" action="{{url_for('reopen',pid=pid)}}"><button>Reopen</button></form>{% endif %}
</div>{% endfor %}"""

CONFIRMED = """<h2>Confirmed with no change</h2>{% for c in rows %}<div class="card"><b>{{c.name}}</b>
<a class="muted" href="{{browser}}/{{c.account_id}}" target="_blank">CRM record</a>
<ul>{% for e in c.evidence %}<li>{{e}}</li>{% endfor %}</ul></div>{% endfor %}"""


def rows_for(item):
    """Build the Now and Proposed table a reviewer sees."""
    rows = []
    for action in item["actions"]:
        if action["op"] == "create_account":
            rows.append(("New account", "does not exist", "will be created"))
            rows += [(k, "", v) for k, v in action["fields"].items() if v and k != "parent_id"]
            rows.append(("parent", "", "Bellhaven"))
        elif action["op"] == "patch_contact":
            rows.append((f"contact {action['contact_name']}", "on this account", "moves to survivor"))
        else:
            current = item["guards"].get(action["account_id"], {})
            for key, value in action["fields"].items():
                shown = "new account id" if value == "$new" else value
                if key == "parent_id":
                    now = next((a["parent_name"] for a in item["accounts"] if a["account_id"] == action["account_id"]), "")
                    rows.append(("parent", now or "blank", "Bellhaven"))
                else:
                    rows.append((key, current.get(key, ""), shown))
            if "fallback_fields" in action:
                rows.append(("if two care types are refused", "", action["fallback_fields"]["care_type"]))
    return rows


def create_app(crm=None, store=None):
    app = Flask(__name__)
    app.secret_key = "local-review-app"
    crm = crm or CRM()
    store = store or Store()
    browser = getattr(crm, "browser_base", "#")

    def waiting():
        decisions = store.decisions()
        items = build_queue(store.proposals(), decisions)
        for item in items:
            record = decisions.get(item["id"], {})
            item.update(rows=rows_for(item), done=record.get("done", 0), error=record.get("error", ""))
        return items

    def show(template, **context):
        total = len(waiting())
        context.update(labels=LABELS, browser=browser, total=total)
        body = render_template_string(template, **context)
        return render_template_string(PAGE, body=body, browser=browser, total=total)

    def stamp(record, decision):
        record.update(decision=decision, reviewer=config.REVIEWER,
                      decided_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

    @app.get("/")
    def queue():
        items, active = waiting(), request.args.get("type")
        counts = sorted(Counter(i["type"] for i in items).items())
        shown = [i for i in items if i["type"] == active] if active else items
        return show(QUEUE, items=shown, counts=counts, active=active)

    @app.post("/approve/<pid>")
    def approve(pid):
        item = next((i for i in waiting() if i["id"] == pid), None)
        if not item:
            flash("That proposal is no longer waiting.", "err")
            return redirect(url_for("queue"))
        if item["flags"] and request.form.get("ack") != "1":
            flash("This item has a flag. Tick the box to confirm you reviewed it.", "err")
            return redirect(url_for("queue") + f"#{pid}")

        decisions = store.decisions()
        record = decisions.get(pid, {})
        record.update(proposal={k: v for k, v in item.items() if k not in ("rows", "done", "error")}, error="")

        def save(current):
            stamp(current, "failed")
            decisions[pid] = current
            store.save_decisions(decisions)

        try:
            apply.run(crm, record["proposal"], record, save)
            stamp(record, "approved")
            flash(f"Approved and written to the CRM: {item['title']}", "")
        except (apply.StaleProposal, apply.WriteMismatch, CRMError, OSError) as error:
            stamp(record, "failed")
            record["error"] = str(error)
            flash(f"Not finished: {error}", "err")
        decisions[pid] = record
        store.save_decisions(decisions)
        return redirect(url_for("queue"))

    @app.post("/reject/<pid>")
    def reject(pid):
        item = next((i for i in waiting() if i["id"] == pid), None)
        if item:
            decisions = store.decisions()
            record = {"proposal": {k: v for k, v in item.items() if k not in ("rows", "done", "error")},
                      "reason": request.form.get("reason", ""), "log": []}
            stamp(record, "rejected")
            decisions[pid] = record
            store.save_decisions(decisions)
            flash(f"Rejected, and nothing was written: {item['title']}", "")
        return redirect(url_for("queue"))

    @app.post("/reopen/<pid>")
    def reopen(pid):
        decisions = store.decisions()
        record = decisions.get(pid)
        if record and record["decision"] == "rejected":
            proposals = store.proposals()
            if all(p["id"] != pid for p in proposals):
                proposals.append(record["proposal"])
                store.save_proposals(proposals)
            del decisions[pid]
            store.save_decisions(decisions)
            flash("Reopened. It is back in the queue.", "")
        return redirect(url_for("history"))

    @app.get("/history")
    def history():
        rows = sorted(store.decisions().items(), key=lambda kv: kv[1]["decided_at"], reverse=True)
        return show(HISTORY, rows=rows)

    @app.get("/confirmed")
    def confirmed():
        return show(CONFIRMED, rows=store.read("confirmed.json", []))

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5050)
