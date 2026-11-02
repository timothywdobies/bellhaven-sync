"""Saves proposals and decisions as readable JSON files that live in the repo."""
import json
import os
from pathlib import Path

FINAL = ("approved", "rejected")


class Store:
    def __init__(self, folder="data"):
        self.folder = Path(__file__).parent / folder if not Path(folder).is_absolute() else Path(folder)
        self.folder.mkdir(exist_ok=True)

    def read(self, name, default):
        path = self.folder / name
        return json.loads(path.read_text()) if path.exists() else default

    def write(self, name, value):
        """Write to a temporary file first, so a crash cannot leave half a file."""
        path = self.folder / name
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        os.replace(temp, path)

    def decisions(self):
        return self.read("decisions.json", {})

    def save_decisions(self, value):
        self.write("decisions.json", value)

    def proposals(self):
        return self.read("proposals.json", [])

    def save_proposals(self, value):
        self.write("proposals.json", value)

    def proposals_pending(self):
        return build_queue(self.proposals(), self.decisions())


def build_queue(proposals, decisions):
    """Remove decided items. Keep half-finished ones and block anything touching them."""
    unfinished = [r for r in decisions.values() if r.get("decision") == "failed" and r.get("done", 0) > 0]
    busy_accounts = {a for r in unfinished for a in r["proposal"]["account_ids"]}
    busy_slugs = {r["proposal"]["slug"] for r in unfinished if r["proposal"]["slug"]}
    queue = [r["proposal"] for r in unfinished]
    finished_ids = {p["id"] for p in queue}
    for item in proposals:
        record = decisions.get(item["id"], {})
        if record.get("decision") in FINAL or item["id"] in finished_ids:
            continue
        if busy_accounts & set(item["account_ids"]) or item["slug"] in busy_slugs:
            continue
        queue.append(item)
    return queue
