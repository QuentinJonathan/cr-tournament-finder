import json
import os
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

import app as app_module


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def test_main_page_has_unique_ids_and_loads_timing_before_app(self):
        with patch.object(app_module, "APP_PASSWORD", ""):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        parser = IdCollector()
        parser.feed(html)

        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertLess(html.index('/static/timing.js'), html.index('/static/app.js'))
        self.assertIn('data-sort="timing"', html)
        self.assertIn('id="results-meta-dot"', html)
        self.assertIn('id="active-filter-chips"', html)

    def test_static_assets_include_the_shared_timing_model(self):
        with patch.object(app_module, "APP_PASSWORD", ""):
            timing = self.client.get("/static/timing.js")
            app_js = self.client.get("/static/app.js")
            css = self.client.get("/static/style.css")

        self.assertEqual(timing.status_code, 200)
        self.assertEqual(app_js.status_code, 200)
        self.assertEqual(css.status_code, 200)
        timing_source = timing.get_data(as_text=True)
        app_source = app_js.get_data(as_text=True)
        css_source = css.get_data(as_text=True)
        timing.close()
        app_js.close()
        css.close()

        self.assertIn("deriveTiming", timing_source)
        self.assertIn("CrTiming.deriveTiming", app_source)
        self.assertIn(".db-row-timing", css_source)
        self.assertIn('"name timing"', css_source)
        self.assertIn("grid-area: timing", css_source)
        self.assertNotIn(".db-row-status", css_source)

    def test_stream_returns_raw_timing_data_for_the_frontend(self):
        tournament = {
            "tag": "#TEST",
            "name": "Timing fixture",
            "type": "open",
            "status": "inPreparation",
            "capacity": 12,
            "maxCapacity": 100,
            "levelCap": 15,
            "gameMode": {"id": 72000009},
            "createdTime": "20260714T100000.000Z",
            "preparationDuration": 600,
            "duration": 1800,
        }
        cache = {
            "tournaments": [tournament],
            "fetchedAt": "2026-07-14T10:00:00+00:00",
            "stats": app_module.make_search_stats(),
        }

        with patch.object(app_module, "APP_PASSWORD", ""), patch.object(
            app_module, "has_api_key", return_value=True
        ), patch.object(app_module, "get_fresh_search_cache", return_value=cache), patch.object(
            app_module, "fetch_tournament_details_batch", return_value=[tournament]
        ):
            response = self.client.get("/api/tournaments/search/stream", buffered=True)

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("event: progress", body)
        self.assertIn("event: done", body)

        done_block = next(block for block in body.split("\n\n") if block.startswith("event: done"))
        data_line = next(line for line in done_block.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line.removeprefix("data: "))
        result = payload["tournaments"][0]

        self.assertEqual(result["status"], "inPreparation")
        self.assertEqual(result["createdTime"], tournament["createdTime"])
        self.assertEqual(result["preparationDuration"], 600)
        self.assertEqual(result["duration"], 1800)

    def test_service_worker_version_caches_timing_asset(self):
        service_worker_path = os.path.join(app_module.BASE_DIR, "static", "service-worker.js")
        with open(service_worker_path, "r", encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("cr-finder-v18", source)
        self.assertIn("'/static/timing.js'", source)


if __name__ == "__main__":
    unittest.main()
