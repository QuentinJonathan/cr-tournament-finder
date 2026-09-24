"""Live, read-only probe: which API request reports a tournament start first?

Polls one preparing tournament through several request variants in parallel and
writes every response (status, startedTime, players, cache headers, response
fingerprint) to .runtime/probe-<tag>-<time>.jsonl. Notifications are unaffected.

  proxy-detail   the production watcher's request (baseline)
  proxy-search   targeted name search through the RoyaleAPI proxy
  *-b, *-c       the same data under other URLs, first requested at staggered
                 times: shows whether every URL runs its own cache cycle
  direct-*       official API without the proxy; needs CR_DIRECT_API_KEY, a key
                 whitelisted for this machine's public IP

Start it while the tournament is still preparing, ideally 3+ minutes before the
start. It stops by itself once every source has reported the start.

  .venv/bin/python scripts/probe_start_latency.py --live '#2CV0Q99G'
  .venv/bin/python scripts/probe_start_latency.py --analyze .runtime/probe-2CV0Q99G-<time>.jsonl
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app  # noqa: E402
from watch import fingerprint, normalize_tag  # noqa: E402

DIRECT_BASE = 'https://api.clashroyale.com/v1'
CACHE_HEADERS = ('cache-control', 'age', 'date', 'expires', 'etag', 'last-modified',
                 'vary', 'via', 'x-cache', 'cf-cache-status', 'server')
STARTED = ('inProgress', 'ended')
TIMEOUT = aiohttp.ClientTimeout(total=8)


def build_variants(tag, query, direct=False, stagger=30):
    """Every variant is a distinct URL; later offsets spread their cache cycles."""
    detail = '/tournaments/' + quote(tag, safe='')
    variants = [('proxy-detail', 'proxy', detail, {}, 0),
                ('proxy-detail-b', 'proxy', detail, {'probe': 'b'}, 2 * stagger)]
    if query:
        # The API ignores `limit` (fixed 20-item cap), but it makes the URL unique.
        variants += [('proxy-search', 'proxy', '/tournaments', {'name': query}, 0),
                     ('proxy-search-b', 'proxy', '/tournaments', {'name': query, 'limit': 21}, stagger),
                     ('proxy-search-c', 'proxy', '/tournaments', {'name': query, 'limit': 22}, 3 * stagger)]
    if direct:
        variants.append(('direct-detail', 'direct', detail, {}, 0))
        if query:
            variants.append(('direct-search', 'direct', '/tournaments', {'name': query}, 0))
    return [dict(zip(('name', 'base', 'path', 'params', 'offset'), v)) for v in variants]


def parse_max_age(cache_control):
    match = re.search(r'max-age=(\d+)', cache_control or '')
    return int(match.group(1)) if match else None


def parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_tournament(record, body, tag, last_fingerprint=None):
    """Fill the record from a 200 body; search results are matched by tag."""
    tournament = body
    if isinstance(body, dict) and isinstance(body.get('items'), list):
        record['results'] = len(body['items'])
        tournament = next((t for t in body['items'] if isinstance(t, dict) and t.get('tag') == tag), None)
    record['found'] = isinstance(tournament, dict)
    if not record['found']:
        return
    record.update(status=tournament.get('status'), startedTime=tournament.get('startedTime'),
                  capacity=tournament.get('capacity'), maxCapacity=tournament.get('maxCapacity'),
                  fingerprint=fingerprint(tournament))
    if record['fingerprint'] != last_fingerprint:
        # Full snapshot whenever the answer changed; members are counted, not copied.
        record['tournament'] = {k: v for k, v in tournament.items() if k != 'membersList'}
        record['members'] = len(tournament.get('membersList') or [])


async def observe(session, base_url, headers, variant, tag, last_fingerprint=None):
    record = {'variant': variant['name'], 'path': variant['path'], 'params': variant['params'],
              'requestedAt': time.time()}
    try:
        async with session.get(base_url + variant['path'], params=variant['params'] or None,
                               headers=headers, timeout=TIMEOUT) as response:
            record.update(httpStatus=response.status,
                          headers={k: response.headers[k] for k in CACHE_HEADERS if k in response.headers},
                          headerNames=sorted({k.lower() for k in response.headers}),
                          maxAge=parse_max_age(response.headers.get('Cache-Control')),
                          ageHeader=parse_int(response.headers.get('Age')))
            if response.status == 200:
                read_tournament(record, await response.json(content_type=None), tag, last_fingerprint)
    except Exception as exc:
        record['error'] = type(exc).__name__  # never the message: it can contain the URL
    record['respondedAt'] = time.time()
    return record


async def choose_query(session, headers, tag, name):
    """The name word (or full name) whose search finds the tag among the fewest results."""
    words = str(name or '').split()
    candidates = sorted(set(words) | ({' '.join(words)} if words else set()), key=len, reverse=True)[:6]
    best = None
    for query in candidates:
        record = await observe(session, app.API_BASE, headers,
                               {'name': 'setup', 'path': '/tournaments', 'params': {'name': query}}, tag)
        if record.get('found') and (best is None or record['results'] < best[1]):
            best = (query, record['results'])
    return best[0] if best else None


def clock(ts):
    return datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '—'


def crtime(value):
    parsed = app.parse_cr_time(value) if value else None
    return parsed.timestamp() if parsed else None


def problem_of(record):
    if record.get('error'):
        return record['error']
    if record.get('httpStatus') != 200:
        return f"HTTP {record.get('httpStatus')}"
    return None if record.get('found') else 'not in search results'


def describe_change(previous, record):
    """One console line for noteworthy changes, None otherwise."""
    problem = problem_of(record)
    if problem:
        return None if previous and problem_of(previous) == problem else problem
    state = 'LIVE' if record.get('status') in STARTED else 'PREP'
    players = f"players {record.get('capacity')}/{record.get('maxCapacity')}"
    if not previous or problem_of(previous):
        return f"{state} · {players} · max-age {record.get('maxAge')} " + \
               ('(answering again)' if previous else '(first answer)')
    parts = []
    if record.get('status') != previous.get('status'):
        started = crtime(record.get('startedTime'))
        parts.append(state + (f" · startedTime {clock(started)} (+{record['respondedAt'] - started:.0f}s)"
                              if started else ''))
    if record.get('maxAge') is not None and (previous.get('maxAge') or 0) < record['maxAge']:
        parts.append(f"cache refreshed (max-age {record['maxAge']})")
    if record.get('fingerprint') != previous.get('fingerprint') and state == 'PREP':
        parts.append(f"answer changed · {players}")
    return ' · '.join(parts) or None


async def sleep_or_stop(stop, seconds):
    """Sleep; True when the probe was stopped meanwhile."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
        return True
    except asyncio.TimeoutError:
        return False


