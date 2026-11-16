"""
Step 2: build the dataset from the paintings chosen in paintings.tsv (Wikidata items), all facts from Wikidata (CC0)
and every picture from Wikimedia Commons.
  python build.py [out folder]          (default ..; needs pillow)

For each painting: its English title (the English Wikipedia article's title without a "(Monet)"-style
disambiguation, else its English label), its painter, the year (inception, P571; a range or a date only known to
the decade or century is given as the middle of it, see year()), its movement (P135 of the painting, else of the
painter; placed in a tree by MOVEMENTS below), the collection that holds it (P195, the current one), the city that
collection is in (P131, walked up to the city) and where it is (P625 of the collection). Its picture is Commons'
P18 file, scaled to fit 640x640 and saved as media/<id>.jpg; credits.tsv lists every file with its author and licence
as Commons gives them.

Every painter must have died before 1956 and every painting must be dated before 1931, so each is in the public
domain in the countries whose terms are life + 70 years and in the United States; the build stops if one is not.
"""
import csv, io, json, re, sys, time, unicodedata, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candidates import get, entities, sparql, UA  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent
CACHE = HERE / '.cache'
SIZE = 640

# Where each movement (Wikidata's English label of the painting's or painter's P135) sits in the tree. The movement
# itself is Wikidata's; the levels above it (a part of the world, then a period or a broad school) are this
# dataset's own grouping, written here by hand.
MOVEMENTS = json.load(open(HERE / 'movements.json'))
# A few facts given here by hand where Wikidata has none or a too-fine one (each says where it came from):
FIXES = json.load(open(HERE / 'fixes.json'))

# P31 classes that count as a city when walking up P131 from a museum
CITYISH = {'Q515', 'Q1549591', 'Q1637706', 'Q5119', 'Q200250', 'Q22865', 'Q262166', 'Q484170', 'Q2039348', 'Q747074',
           'Q1093829', 'Q42744322', 'Q7930989', 'Q3957', 'Q1764511', 'Q1066984', 'Q174844', 'Q902814', 'Q1187811',
           'Q1907114', 'Q15284', 'Q1352230', 'Q1435850', 'Q133442', 'Q2264924', 'Q15661340', 'Q21507948', 'Q1749269',
           'Q3184121', 'Q13218690', 'Q1867183', 'Q1137272', 'Q5153359', 'Q2989470', 'Q3199141', 'Q1758856', 'Q4835091',
           'Q6501447', 'Q1115575', 'Q2974842', 'Q15127012', 'Q22674925', 'Q50330360', 'Q1059478', 'Q148837', 'Q2202509',
           'Q116457956', 'Q7830262', 'Q1269763', 'Q1306755', 'Q1394476', 'Q1004262'}


def claims(e, p):
    """the statements of e for p, best rank first, deprecated left out"""
    cs = [c for c in e.get('claims', {}).get(p, []) if c.get('rank') != 'deprecated' and 'datavalue' in c['mainsnak']]
    return sorted(cs, key=lambda c: c.get('rank') != 'preferred')


def val(c): return c['mainsnak']['datavalue']['value']
def qid(c): return val(c)['id']
def label(e): return e.get('labels', {}).get('en', {}).get('value')


def year_of(t):
    """a Wikidata time value: (year it is in, its first year, its last year) from its precision"""
    y = int(re.match(r'([+-]\d+)', t['time']).group(1))
    p = t['precision']
    if p >= 9: return y, y
    if p == 8: return y // 10 * 10, y // 10 * 10 + 9
    if p == 7:  # century: Wikidata writes the 16th century as 1501..1600, stored as some year in it
        c = (y - 1) // 100
        return c * 100 + 1, c * 100 + 100
    raise ValueError(t)


