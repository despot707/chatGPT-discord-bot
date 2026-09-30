"""Build a bounded, paginated CC0 game catalog with multilingual-label fallback."""

from __future__ import annotations

import datetime
import gzip
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PAGE_SIZE = 2000


def get_page(offset):
    query = """PREFIX hint: <http://www.bigdata.com/queryHints#>
SELECT ?item ?label ?description WHERE {
 hint:Query hint:optimizer "None".
 { SELECT ?item WHERE { ?item wdt:P31 wd:Q7889. } ORDER BY ?item LIMIT %d OFFSET %d }
 OPTIONAL { ?item rdfs:label ?english. FILTER(LANG(?english) = "en") }
 OPTIONAL { ?item rdfs:label ?multilingual. FILTER(LANG(?multilingual) = "mul") }
 BIND(COALESCE(?english, ?multilingual) AS ?label)
 OPTIONAL { ?item schema:description ?description. FILTER(LANG(?description) = "en") }
}""" % (PAGE_SIZE, offset)
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode(
        {"query": query, "format": "json"}
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "LunaGameCatalog/1.0 (https://github.com/despot707/chatGPT-discord-bot)",
            "Accept": "application/sparql-results+json",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=65) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError("Catalog page too large")
            return json.loads(raw)["results"]["bindings"]
        except urllib.error.HTTPError as exc:
            if attempt == 2:
                raise
            delay = exc.headers.get("Retry-After", "5")
            time.sleep(min(60, int(delay)) if str(delay).isdigit() else 5)
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(5)
    raise RuntimeError("Catalog retrieval exhausted")


def main():
    records = {}
    complete = False
    # At most two upstream requests concurrently; bounded batches and retries.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for offset in range(0, 500000, PAGE_SIZE * 2):
            pages = list(pool.map(get_page, (offset, offset + PAGE_SIZE)))
            for page in pages:
                for row in page:
                    qid = row["item"]["value"].rsplit("/", 1)[-1]
                    name = row.get("label", {}).get("value", "").strip()
                    if (
                        not re.fullmatch(r"Q[1-9][0-9]*", qid)
                        or not name
                        or len(name) > 200
                        or any(ord(c) < 32 for c in name)
                    ):
                        continue
                    records[qid] = {
                        "id": "wikidata:" + qid,
                        "name": name,
                        "description": row.get("description", {}).get("value", "")[:180],
                        "aliases": [],
                    }
                if len(page) < PAGE_SIZE:
                    complete = True
            print("catalog batch", offset, "titles", len(records), flush=True)
            if complete:
                break
            time.sleep(0.5)
    if not complete or len(records) < 5000:
        raise ValueError("Incomplete catalog")
    names = {r["name"].casefold() for r in records.values()}
    for expected in ["League of Legends", "Super Mario Bros.", "World of Warcraft"]:
        if expected.casefold() not in names:
            raise ValueError("Missing catalog smoke title: " + expected)
    root = pathlib.Path("assets")
    root.mkdir(exist_ok=True)
    rows = sorted(records.values(), key=lambda x: x["id"])
    (root / "game_catalog.json.gz").write_bytes(
        gzip.compress(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)
    )
    metadata = {
        "source": "Wikidata: instances of video game (Q7889), English labels with multilingual fallback and English descriptions",
        "license": "CC0",
        "count": len(rows),
        "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "coverage": "Cross-platform game titles present in Wikidata, not a guarantee of every release, edition, DLC or new title.",
    }
    (root / "game_catalog_source.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
