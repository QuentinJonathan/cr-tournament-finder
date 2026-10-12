import asyncio
import copy
import os
import unittest
from unittest.mock import patch
import app


class AdaptiveSearchTests(unittest.TestCase):
    def test_adaptive_preserves_fixture_coverage_and_skips_unsaturated_pairs(self):
        # Force a saturated prefix (t), exact one-letter names, digits and Unicode.
        corpus = [{'tag': str(i), 'name': 'ta' + str(i), 'gameMode': {'id': 1}} for i in range(23)]
        corpus += [{'tag': 'solo', 'name': 'a'}, {'tag': 'accent', 'name': 'éclair'},
                   {'tag': 'digit', 'name': '9abc'}, {'tag': 'cyrillic', 'name': 'яabc'},
                   {'tag': 'arabic', 'name': 'مabc'}]
        queries = []
        async def fake_query(session, query, stats):
            queries.append(query)
            stats['queries_completed'] += 1
            matches = [copy.deepcopy(t) for t in corpus if any(word.startswith(query) for word in t['name'].split())]
            return {'ok': True, 'items': matches[:20], 'attempts': 1}
        with patch.dict(os.environ, {'SEARCH_STRATEGY': 'adaptive'}), patch.object(app, 'fetch_tournaments_by_query_async', fake_query):
            adaptive = app.fetch_all_tournaments()
        adaptive_queries = list(queries)
        queries.clear()
        with patch.dict(os.environ, {'SEARCH_STRATEGY': 'legacy'}), patch.object(app, 'fetch_tournaments_by_query_async', fake_query):
            legacy = app.fetch_all_tournaments()
        self.assertEqual({t['tag'] for t in adaptive}, {t['tag'] for t in legacy})
        self.assertEqual({t['tag'] for t in adaptive}, {t['tag'] for t in corpus})
        self.assertNotIn('ab', adaptive_queries)
        self.assertIn('ta', adaptive_queries)
        self.assertLess(len(adaptive_queries), len(queries))

    def test_saturated_prefix_drills_past_four_characters(self):
        # 30 identical "Emote" names fill every "emot"/"emote" answer; the "Emoticon" ones only
        # become reachable once the crawl splits beyond four characters.
        corpus = [{'tag': f'e{i}', 'name': 'Emote'} for i in range(30)]
        corpus += [{'tag': f'c{i}', 'name': 'Emoticon'} for i in range(3)]
        async def fake_query(session, query, stats):
            stats['queries_completed'] += 1
            matches = [t for t in corpus if any(w.lower().startswith(query) for w in t['name'].split())]
            return {'ok': True, 'items': matches[:20], 'attempts': 1}
        with patch.dict(os.environ, {'SEARCH_STRATEGY': 'adaptive'}), patch.object(app, 'fetch_tournaments_by_query_async', fake_query):
            found = {t['tag'] for t in app.fetch_all_tournaments()}
        self.assertTrue({'c0', 'c1', 'c2'} <= found)
        # 30 identical words cannot be split by the API: "emote" stays flagged as incomplete.
        self.assertEqual(len([t for t in found if t.startswith('e')]), 20)
        self.assertEqual(app.search_stats['saturated_queries'], 1)
        self.assertEqual(app.search_stats['search_confidence'], 'medium')

    def test_capped_prefix_whose_words_continue_is_not_flagged(self):
        corpus = [{'tag': f'e{i}', 'name': f'Emote{i}'} for i in range(25)]
        corpus += [{'tag': f'a{i}', 'name': f'a {i}x'} for i in range(25)]  # single-letter words
        async def fake_query(session, query, stats):
            stats['queries_completed'] += 1
            matches = [t for t in corpus if any(w.lower().startswith(query) for w in t['name'].split())]
            return {'ok': True, 'items': matches[:20], 'attempts': 1}
        with patch.dict(os.environ, {'SEARCH_STRATEGY': 'adaptive'}), patch.object(app, 'fetch_tournaments_by_query_async', fake_query):
            found = app.fetch_all_tournaments()
        self.assertEqual(len(found), 50)
        self.assertEqual(app.search_stats['search_confidence'], 'high')

    def test_failed_root_is_retried_in_verification_instead_of_treated_as_empty(self):
        attempts = {}
        async def query(session, q, stats):
            attempts[q] = attempts.get(q, 0) + 1
            if q == 'a' and attempts[q] == 1:
                return {'ok': False, 'items': [], 'attempts': 1}
            return {'ok': True, 'items': [{'tag': 'found'}] if q == 'a' else [], 'attempts': 1}
        with patch.dict(os.environ, {'SEARCH_STRATEGY': 'adaptive'}), patch.object(app, 'fetch_tournaments_by_query_async', query):
            result = app.fetch_all_tournaments()
        self.assertEqual(result, [{'tag': 'found'}])
        self.assertEqual(attempts['a'], 2)
        self.assertEqual(app.search_stats['failed_queries'], 0)
