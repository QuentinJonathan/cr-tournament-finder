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