def finished(progress, after_live, now):
    """All answering variants reported the start, or the first report is `after_live` old."""
    answering = [p for p in progress.values() if p['answered']]
    live = [p['firstLiveAt'] for p in answering if p['firstLiveAt']]
    return bool(live) and (len(live) == len(answering) or now - min(live) >= after_live)


async def poll_all(sources, variants, tag, out, interval=5, after_live=240, max_seconds=7200):
    stop = asyncio.Event()
    deadline = time.time() + max_seconds
    progress = {v['name']: {'answered': False, 'firstLiveAt': None, 'last': None, 'lastFound': None,
                            'rejected': 0} for v in variants}

    async def poll(variant):
        session, base_url, headers = sources[variant['base']]
        state = progress[variant['name']]
        if await sleep_or_stop(stop, variant['offset']):
            return
        while not stop.is_set():
            slot = time.monotonic()
            record = await observe(session, base_url, headers, variant, tag,
                                   (state['lastFound'] or {}).get('fingerprint'))
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
            out.flush()
            note = describe_change(state['last'], record)
            if note:
                print(f"{clock(record['respondedAt'])}  {variant['name']:<15} {note}", flush=True)
            state['last'] = record
            if record.get('found'):
                state['answered'], state['lastFound'] = True, record
                if record.get('status') in STARTED and not state['firstLiveAt']:
                    state['firstLiveAt'] = record['respondedAt']
            # A URL the API keeps refusing (e.g. 400 for an unknown parameter) will not recover.
            status = record.get('httpStatus') or 0
            state['rejected'] = state['rejected'] + 1 if 400 <= status < 500 and status != 429 else 0
            if state['rejected'] >= 3 and not state['answered']:
                print(f"{clock(record['respondedAt'])}  {variant['name']:<15} dropped (HTTP {status})", flush=True)
                return
            if await sleep_or_stop(stop, slot + interval - time.monotonic()):
                return

    async def supervise():
        while not stop.is_set():
            await asyncio.sleep(1)
            if finished(progress, after_live, time.time()) or time.time() >= deadline:
                stop.set()

    await asyncio.gather(supervise(), *(poll(v) for v in variants))


