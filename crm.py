"""Small client for the CRM API. Reads the token from the environment or .env."""
import os
from pathlib import Path

import requests

import config


def load_env(path=".env"):
    """Copy KEY=VALUE lines from .env into the environment without overriding it."""
    p = Path(__file__).parent / path
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


class CRMError(Exception):
    def __init__(self, status, body):
        super().__init__(f"CRM API returned {status}: {body[:300]}")
        self.status = status


class CRM:
    def __init__(self):
        load_env()
        token = os.environ.get("CRM_API_TOKEN")
        if not token:
            raise SystemExit("CRM_API_TOKEN is not set. Add it to .env or the workflow secret.")
        self.base = config.CRM_BASE_URL
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.browser_base = f"{config.SITE_BASE_URL}/crm/{token}/accounts"

    def _send(self, method, path, **kwargs):
        response = self.session.request(method, self.base + path, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise CRMError(response.status_code, response.text)
        return response.json()

    def _all(self, path, **params):
        rows, page = [], 1
        while True:
            body = self._send("GET", path, params={**params, "page": page, "page_size": 50})
            rows += body["data"]
            if len(rows) >= body["total"] or not body["data"]:
                return rows
            page += 1

    def list_accounts(self, **filters):
        return self._all("/accounts", **filters)

    def list_contacts(self):
        return self._all("/contacts")

    def get_account(self, account_id):
        return self._send("GET", f"/accounts/{account_id}")

    def patch_account(self, account_id, fields):
        return self._send("PATCH", f"/accounts/{account_id}", json=fields)

    def create_account(self, fields):
        return self._send("POST", "/accounts", json=fields)

    def get_contact(self, contact_id):
        return self._send("GET", f"/contacts/{contact_id}")

    def patch_contact(self, contact_id, fields):
        return self._send("PATCH", f"/contacts/{contact_id}", json=fields)
