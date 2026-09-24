import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app
from watch import WatchRetryAfter, fingerprint


class WatchApiTests(unittest.TestCase):
    def fetch(self, status=200, headers=None, detail=None, error=None, query=None):
        response = MagicMock(status=status, headers=headers or {})
        response.json = AsyncMock(return_value=detail)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response, side_effect=error)
        context.__aexit__ = AsyncMock(return_value=False)
        client = MagicMock()
        client.get.return_value = context
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=client)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(app.aiohttp, 'ClientSession', return_value=session), \
                patch('watch.watch_event') as event:
            try:
                if query is not None:
                    return app.fetch_watch_search('#2PQGYYGY', query)
                return app.fetch_watch_detail('#2PQGYYGY')
            finally:
                self.observed = event.call_args.kwargs
                self.params = client.get.call_args.kwargs.get('params')

    def test_search_finds_pinned_tournament_and_reports_cache_lifetime(self):
        body = {'items': [{'tag': '#OTHER', 'status': 'inProgress'},
                          {'tag': '#2PQGYYGY', 'status': 'inProgress', 'capacity': 3}]}
        result = self.fetch(detail=body, headers={'Cache-Control': 'max-age=87'}, query=' Alliance')
        self.assertEqual(result, {'item': body['items'][1], 'maxAge': 87})
        self.assertEqual(self.params, {'name': ' Alliance'})
        self.assertEqual((self.observed['source'], self.observed['query'], self.observed['found'],
                          self.observed['status'], self.observed['results']),
                         ('search', ' Alliance', True, 'inProgress', 2))
        missing = self.fetch(detail={'items': body['items'][:1]}, query='alliance')
        self.assertEqual(missing, {'item': None, 'maxAge': None})
        self.assertFalse(self.observed['found'])

    def test_live_response_and_timestamps_are_logged(self):
        detail = {'status': 'inProgress', 'startedTime': '20260906T110131.000Z'}
        self.assertEqual(self.fetch(detail=detail), detail)
        self.assertEqual(self.observed['status'], 'inProgress')
        self.assertEqual(self.observed['startedTime'], detail['startedTime'])
        self.assertGreaterEqual(self.observed['respondedAt'], self.observed['requestedAt'])

    def test_players_fingerprint_and_server_date_are_logged(self):
        detail = {'status': 'inPreparation', 'capacity': 12, 'membersList': [{'tag': '#P'}]}
        self.fetch(detail=detail, headers={'Date': 'Mon, 07 Sep 2026 20:15:10 GMT'})
        self.assertEqual(self.observed['capacity'], 12)
        self.assertEqual(self.observed['fingerprint'], fingerprint(detail))
        self.assertEqual(self.observed['date'], 'Mon, 07 Sep 2026 20:15:10 GMT')
        self.assertNotIn('membersList', self.observed)

    def test_rate_limit_and_service_retry_headers_reach_scheduler(self):
        for status, headers, expected in [(429, {}, 30), (429, {'Retry-After': '45'}, 45),
                                           (503, {'Retry-After': '90'}, 90)]:
            with self.assertRaises(WatchRetryAfter) as raised:
                self.fetch(status=status, headers=headers)
            self.assertEqual(raised.exception.seconds, expected)
            self.assertEqual(self.observed['httpStatus'], status)

    def test_transient_errors_return_none_without_logging_secret_exception_text(self):
        self.assertIsNone(self.fetch(error=TimeoutError('secret-url')))
        self.assertEqual(self.observed['errorType'], 'TimeoutError')
        self.assertNotIn('secret-url', str(self.observed))
        self.assertIsNone(self.fetch(status=503))
