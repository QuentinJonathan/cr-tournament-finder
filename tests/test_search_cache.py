import asyncio
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

    def test_simultaneous_callers_share_one_detail_phase(self):
        release_crawl = threading.Event()
        detail_batches = []

        def fake_fetch(progress_cb=None, stop_event=None):
            self.assertTrue(release_crawl.wait(timeout=2))
            return [{"tag": "#LIVE", "status": "inProgress"}, {"tag": "#PREP", "status": "inPreparation"}]

        def fake_details(tournaments, progress_cb=None, stop_event=None):
            detail_batches.append([t["tag"] for t in tournaments])
            for t in tournaments:
                t["startedTime"] = "20261019T170000.000Z"
            return tournaments

        results = []
        with patch.dict(os.environ, {"SEARCH_CACHE_TTL_SECONDS": "180"}), \
                patch.object(app_module, "fetch_all_tournaments", side_effect=fake_fetch), \
                patch.object(app_module, "fetch_tournament_details_batch", side_effect=fake_details):
            threads = [threading.Thread(target=lambda: results.append(app_module.get_cached_search_results()))
                       for _ in range(3)]
            for thread in threads:
                thread.start()
            time.sleep(0.05)
            release_crawl.set()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(detail_batches, [["#LIVE"]])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["tournaments"][0]["startedTime"], "20261019T170000.000Z")


class DetailPhaseTests(unittest.TestCase):
    def test_stalled_detail_request_does_not_hold_back_the_others(self):
        async def detail(session, tag):
            if tag == "#STUCK":
                await asyncio.sleep(30)
            return {"startedTime": "20261019T170000.000Z"}

        tournaments = [{"tag": "#STUCK"}] + [{"tag": f"#T{i}"} for i in range(5)]
        with patch.dict(os.environ, {"DETAIL_PHASE_BUDGET_SECONDS": "0.2"}), \
                patch.object(app_module, "_get_cached_tournament_detail_async", detail):
            started = time.monotonic()
            app_module.fetch_tournament_details_batch(tournaments)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2)
        self.assertNotIn("startedTime", tournaments[0])
        self.assertTrue(all(t["startedTime"] for t in tournaments[1:]))

    def test_retry_after_is_capped_for_interactive_searches(self):
        class Response:
            headers = {"Retry-After": "120"}
        self.assertEqual(app_module.get_retry_delay_seconds(0, Response()), 10.0)
        self.assertEqual(app_module.get_retry_delay_seconds(0), 0.75)


if __name__ == "__main__":
    unittest.main()
