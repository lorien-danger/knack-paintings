"""
Step 1: list candidate paintings for each painter in painters.txt (English Wikipedia titles), from Wikidata (CC0).
For each painter: their Wikidata item, death date, and up to 20 of their paintings (P170 creator; instance of
painting, fresco, hanging scroll, handscroll, icon, folding screen...) that have a picture on Commons, most widely
written about first (wikipedias: how many Wikipedia articles a painting has, a measure of how well known it is).
Writes candidates.tsv. paintings.tsv (the chosen ones) was picked from it by hand.
  python candidates.py
"""
import csv, json, sys, time, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = {'User-Agent': 'knack-paintings-build/1.0 (dataset build script; https://github.com/lorien-danger/knack-paintings)'}
KINDS = 'wd:Q3305213 wd:Q134194 wd:Q2647254 wd:Q1440894 wd:Q132137 wd:Q1366988 wd:Q11060274 wd:Q1278452'


def get(url, params, tries=5):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url + '?' + urllib.parse.urlencode(params), headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
        except Exception as e:
            print('retry', url, e, file=sys.stderr, flush=True); time.sleep(3 + 5 * attempt)
    raise RuntimeError(url)


def sparql(q):
    return get('https://query.wikidata.org/sparql', {'query': q, 'format': 'json'})['results']['bindings']


def entities(ids, props='labels|claims|sitelinks'):
    out = {}
    for s in range(0, len(ids), 50):
        out.update(get('https://www.wikidata.org/w/api.php', {'action': 'wbgetentities', 'ids': '|'.join(ids[s:s + 50]),
                                                              'props': props, 'languages': 'en', 'format': 'json'})['entities'])
    return out


def qid_of(title):
    r = get('https://en.wikipedia.org/w/api.php', {'action': 'query', 'titles': title, 'prop': 'pageprops', 'redirects': 1, 'format': 'json'})
    for p in r['query']['pages'].values(): return p.get('pageprops', {}).get('wikibase_item')


if __name__ == '__main__':
    out = []
    for name in [l.strip() for l in open(HERE / 'painters.txt') if l.strip()]:
        q = qid_of(name)
        rows = sparql(f'''SELECT DISTINCT ?p ?links WHERE {{
          VALUES ?kind {{ {KINDS} }}
          ?p wdt:P170 wd:{q}; wdt:P31 ?kind; wikibase:sitelinks ?links.
        }} ORDER BY DESC(?links) LIMIT 20''')
        ids = [b['p']['value'].rsplit('/', 1)[1] for b in rows]
        ents = entities(ids + [q], 'labels|claims')
        death = ''
        for c in ents.get(q, {}).get('claims', {}).get('P570', []):
            death = c['mainsnak'].get('datavalue', {}).get('value', {}).get('time', '')[1:11]
        print(name, q, death, len(rows), file=sys.stderr, flush=True)
        for b, i in zip(rows, ids):
            e = ents[i]; cl = e.get('claims', {})
            if 'P18' not in cl: continue
            out.append([name, q, death, i, b['links']['value'], len(cl.get('P170', [])), e.get('labels', {}).get('en', {}).get('value', '')])
        time.sleep(0.5)
    with open(HERE / 'candidates.tsv', 'w', newline='') as f:
        w = csv.writer(f, delimiter='\t'); w.writerow(['painter', 'painter_qid', 'died', 'qid', 'wikipedias', 'creators', 'label']); w.writerows(out)
