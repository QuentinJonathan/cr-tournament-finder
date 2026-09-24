import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app
from watch import WatchRetryAfter


class WatchApiTests(unittest.TestCase):
    def fetch(self, status=200, headers=None, detail=None, error=None):
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
                return app.fetch_watch_detail('#2PQGYYGY')
            finally:
                self.observed = event.call_args.kwargs

    def test_live_response_and_timestamps_are_logged(self):
        detail = {'status': 'inProgress', 'startedTime': '20260906T110131.000Z'}
        self.assertEqual(self.fetch(detail=detail), detail)
        self.assertEqual(self.observed['status'], 'inProgress')
        self.assertEqual(self.observed['startedTime'], detail['startedTime'])
        self.assertGreaterEqual(self.observed['respondedAt'], self.observed['requestedAt'])

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