def entry_generated(record, ttl, counts_down):
    """When the upstream answer in this response was produced (response time minus cache age)."""
    if record.get('ageHeader') is not None:
        return record['respondedAt'] - record['ageHeader']
    if counts_down and ttl and record.get('maxAge') is not None:
        return record['respondedAt'] - (ttl - record['maxAge'])
    return None


def clock_offset(records):
    """Median server-minus-local clock difference from Date headers (1s resolution)."""
    samples = []
    for record in records:
        date = (record.get('headers') or {}).get('date')
        try:
            server = parsedate_to_datetime(date).timestamp() + 0.5
        except (TypeError, ValueError):
            continue
        samples.append(server - (record['requestedAt'] + record['respondedAt']) / 2)
    return round(statistics.median(samples), 1) if samples else None


def shared_entries(a, b):
    """Whether two variants were served the same cache entries (None: not comparable).

    Pairs answers requested within a second of each other and compares when their
    upstream answers were produced; the median ignores pairs straddling a refresh.
    """
    diffs = []
    for at, made in a:
        nearest = min(b, key=lambda other: abs(other[0] - at), default=None)
        if nearest and abs(nearest[0] - at) <= 1:
            diffs.append(abs(nearest[1] - made))
    return statistics.median(diffs) <= 2 if diffs else None


def analyze(records, interval=5):
    by_variant = defaultdict(list)
    produced = {}
    for record in sorted(records, key=lambda r: r['requestedAt']):
        by_variant[record['variant']].append(record)
    starts = Counter(r['startedTime'] for r in records if r.get('startedTime'))
    started_time = starts.most_common(1)[0][0] if starts else None
    started_at = crtime(started_time)
    snapshot = next((r['tournament'] for r in records if r.get('tournament')), {})
    result = {'tag': snapshot.get('tag'), 'name': snapshot.get('name'), 'startedTime': started_time,
              'startedAt': started_at, 'interval': interval, 'clockOffset': clock_offset(records),
              'variants': {}}

    for name, recs in by_variant.items():
        ok = [r for r in recs if not problem_of(r)]
        ages = [r['maxAge'] for r in ok if r.get('maxAge') is not None]
        ttl = max(ages, default=None)
        counts_down = len(set(ages)) >= 3

        def generated(record):
            return entry_generated(record, ttl, counts_down) if record else None

        produced[name] = [(r['requestedAt'], generated(r)) for r in ok if generated(r) is not None]
        live = [r for r in ok if r.get('status') in STARTED]
        first_live = live[0] if live else None
        prep = [r for r in ok if r.get('status') == 'inPreparation'
                and (first_live is None or r['respondedAt'] < first_live['respondedAt'])]
        last_prep = prep[-1] if prep else None
        pairs = list(zip(ok, ok[1:]))
        refreshed = {id(b) for a, b in pairs if a.get('maxAge') is not None
                     and b.get('maxAge') is not None and b['maxAge'] > a['maxAge']}
        changes = [b for a, b in pairs if b.get('status') == 'inPreparation'
                   and a.get('fingerprint') != b.get('fingerprint')]
        transition = bool(last_prep and first_live)
        result['variants'][name] = {
            'requests': len(recs), 'answered': len(ok),
            'problems': dict(Counter(problem_of(r) for r in recs if problem_of(r))),
            'ttl': ttl, 'countsDown': counts_down,
            'firstMaxAge': ok[0].get('maxAge') if ok else None,
            'transition': transition,
            'lastPrepAt': last_prep['respondedAt'] if last_prep else None,
            'lastPrepGeneratedAt': generated(last_prep),
            'firstLiveAt': first_live['respondedAt'] if first_live else None,
            'firstLiveGeneratedAt': generated(first_live),
            'lagSeconds': (round(first_live['respondedAt'] - started_at, 1)
                           if transition and started_at else None),
            'refreshes': [generated(r) for r in ok if id(r) in refreshed and generated(r) is not None],
            'prepChanges': len(changes),
            'prepChangesWithoutRefresh': sum(1 for r in changes if id(r) not in refreshed),
        }

    variants = result['variants']
    comparable = {n: v for n, v in variants.items() if v['transition']}
    if comparable:
        earliest = min(comparable, key=lambda n: comparable[n]['firstLiveAt'])
        result['earliest'] = earliest
        if 'proxy-detail' in comparable:
            result['gainOverProxyDetail'] = round(
                comparable['proxy-detail']['firstLiveAt'] - comparable[earliest]['firstLiveAt'], 1)
    prep_made = [v['lastPrepGeneratedAt'] for v in variants.values() if v['lastPrepGeneratedAt'] is not None]
    live_made = [v['firstLiveGeneratedAt'] for v in variants.values() if v['firstLiveGeneratedAt'] is not None]
    if started_at and prep_made and live_made:
        # The API data turned LIVE after its last PREP answer was produced and no later
        # than its first LIVE answer, in seconds after startedTime.
        result['sourceSwitch'] = [round(max(prep_made) - started_at, 1), round(min(live_made) - started_at, 1)]
    # URLs nobody requested before (b/c variants): a full max-age on the first answer
    # means our request opened a new cache entry, i.e. every URL runs its own cycle.
    fresh = [v['firstMaxAge'] >= v['ttl'] - interval for n, v in variants.items()
             if n.startswith('proxy-') and n.endswith(('-b', '-c')) and v['countsDown']
             and v['firstMaxAge'] is not None]
    result['perUrlCache'] = all(fresh) if fresh else None
    result['directSharesProxyCache'] = shared_entries(produced.get('direct-detail', []),
                                                      produced.get('proxy-detail', []))
    return result


