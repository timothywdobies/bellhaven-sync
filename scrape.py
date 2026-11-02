"""Reads every Bellhaven community from the website.

The directory alone is not enough: it lists 34 of 35 communities. So we also collect
community links from the homepage and About page, then open each community's own page,
which is the only place the street, zip, phone, and administrator appear.
"""
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import config

LINK = re.compile(r"^/communities/[^/?#]+$")
CITY_LINE = re.compile(r"^(.+),\s*([A-Z]{2})\s+(\d{5})$")


class ScrapeError(Exception):
    pass


def fetch(path):
    response = requests.get(config.SITE_BASE_URL + path, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def community_links(soup):
    return {a["href"] for a in soup.find_all("a", href=True) if LINK.match(a["href"])}


def collect_links():
    """Return (all links, directory links, count stated by the directory, count stated on home)."""
    directory, page, stated = set(), 1, None
    while True:
        soup = fetch(f"/communities?page={page}")
        found = community_links(soup)
        match = re.search(r"(\d+) communities listed", soup.get_text())
        stated = int(match.group(1)) if match else stated
        directory |= found
        # An out-of-range page still returns 200, so stop on the missing Next link.
        if not found or not soup.find("a", string=re.compile("Next")):
            break
        page += 1

    extra, home_stated = set(), None
    for path in config.EXTRA_LINK_PAGES:
        soup = fetch(path)
        extra |= community_links(soup)
        match = re.search(r"serve (\d+) communities", soup.get_text())
        home_stated = int(match.group(1)) if match else home_stated
    return directory | extra, directory, stated, home_stated


def parse_community(path, soup, in_directory):
    fields = {}
    for dt in soup.select("dl.detail dt"):
        fields[dt.get_text(strip=True)] = dt.find_next_sibling("dd")
    try:
        street, city_line = list(fields["Address"].stripped_strings)
        city, state, zip_code = CITY_LINE.match(city_line).groups()
        return {
            "slug": path.rsplit("/", 1)[-1],
            "url": config.SITE_BASE_URL + path,
            "name": soup.find("h1").get_text(strip=True),
            "street": street,
            "city": city,
            "state": state,
            "zip": zip_code,
            "care_offerings": [b.get_text(strip=True) for b in fields["Care Offerings"].select(".badge")],
            "administrator": fields["Administrator"].get_text(strip=True),
            "phone": fields["Phone"].get_text(strip=True),
            "in_directory": in_directory,
        }
    except (KeyError, ValueError, AttributeError) as error:
        raise ScrapeError(f"Could not read {path}: {error!r}")


def check_complete(locations, stated, home_stated):
    """Stop the run if the scrape looks partial, so nothing is falsely called missing."""
    if not locations:
        raise ScrapeError("No communities found.")
    for label, count in (("directory", stated), ("homepage", home_stated)):
        if count is not None and len(locations) < count:
            raise ScrapeError(f"Scraped {len(locations)} but the {label} states {count}.")
    for loc in locations:
        unknown = [c for c in loc["care_offerings"] if c not in config.CARE_MAP]
        if unknown or not loc["care_offerings"]:
            raise ScrapeError(f"{loc['name']} has unmapped care offerings: {unknown}. Add to CARE_MAP.")


def scrape_all():
    links, directory, stated, home_stated = collect_links()
    locations = [parse_community(p, fetch(p), p in directory) for p in sorted(links)]
    check_complete(locations, stated, home_stated)
    return locations


if __name__ == "__main__":
    result = scrape_all()
    Path("data").mkdir(exist_ok=True)
    Path("data/site_latest.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    hidden = [loc["name"] for loc in result if not loc["in_directory"]]
    print(f"Scraped {len(result)} communities. Not in the directory: {hidden}")
