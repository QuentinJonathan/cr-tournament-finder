import asyncio
import contextlib
import importlib.util
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from multidict import CIMultiDict

from watch import fingerprint

spec = importlib.util.spec_from_file_location(
    'probe_start_latency', Path(__file__).resolve().parents[1] / 'scripts' / 'probe_start_latency.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

TAG = '#2CV0Q99G'
START = datetime(2026, 9, 7, 20, 13, 46, tzinfo=timezone.utc).timestamp()
STARTED_TIME = '20260907T201346.000Z'


def cached_series(variant, first_request, generated, switch_at, until=START + 200, ttl=120, step=5):
    """Poll a URL whose cached answer is regenerated on the first request after expiry."""
    records, now, entry = [], first_request, generated
    while now <= until:
        if now >= entry + ttl:
            entry = now
        live = entry >= switch_at
        records.append({'variant': variant, 'requestedAt': now - 0.2, 'respondedAt': now,
                        'httpStatus': 200, 'found': True, 'maxAge': ttl - int(now - entry),
                        'status': 'inProgress' if live else 'inPreparation',
                        'startedTime': STARTED_TIME if live else None,
                        'fingerprint': 'live' if live else f'prep-{entry}'})
        now += step
    return records


def timeline(switch_at):
    # proxy-detail inherits a running cache entry; the other URLs are new and staggered.
    return (cached_series('proxy-detail', START - 140, START - 146, switch_at) +
            cached_series('proxy-search', START - 140, START - 141, switch_at) +
            cached_series('proxy-search-b', START - 110, START - 110, switch_at) +
            cached_series('proxy-detail-b', START - 80, START - 80, switch_at) +
            cached_series('proxy-search-c', START - 50, START - 50, switch_at) +
            [{'variant': 'direct-detail', 'requestedAt': START + i, 'respondedAt': START + i + 0.1,
              'httpStatus': 403} for i in range(3)])


def response_context(body, headers=None, error=None):
    response = MagicMock(status=200, headers=CIMultiDict(headers or {}))
    response.json = AsyncMock(return_value=body)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response, side_effect=error)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