def conclusions(result):
    interval = result['interval']
    if 'earliest' not in result:
        return ['No PREP → LIVE transition was observed. Start the probe while the tournament is still preparing.']
    earliest, gain = result['earliest'], result.get('gainOverProxyDetail')
    variants = result['variants']
    lines = []
    if gain is None:
        lines.append(f"{earliest} reported the start first; proxy-detail showed no transition.")
    elif gain > interval:
        lines.append(f"{earliest} reported the start {gain:.0f}s before proxy-detail "
                     f"(the production watcher's request).")
    else:
        lines.append("No alternative request reported the start measurably earlier than proxy-detail "
                     "(the production watcher's request).")
    direct, proxy = variants.get('direct-detail'), variants.get('proxy-detail')
    if direct and proxy and direct['transition'] and proxy['transition']:
        diff = proxy['firstLiveAt'] - direct['firstLiveAt']
        lines.append(f"Direct API {'earlier' if diff > 0 else 'later'} than the proxy by {abs(diff):.0f}s."
                     if abs(diff) > interval else 'Direct API and proxy reported the start at the same time.')
    shared = result.get('directSharesProxyCache')
    if shared is True:
        lines.append('Direct API and proxy served the same cache entries: the proxy itself adds no delay.')
    elif shared is False:
        lines.append('Direct API and proxy use separate cache entries: one start can differ by cycle '
                     'timing alone, so repeat before switching to the direct API.')
    if result.get('sourceSwitch'):
        low, high = result['sourceSwitch']
        if low > interval:
            lines.append(f"An answer produced {low:.0f}s after the start still said PREP: the data source "
                         f"itself lags by at least that much; different requests cannot beat that part.")
        elif high <= interval:
            lines.append(f"The API data turned LIVE within {max(high, 0):.0f}s of the start; the rest of "
                         f"the delay was response caching.")
        else:
            lines.append(f"The API data turned LIVE {max(low, 0):.0f}–{high:.0f}s after the start "
                         f"(more staggered URLs would narrow this window).")
    if result.get('perUrlCache') is True:
        lines.append('New URLs started their own full cache cycle: polling several staggered URLs '
                     'can shorten the delay.')
    elif result.get('perUrlCache') is False:
        lines.append('New URLs joined an already running cache cycle: staggering URLs does not help.')
    offset = result.get('clockOffset')
    if offset is not None and abs(offset) > 2:
        lines.append(f"Local clock differs from the API server by {offset:+.0f}s: delays relative to "
                     f"startedTime are shifted by that much (comparisons between sources are not).")
    return lines


