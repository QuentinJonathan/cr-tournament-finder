import os
import threading
import time
import unittest
from unittest.mock import patch

import app as app_module


class SearchCacheTests(unittest.TestCase):
    def setUp(self):
        with app_module._SEARCH_CACHE_COND:
            app_module._SEARCH_CACHE = None
            app_module._SEARCH_FETCH_IN_PROGRESS = False

    def tearDown(self):
        with app_module._SEARCH_CACHE_COND:
            app_module._SEARCH_CACHE = None
            app_module._SEARCH_FETCH_IN_PROGRESS = False
            app_module._SEARCH_CACHE_COND.notify_all()

    def test_simultaneous_callers_share_one_crawl(self):
        crawl_started = threading.Event()
        release_crawl = threading.Event()
        call_count = 0
        call_count_lock = threading.Lock()
        results = []
        errors = []

        def fake_fetch(progress_cb=None, stop_event=None):
            nonlocal call_count
            with call_count_lock:
                call_count += 1
            crawl_started.set()
            self.assertTrue(release_crawl.wait(timeout=2))
            return [{"tag": "#TEST"}]

        def call_cache():
            try:
                results.append(app_module.get_cached_search_results())
            except Exception as exc:  # pragma: no cover - diagnostic path
                errors.append(exc)

        with patch.dict(os.environ, {"SEARCH_CACHE_TTL_SECONDS": "180"}), patch.object(
            app_module, "fetch_all_tournaments", side_effect=fake_fetch
        ):
            first = threading.Thread(target=call_cache)
            first.start()
            self.assertTrue(crawl_started.wait(timeout=2))

            second = threading.Thread(target=call_cache)
            second.start()
            time.sleep(0.05)
            release_crawl.set()

            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["tournaments"], [{"tag": "#TEST"}])
        self.assertIs(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