def year(e):
    """the year a painting was made, and how it was worked out: one year as it is; a range (two inception
    statements, or earliest / latest date qualifiers, or a date known only to the decade or century) as its middle,
    rounded down"""
    lo, hi = [], []
    for c in claims(e, 'P571'):
        a, b = year_of(val(c))
        q = c.get('qualifiers', {})
        for k, side in (('P1319', lo), ('P580', lo)):
            for x in q.get(k, []):
                if 'datavalue' in x: side.append(year_of(x['datavalue']['value'])[0])
        for k, side in (('P1326', hi), ('P582', hi)):
            for x in q.get(k, []):
                if 'datavalue' in x: side.append(year_of(x['datavalue']['value'])[1])
        lo.append(a); hi.append(b)
    if not lo: return None, None
    a, b = min(lo), max(hi)
    return (a + b) // 2, (None if a == b else f'{a}–{b}')


def slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:48].strip('-')


def commons_info(files):
    """per Commons file: a thumbnail link at SIZE and its author, licence and page"""
    out = {}
    for s in range(0, len(files), 40):
        r = get('https://commons.wikimedia.org/w/api.php', {'action': 'query', 'titles': '|'.join('File:' + f for f in files[s:s + 40]),
                                                            'prop': 'imageinfo', 'iiprop': 'url|size|extmetadata', 'iiurlwidth': SIZE,
                                                            'iiurlheight': SIZE, 'format': 'json', 'formatversion': 2})
        norm = {n['to']: n['from'] for n in r['query'].get('normalized', [])}
        for p in r['query']['pages']:
            ii = p['imageinfo'][0]; m = ii.get('extmetadata', {})
            g = lambda k: re.sub(r'<[^>]+>', '', m.get(k, {}).get('value', '')).strip()
            out[norm.get(p['title'], p['title'])[5:]] = {'thumb': ii['thumburl'], 'page': ii['descriptionurl'], 'width': ii['width'], 'height': ii['height'],
                                                     'licence': g('LicenseShortName'), 'artist': g('Artist'), 'credit': g('Credit')}
    return out


def fetch(url, path):
    if path.exists(): return path.read_bytes()
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r: b = r.read()
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b); time.sleep(1)
            return b
        except Exception as e:
            print('retry', url, e, file=sys.stderr, flush=True); time.sleep(10 + 20 * attempt)
    raise RuntimeError(url)


def chain(place, ents):
    """a place and the places it is in (P131, located in), nearest first"""
    out, q = [], place
    while q and q not in out and len(out) < 8:
        if q not in ents: ents.update(entities([q], 'labels|claims|sitelinks'))
        out.append(q)
        up = claims(ents[q], 'P131')
        q = qid(up[0]) if up else None
    return out


def kinds(items, cls):
    """which of these items are a kind of cls (P31/P279*)"""
    found = set()
    for s in range(0, len(items), 100):
        vs = ' '.join('wd:' + i for i in items[s:s + 100])
        found |= {b['i']['value'].rsplit('/', 1)[1] for b in sparql(f'SELECT DISTINCT ?i WHERE {{ VALUES ?i {{ {vs} }} ?i wdt:P31/wdt:P279* wd:{cls}. }}')}
    return found


def infobox_years(title):
    """the years in the English Wikipedia article's infobox 'year' field (c. 1503–1519 gives 1503, 1519)"""
    if not title: return None, None
    r = get('https://en.wikipedia.org/w/api.php', {'action': 'query', 'titles': title, 'prop': 'revisions', 'rvprop': 'content',
                                                   'rvslots': 'main', 'format': 'json', 'formatversion': 2, 'redirects': 1})
    p = r['query']['pages'][0]
    if 'revisions' not in p: return None, None
    m = re.search(r'\|\s*year\s*=\s*(.*?)\n\s*(?:\||\}\})', p['revisions'][0]['slots']['main']['content'], re.S)
    if not m: return None, None
    text = re.sub(r'<ref.*?(/>|</ref>)|<!--.*?-->', '', m.group(1), flags=re.S)
    text = re.sub(r'\{\{(?:circa|c\.|ca)\|', 'c. ', text, flags=re.I)
    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text).replace('{{', '').replace('}}', '').replace('|', ' ').strip()
    # the first year, or the first range (1503–1506, 1610–11) when the text starts with one
    m = re.search(r'\b(1\d{3}|[5-9]\d{2})\b(?:\s*(?:–|-|—|to)\s*(\d{2,4})\b)?', text)
    if not m: return None, text
    a = int(m.group(1)); b = m.group(2)
    b = a if not b else int(str(a)[:len(str(a)) - len(b)] + b)
    return (a, b), text


