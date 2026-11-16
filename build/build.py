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
from candidates import get, entities, UA  # noqa: E402

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


def city_of(place, ents):
    """walk up P131 (located in) from a museum to the first place that is a city"""
    seen = []
    q = place
    for _ in range(6):
        e = ents.get(q) or entities([q], 'labels|claims')[q]
        ents[q] = e
        seen.append(q)
        if q != place and {qid(c) for c in claims(e, 'P31')} & CITYISH: return q
        up = claims(e, 'P131')
        if not up: break
        q = qid(up[0])
    return None


def main():
    chosen = [r for r in csv.DictReader(open(HERE / 'paintings.tsv'), delimiter='\t')]
    ids = [r['qid'] for r in chosen]
    P = entities(ids)
    # the painters, and the collections (the current one: without an end time)
    painter_of, coll_of = {}, {}
    for q in ids:
        e = P[q]
        cr = claims(e, 'P170')
        if len(cr) != 1: raise SystemExit(f'{q} {label(e)}: {len(cr)} creators')
        painter_of[q] = qid(cr[0])
        cs = [c for c in claims(e, 'P195') if 'P582' not in c.get('qualifiers', {})]
        coll_of[q] = FIXES['collection'].get(q, {}).get('qid') or (qid(cs[0]) if cs else None)
    more = entities(sorted(set(painter_of.values()) | {c for c in coll_of.values() if c}), 'labels|claims')
    rows, credits, notes = [], [], []
    files = {q: FIXES['image'].get(q, {}).get('file') or val(claims(P[q], 'P18')[0]) for q in ids}
    info = commons_info(sorted(set(files.values())))
    used = set()
    for q in ids:
        e, pe = P[q], more[painter_of[q]]
        title = FIXES['title'].get(q, {}).get('title')
        if not title:
            en = e.get('sitelinks', {}).get('enwiki', {}).get('title')
            title = re.sub(r'\s*\([^)]*\)$', '', en) if en else label(e)
        painter = FIXES['painter'].get(painter_of[q], {}).get('name') or label(pe)
        died = int(val(claims(pe, 'P570')[0])['time'][1:5])
        y, span = year(e)
        if q in FIXES['year']: y, span = FIXES['year'][q]['year'], FIXES['year'][q].get('range')
        if y is None: raise SystemExit(f'{q} {title}: no year')
        if died >= 1956 or y >= 1931: raise SystemExit(f'{q} {title}: painter died {died}, painted {y}: not public domain everywhere we need')
        if span: notes.append((title, span, y))
        mv = FIXES['movement'].get(q, {}).get('movement')
        if not mv:
            m = claims(e, 'P135') or claims(pe, 'P135')
            if not m: raise SystemExit(f'{q} {title}: no movement')
            mq = qid(m[0])
            mv = label(more.get(mq) or entities([mq], 'labels')[mq])
        if mv not in MOVEMENTS: raise SystemExit(f'{q} {title} ({painter}): movement {mv!r} is not in movements.json')
        cq = coll_of[q]
        if not cq: raise SystemExit(f'{q} {title}: no collection')
        ce = more.get(cq) or entities([cq], 'labels|claims')[cq]
        more[cq] = ce
        museum = FIXES['collection'].get(q, {}).get('name') or FIXES['museum'].get(cq, {}).get('name') or label(ce)
        loc = FIXES['museum'].get(cq, {}).get('location')
        if not loc:
            co = claims(ce, 'P625')
            if not co: raise SystemExit(f'{q} {title}: {museum} ({cq}) has no coordinates')
            loc = [round(val(co[0])['latitude'], 4), round(val(co[0])['longitude'], 4)]
        city = FIXES['museum'].get(cq, {}).get('city')
        if not city:
            cityq = city_of(cq, more)
            if not cityq: raise SystemExit(f'{q} {title}: {museum} ({cq}): no city found')
            city = label(more[cityq])
        rid = slug(title)
        if rid in used: rid = slug(f'{title} {painter.split()[-1]}')
        used.add(rid)
        f = files[q]; i = info[f]
        im = Image.open(io.BytesIO(fetch(i['thumb'], CACHE / 'img' / f'{q}.jpg'))).convert('RGB')
        im.thumbnail((SIZE, SIZE), Image.LANCZOS)
        (OUT / 'media').mkdir(parents=True, exist_ok=True)
        im.save(OUT / 'media' / f'{rid}.jpg', quality=82, optimize=True, progressive=True)
        rows.append({'id': rid, 'picture': f'media/{rid}.jpg', 'title': title, 'painter': painter, 'year': y,
                     'movement': MOVEMENTS[mv], 'museum': museum, 'city': city, 'location': loc})
        credits.append([rid, q, f, i['page'], i['licence'], i['artist'], i['credit']])
        print(rid, '|', painter, '|', y, '|', ' › '.join(MOVEMENTS[mv]), '|', museum, '|', city, flush=True)
    with open(OUT / 'rows.jsonl', 'w') as fo:
        for r in rows: fo.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(HERE / 'credits.tsv', 'w', newline='') as fo:
        w = csv.writer(fo, delimiter='\t')
        w.writerow(['id', 'wikidata', 'commons file', 'commons page', 'licence', 'artist', 'credit']); w.writerows(credits)
    with open(HERE / 'ranges.tsv', 'w', newline='') as fo:
        w = csv.writer(fo, delimiter='\t'); w.writerow(['title', 'dated', 'year given']); w.writerows(notes)
    print(len(rows), 'rows', file=sys.stderr)


if __name__ == '__main__':
    main()
