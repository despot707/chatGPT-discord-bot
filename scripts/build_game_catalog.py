"""Build a bounded, paginated catalog of public CC0 video-game records."""
from __future__ import annotations

import datetime
import gzip
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request

PAGE_SIZE = 2000


def get_page(offset):
    query = '''PREFIX hint: <http://www.bigdata.com/queryHints#>
SELECT ?item ?label ?description WHERE {
 hint:Query hint:optimizer "None".
 { SELECT ?item WHERE { ?item wdt:P31 wd:Q7889. } ORDER BY ?item LIMIT %d OFFSET %d }
 OPTIONAL { ?item rdfs:label ?label. FILTER(LANG(?label) = "en") }
 OPTIONAL { ?item schema:description ?description. FILTER(LANG(?description) = "en") }
}''' % (PAGE_SIZE, offset)
    url = 'https://query.wikidata.org/sparql?' + urllib.parse.urlencode({'query': query, 'format': 'json'})
    request = urllib.request.Request(url, headers={'User-Agent': 'LunaGameCatalog/1.0 (https://github.com/despot707/chatGPT-discord-bot)', 'Accept': 'application/sparql-results+json'})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=65) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('Catalog page too large')
            return json.loads(raw)['results']['bindings']
        except Exception:
            if attempt:
                raise
            time.sleep(3)


def main():
    records = {}
    for offset in range(0, 500000, PAGE_SIZE):
        page = get_page(offset)
        for row in page:
            qid = row['item']['value'].rsplit('/', 1)[-1]
            name = row.get('label', {}).get('value', '').strip()
            if not re.fullmatch(r'Q[1-9][0-9]*', qid) or not name or len(name) > 200 or any(ord(c) < 32 for c in name):
                continue
            records[qid] = {'id': 'wikidata:' + qid, 'name': name, 'description': row.get('description', {}).get('value', '')[:180], 'aliases': []}
        print('catalog page', offset, 'rows', len(page), 'titles', len(records), flush=True)
        if len(page) < PAGE_SIZE:
            break
        time.sleep(0.4)
    else:
        raise ValueError('Import limit reached before completing catalog')
    if len(records) < 5000:
        raise ValueError('Incomplete catalog')
    names = {r['name'].casefold() for r in records.values()}
    for expected in ['League of Legends', 'Super Mario Bros.', 'World of Warcraft']:
        if expected.casefold() not in names:
            raise ValueError('Missing catalog smoke title: ' + expected)
    root = pathlib.Path('assets')
    root.mkdir(exist_ok=True)
    rows = sorted(records.values(), key=lambda x: x['id'])
    (root / 'game_catalog.json.gz').write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
    metadata = {'source': 'Wikidata: instances of video game (Q7889), English labels and descriptions', 'license': 'CC0', 'count': len(rows), 'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'coverage': 'Cross-platform game titles present in Wikidata, not a guarantee of every release, edition, DLC or new title.'}
    (root / 'game_catalog_source.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