def print_summary(result, path=None):
    print(f"\nStart probe {result.get('tag') or ''} \"{result.get('name') or ''}\"")
    print(f"startedTime (API): {clock(result['startedAt'])} · poll interval {result['interval']:g}s")
    print(f"{'variant':<15} {'answers':>9}  {'last PREP':>9}  {'first LIVE':>10}  {'delay':>6}  "
          f"{'cache':>5}  {'1st max-age':>11}  problems")
    for name, v in result['variants'].items():
        delay = f"+{v['lagSeconds']:.0f}s" if v['lagSeconds'] is not None else '—'
        cache = f"{v['ttl']}s" if v['ttl'] is not None else '—'
        first_age = v['firstMaxAge'] if v['firstMaxAge'] is not None else '—'
        problems = ', '.join(f'{k} ×{n}' for k, n in v['problems'].items()) or '—'
        print(f"{name:<15} {v['answered']:>4}/{v['requests']:<4}  {clock(v['lastPrepAt']):>9}  "
              f"{clock(v['firstLiveAt']):>10}  {delay:>6}  {cache:>5}  {first_age:>11}  {problems}")
    for line in conclusions(result):
        print('→ ' + line)
    if path:
        print(f'Raw responses: {path}')


def load(path):
    with open(path, encoding='utf-8') as source:
        return [json.loads(line) for line in source if line.strip()]


async def probe(args):
    tag = normalize_tag(args.tag)
    headers = app.get_api_headers()
    if headers['Authorization'].strip() == 'Bearer':
        sys.exit('No API key: set CR_API_KEY or save a key in the app first.')
    detail = {'name': 'proxy-detail', 'path': '/tournaments/' + quote(tag, safe=''), 'params': {}}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=app.get_ssl_context())) as session:
        first = await observe(session, app.API_BASE, headers, detail, tag)
        if not first.get('found'):
            sys.exit(f'{tag} could not be loaded ({problem_of(first)}).')
        name = first['tournament'].get('name')
        if first.get('status') != 'inPreparation':
            sys.exit(f'{tag} "{name}" is {first.get("status")}: nothing to measure. Pick a preparing tournament.')
        setup = [first]
        sources = {'proxy': (session, app.API_BASE, headers)}
        direct_key = os.environ.get('CR_DIRECT_API_KEY', '').strip()
        if direct_key:
            direct_headers = {'Authorization': 'Bearer ' + direct_key, 'Accept': 'application/json'}
            check = await observe(session, DIRECT_BASE, direct_headers, {**detail, 'name': 'direct-detail'}, tag)
            if check.get('found'):
                sources['direct'] = (session, DIRECT_BASE, direct_headers)
                setup.append(check)
            else:
                print(f"Direct API skipped ({problem_of(check)}): is this machine's public IP "
                      f"whitelisted for CR_DIRECT_API_KEY?")
        query = args.query or await choose_query(session, headers, tag, name)
        if not query:
            print('No name search finds this tag; search variants skipped (pass --query to override).')
        variants = build_variants(tag, query, 'direct' in sources, args.stagger)
        created, prep = crtime(first['tournament'].get('createdTime')), first['tournament'].get('preparationDuration')
        latest = created + prep if created and prep else None
        path = ROOT / '.runtime' / f"probe-{tag.lstrip('#')}-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        path.parent.mkdir(exist_ok=True)
        args.output = path  # lets Ctrl+C still analyze this run
        print(f'Probing {tag} "{name}" (PREP, starts by {clock(latest)}) every {args.interval:g}s'
              + (f" · search '{query}'" if query else '') + f"\nVariants: {', '.join(v['name'] for v in variants)}"
              f"\nKeep this running until the start is reported. Raw responses: {path}\n", flush=True)
        with path.open('w', encoding='utf-8') as out:
            for record in setup:
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
            await poll_all(sources, variants, tag, out, args.interval, args.after_live, args.max_minutes * 60)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tag', nargs='?', help="tournament tag, e.g. '#2CV0Q99G'")
    parser.add_argument('--live', action='store_true', help='send live API requests (required to probe)')
    parser.add_argument('--analyze', metavar='JSONL', help='only analyze an earlier probe file')
    parser.add_argument('--query', help='name search to use instead of the automatic choice')
    parser.add_argument('--interval', type=float, default=5, help='seconds between requests per variant')
    parser.add_argument('--stagger', type=float, default=30, help='offset step between b/c variants')
    parser.add_argument('--after-live', type=float, default=240,
                        help='stop this many seconds after the first start report')
    parser.add_argument('--max-minutes', type=float, default=120)
    args = parser.parse_args()
    if args.analyze:
        path = Path(args.analyze)
    elif args.live and args.tag:
        args.output = None
        try:
            asyncio.run(probe(args))
        except KeyboardInterrupt:
            print('\nStopped.')
        path = args.output
        if path is None or not path.exists():
            return
    else:
        parser.error("pass a tag with --live, or --analyze FILE")
    result = analyze(load(path), args.interval)
    print_summary(result, path)
    path.with_suffix('.summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
