"""Build a public game-title catalog, never user/game-account data."""
from __future__ import annotations
import datetime
import gzip
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request

QUERY = '''SELECT DISTINCT ?item ?label ?description WHERE {
  ?item wdt:P31 wd:Q7889; rdfs:label ?label.
  FILTER(LANG(?label) = "en")
  OPTIONAL { ?item schema:description ?description. FILTER(LANG(?description) = "en") }
}'''


def main():
    url = 'https://query.wikidata.org/sparql?' + urllib.parse.urlencode({'query': QUERY, 'format': 'json'})
    request = urllib.request.Request(url, headers={
        'User-Agent': 'LunaGameCatalog/1.0 (https://github.com/despot707/chatGPT-discord-bot)',
        'Accept': 'application/sparql-results+json',
    })
    failure = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read(40_000_001)
            if len(raw) > 40_000_000:
                raise ValueError('Catalog response exceeds bounded import size')
            payload = json.loads(raw)
            break
        except Exception as exc:
            failure = exc
            if attempt == 2:
                raise RuntimeError('Public game catalog download failed') from failure
            time.sleep(5 * (attempt + 1))
    records = {}
    for row in payload['results']['bindings']:
        qid = row['item']['value'].rsplit('/', 1)[-1]
        name = row['label']['value'].strip()
        if not re.fullmatch(r'Q[1-9][0-9]*', qid) or not name or len(name) > 200:
            continue
        if any(ord(c) < 32 for c in name):
            continue
        records[qid] = {'id': 'wikidata:' + qid, 'name': name,
            'description': row.get('description', {}).get('value', '')[:180], 'aliases': []}
    if len(records) < 5000:
        raise ValueError(f'Refusing incomplete catalog: {len(records)} records')
    names = {r['name'].casefold() for r in records.values()}
    for expected in ['League of Legends', 'Super Mario Bros.', 'World of Warcraft']:
        if expected.casefold() not in names:
            raise ValueError(f'Catalog smoke check missing {expected}')
    root = pathlib.Path('assets'); root.mkdir(exist_ok=True)
    rows = sorted(records.values(), key=lambda x: x['id'])
    (root/'game_catalog.json.gz').write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
    metadata = {'source': 'Wikidata: instances of video game (Q7889), English labels and descriptions',
        'license': 'CC0', 'count': len(rows), 'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'coverage': 'Cross-platform game titles present in Wikidata. Not a guarantee of every release, edition, DLC or newly released title.'}
    (root/'game_catalog_source.json').write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