def name_of(e):
    """a usual English name: the English label, else the English Wikipedia title without a disambiguation"""
    en = e.get('sitelinks', {}).get('enwiki', {}).get('title')
    return label(e) or (re.sub(r'\s*\([^)]*\)$', '', en) if en else None)


def pick_collection(e):
    """the collection that holds a painting now: of its current collections (P195 without an end time), the one
    that is also its location (P276), else the first that has coordinates, else the first"""
    cs = [qid(c) for c in claims(e, 'P195') if 'P582' not in c.get('qualifiers', {})]
    at = {qid(c) for c in claims(e, 'P276')}
    for c in cs:
        if c in at: return c
    return cs


def main():
    chosen = [r for r in csv.DictReader(open(HERE / 'paintings.tsv'), delimiter='\t')]
    ids = [r['qid'] for r in chosen]
    P = entities(ids)
    painter_of, coll_of = {}, {}
    for q in ids:
        cr = claims(P[q], 'P170')
        if len(cr) != 1: raise SystemExit(f'{q} {label(P[q])}: {len(cr)} creators')
        painter_of[q] = qid(cr[0])
        fix = FIXES['collection'].get(q)
        coll_of[q] = fix['use'] if fix else pick_collection(P[q])
    more = entities(sorted(set(painter_of.values())), 'labels|claims|sitelinks')
    flat = {c for v in coll_of.values() for c in ([v] if isinstance(v, str) else v)}
    more.update(entities(sorted(flat), 'labels|claims|sitelinks'))
    for q, v in coll_of.items():
        if isinstance(v, list):
            if not v: raise SystemExit(f'{q} {label(P[q])}: no current collection')
            coords = [c for c in v if claims(more[c], 'P625')]
            coll_of[q] = (coords or v)[0]
    # a collection may stand for a museum it is part of (the Louvre's department of paintings is the Louvre)
    for q, c in coll_of.items():
        if c in FIXES['museum'] and 'use' in FIXES['museum'][c]: coll_of[q] = FIXES['museum'][c]['use']
    more.update(entities(sorted(set(coll_of.values()) - set(more)), 'labels|claims|sitelinks'))

    # the city each collection is in: the nearest place it is in that is a kind of city, else of municipality
    chains = {c: chain(c, more) for c in set(coll_of.values())}
    every = sorted({x for ch in chains.values() for x in ch[1:]})
    city, muni = kinds(every, 'Q515'), kinds(every, 'Q15284')
    city_of = {c: next((x for x in ch[1:] if x in city), None) or next((x for x in ch[1:] if x in muni), None) for c, ch in chains.items()}

    files = {q: FIXES['image'].get(q, {}).get('file') or val(claims(P[q], 'P18')[0]) for q in ids}
    info = commons_info(sorted(set(files.values())))
    rows, credits, years, used = [], [], [], set()
    def one(q):
        e, pe = P[q], more[painter_of[q]]
        title = FIXES['title'].get(q, {}).get('title') or name_of(e)
        painter = FIXES['painter'].get(painter_of[q], {}).get('name') or name_of(pe)
        died = int(val(claims(pe, 'P570')[0])['time'][1:5])
        # the year: Wikidata's when it is known to the year (one or a range of years); else the English Wikipedia
        # infobox's; else Wikidata's decade or century; the middle of a range, rounded down
        wd = [val(c) for c in claims(e, 'P571')]
        precise = wd and all(t['precision'] >= 9 for t in wd)
        y, span = year(e)
        how = 'Wikidata'
        if q in FIXES['year']:
            f = FIXES['year'][q]; y, span, how = f['year'], f.get('range'), f['source']
        elif not precise:
            ys, text = infobox_years(e.get('sitelinks', {}).get('enwiki', {}).get('title'))
            if ys:
                y, span, how = (ys[0] + ys[1]) // 2, (None if ys[0] == ys[1] else f'{ys[0]}–{ys[1]}'), f'English Wikipedia infobox: {text}'
            elif y is not None:
                how = 'Wikidata (to the decade or century)'
        if y is None: raise SystemExit(f'{q} {title}: no year')
        if died >= 1956 or y >= 1931: raise SystemExit(f'{q} {title}: painter died {died}, painted {y}: not public domain everywhere we need')
        years.append([title, painter, '; '.join(t['time'][1:5] + '/' + str(t['precision']) for t in wd), span or '', y, how])
        mv = (MOVEMENTS['paintings'].get(q) or MOVEMENTS['painters'].get(painter_of[q]) or [None])[0]
        if mv not in MOVEMENTS['tree']: raise SystemExit(f'{q} {title} ({painter} {painter_of[q]}): no movement')
        cq = coll_of[q]; ce = more[cq]
        mfix = FIXES['museum'].get(cq, {})
        museum = mfix.get('name') or name_of(ce)
        loc = mfix.get('location')
        if not loc:
            co = claims(ce, 'P625')
            if not co: raise SystemExit(f'{q} {title}: {museum} ({cq}) has no coordinates')
            loc = [round(val(co[0])['latitude'], 4), round(val(co[0])['longitude'], 4)]
        city = mfix.get('city')
        if not city:
            cityq = city_of[cq]
            if not cityq: raise SystemExit(f'{q} {title}: {museum} ({cq}): no city found')
            city = name_of(more[cityq])
        city = FIXES['city'].get(city, {}).get('name', city)
        museum = re.sub(r',\s*' + re.escape(city) + '$', '', museum)
        rid = slug(title)
        if rid in used: rid = slug(f'{title} {painter.split()[-1]}')
        used.add(rid)
        f = files[q]; i = info[f]
        im = Image.open(io.BytesIO(fetch(i['thumb'], CACHE / 'img' / f'{q}.jpg'))).convert('RGB')
        im.thumbnail((SIZE, SIZE), Image.LANCZOS)
        (OUT / 'media').mkdir(parents=True, exist_ok=True)
        im.save(OUT / 'media' / f'{rid}.jpg', quality=82, optimize=True, progressive=True)
        rows.append({'id': rid, 'picture': f'media/{rid}.jpg', 'title': title, 'painter': painter, 'year': y,
                     'movement': MOVEMENTS['tree'][mv], 'museum': museum, 'city': city, 'location': loc})
        credits.append([rid, q, f, i['page'], i['licence'], i['artist'], i['credit']])
        print(rid, '|', painter, '|', y, '|', ' › '.join(MOVEMENTS['tree'][mv]), '|', museum, '|', city, '|', cq, flush=True)
    errors = []
    for q in ids:
        try: one(q)
        except SystemExit as x: errors.append(str(x))
    if errors: raise SystemExit('\n'.join(errors))


    with open(OUT / 'rows.jsonl', 'w') as fo:
        for r in rows: fo.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(HERE / 'credits.tsv', 'w', newline='') as fo:
        w = csv.writer(fo, delimiter='\t')
        w.writerow(['id', 'wikidata', 'commons file', 'commons page', 'licence', 'artist', 'credit']); w.writerows(credits)
    with open(HERE / 'years.tsv', 'w', newline='') as fo:
        w = csv.writer(fo, delimiter='\t'); w.writerow(['title', 'painter', 'wikidata inception (year/precision)', 'range', 'year given', 'from']); w.writerows(years)
    print(len(rows), 'rows', file=sys.stderr)


if __name__ == '__main__':
    main()