class StartProbeTests(unittest.TestCase):
    def test_every_variant_is_a_distinct_url_with_staggered_first_requests(self):
        variants = probe.build_variants(TAG, 'ALLIANCE', direct=True, stagger=30)
        self.assertEqual([v['name'] for v in variants],
                         ['proxy-detail', 'proxy-detail-b', 'proxy-search', 'proxy-search-b',
                          'proxy-search-c', 'direct-detail', 'direct-search'])
        self.assertEqual(variants[0]['path'], '/tournaments/%232CV0Q99G')
        urls = {(v['base'], v['path'], tuple(sorted(v['params'].items()))) for v in variants}
        self.assertEqual(len(urls), len(variants))
        self.assertEqual(sorted({v['offset'] for v in variants}), [0, 30, 60, 90])
        self.assertEqual([v['name'] for v in probe.build_variants(TAG, None)], ['proxy-detail', 'proxy-detail-b'])

    def test_search_answer_is_matched_by_tag_and_logged_without_key(self):
        target = {'tag': TAG, 'name': 'ALLIANCE', 'status': 'inPreparation', 'capacity': 12,
                  'maxCapacity': 50, 'membersList': [{'tag': '#P'}]}
        session = MagicMock()
        session.get.return_value = response_context(
            {'items': [{'tag': '#OTHER'}, target]},
            {'Cache-Control': 'public max-age=87', 'Date': 'Mon, 07 Sep 2026 20:12:00 GMT', 'Set-Cookie': 'x'})
        variant = next(v for v in probe.build_variants(TAG, 'ALLIANCE') if v['name'] == 'proxy-search-b')
        headers = {'Authorization': 'Bearer top-secret-key'}
        record = asyncio.run(probe.observe(session, 'https://proxy.test/v1', headers, variant, TAG))
        self.assertEqual(session.get.call_args.args[0], 'https://proxy.test/v1/tournaments')
        self.assertEqual(session.get.call_args.kwargs['params'], {'name': 'ALLIANCE', 'limit': 21})
        self.assertEqual((record['httpStatus'], record['maxAge'], record['results']), (200, 87, 2))
        self.assertEqual((record['status'], record['capacity'], record['members']), ('inPreparation', 12, 1))
        self.assertEqual(record['fingerprint'], fingerprint(target))
        self.assertNotIn('membersList', record['tournament'])
        self.assertNotIn('set-cookie', record['headers'])
        self.assertNotIn('top-secret-key', json.dumps(record))
        unchanged = asyncio.run(probe.observe(session, 'https://proxy.test/v1', headers, variant, TAG,
                                              record['fingerprint']))
        self.assertNotIn('tournament', unchanged)

    def test_request_errors_are_logged_by_type_only(self):
        session = MagicMock()
        session.get.return_value = response_context({}, error=aiohttp.ClientConnectionError('https://x?secret'))
        variant = probe.build_variants(TAG, None)[0]
        record = asyncio.run(probe.observe(session, 'https://proxy.test/v1', {}, variant, TAG))
        self.assertEqual(record['error'], 'ClientConnectionError')
        self.assertNotIn('secret', json.dumps(record))

    def test_query_choice_prefers_the_narrowest_search_that_finds_the_tag(self):
        target, other = {'tag': TAG}, {'tag': '#OTHER'}
        bodies = {'Free Alliance': {'items': [target] + [other] * 19},
                  'Alliance': {'items': [target] + [other] * 4},
                  'Free': {'items': [other] * 20}}
        session = MagicMock()
        session.get.side_effect = lambda url, params=None, **_: response_context(bodies[params['name']])
        self.assertEqual(asyncio.run(probe.choose_query(session, {}, TAG, 'Free Alliance')), 'Alliance')
        session.get.side_effect = lambda url, params=None, **_: response_context({'items': [other]})
        self.assertIsNone(asyncio.run(probe.choose_query(session, {}, TAG, 'Free Alliance')))

    def test_console_reports_only_noteworthy_changes(self):
        prep = {'status': 'inPreparation', 'httpStatus': 200, 'found': True, 'capacity': 5,
                'maxCapacity': 50, 'maxAge': 10, 'fingerprint': 'a', 'respondedAt': START - 5}
        self.assertIn('first answer', probe.describe_change(None, prep))
        self.assertIsNone(probe.describe_change(prep, {**prep, 'maxAge': 5}))
        refreshed = {**prep, 'maxAge': 120, 'fingerprint': 'b', 'capacity': 6}
        self.assertEqual(probe.describe_change(prep, refreshed),
                         'cache refreshed (max-age 120) · answer changed · players 6/50')
        live = {**refreshed, 'status': 'inProgress', 'startedTime': STARTED_TIME, 'respondedAt': START + 94}
        self.assertRegex(probe.describe_change(refreshed, live), r'^LIVE · startedTime .* \(\+94s\)$')
        failed = {'httpStatus': 429, 'respondedAt': START + 99}
        self.assertEqual(probe.describe_change(live, failed), 'HTTP 429')
        self.assertIn('answering again', probe.describe_change(failed, live))
        self.assertIsNone(probe.describe_change(failed, dict(failed)))
        search_live = {**refreshed, 'status': 'inProgress'}  # search answers carry no startedTime
        self.assertEqual(probe.describe_change(refreshed, search_live), 'LIVE')

    def test_direct_and_proxy_cache_entries_are_compared_not_just_their_timing(self):
        proxy = cached_series('proxy-detail', START - 140, START - 146, START)
        shared = [{**r, 'variant': 'direct-detail', 'requestedAt': r['requestedAt'] + 0.3} for r in proxy]
        result = probe.analyze(proxy + shared)
        self.assertTrue(result['directSharesProxyCache'])
        self.assertIn('the proxy itself adds no delay', ' '.join(probe.conclusions(result)))
        separate = cached_series('direct-detail', START - 140, START - 205, START)
        result = probe.analyze(proxy + separate)
        self.assertFalse(result['directSharesProxyCache'])
        self.assertIn('Direct API earlier than the proxy by 60s', ' '.join(probe.conclusions(result)))
        self.assertIn('repeat before switching', ' '.join(probe.conclusions(result)))
        self.assertIsNone(probe.analyze(proxy)['directSharesProxyCache'])

    def test_probe_stops_when_all_answering_variants_are_live_or_after_grace(self):
        progress = {'a': {'answered': True, 'firstLiveAt': 100}, 'b': {'answered': True, 'firstLiveAt': None},
                    'never': {'answered': False, 'firstLiveAt': None}}
        self.assertFalse(probe.finished(progress, 240, 200))
        self.assertTrue(probe.finished(progress, 240, 340))
        progress['b']['firstLiveAt'] = 150
        self.assertTrue(probe.finished(progress, 240, 151))
        self.assertFalse(probe.finished({'a': {'answered': True, 'firstLiveAt': None}}, 240, 10 ** 10))

    def test_cached_delay_is_attributed_to_caching_and_staggered_url_wins(self):
        result = probe.analyze(timeline(switch_at=START))
        variants = result['variants']
        self.assertEqual([variants[n]['lagSeconds'] for n in ('proxy-detail', 'proxy-search-b', 'proxy-detail-b')],
                         [95, 10, 40])
        self.assertEqual((result['earliest'], result['gainOverProxyDetail']), ('proxy-search-b', 85))
        self.assertEqual(result['sourceSwitch'], [-20, 10])
        self.assertTrue(result['perUrlCache'])
        self.assertEqual(variants['proxy-detail']['firstMaxAge'], 114)
        self.assertEqual(variants['direct-detail']['problems'], {'HTTP 403': 3})
        self.assertEqual(variants['proxy-search']['prepChangesWithoutRefresh'], 0)
        lines = probe.conclusions(result)
        self.assertIn('proxy-search-b reported the start 85s before proxy-detail', lines[0])
        self.assertTrue(any('staggered URLs can shorten' in line for line in lines))

    def test_source_lag_is_not_blamed_on_caching(self):
        # The data turns LIVE 60s after the start; caching adds up to 120s on top.
        result = probe.analyze(timeline(switch_at=START + 60))
        self.assertEqual((result['earliest'], result['gainOverProxyDetail']), ('proxy-search-c', 25))
        self.assertEqual(result['sourceSwitch'], [40, 70])
        lines = probe.conclusions(result)
        self.assertTrue(any('produced 40s after the start still said PREP: the data source itself lags' in line
                            for line in lines))

    def test_same_cache_phase_shows_no_gain(self):
        records = (cached_series('proxy-detail', START - 140, START - 146, START) +
                   cached_series('proxy-search', START - 140, START - 146, START))
        result = probe.analyze(records)
        self.assertEqual(result['gainOverProxyDetail'], 0)
        self.assertIn('No alternative request reported the start measurably earlier', probe.conclusions(result)[0])

    def test_probe_started_after_the_start_reports_no_transition(self):
        records = [r for r in timeline(switch_at=START) if r.get('status') != 'inPreparation']
        result = probe.analyze(records)
        self.assertNotIn('earliest', result)
        self.assertIn('No PREP → LIVE transition', probe.conclusions(result)[0])

    def test_summary_prints_every_variant_and_conclusion(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            probe.print_summary(probe.analyze(timeline(switch_at=START)), 'probe.jsonl')
        text = output.getvalue()
        for name in ('proxy-detail', 'proxy-search-c', 'direct-detail'):
            self.assertIn(name, text)
        self.assertIn('HTTP 403 ×3', text)
        self.assertIn('Raw responses: probe.jsonl', text)


if __name__ == '__main__':
    unittest.main()
