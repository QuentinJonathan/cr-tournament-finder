import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import app


class FakeResponse:
    def __init__(self, status, items=None):
        self.status = status
        self.headers = {}
        self._items = items or []

    async def json(self):
        return {"items": self._items}


class FakeSession:
    """Answers each spelling after its configured delay; records the order they were requested."""

    def __init__(self, delays, statuses=None):
        self.delays = delays
        self.statuses = statuses or {}
        self.requested = []

    def get(self, url, params=None, **kwargs):
        spelling = params["name"]
        self.requested.append(spelling)
        session = self

        class Ctx:
            async def __aenter__(self):
                await asyncio.sleep(session.delays.get(spelling, 0))
                status = session.statuses.get(spelling, 200)
                return FakeResponse(status, [{"tag": "#" + spelling.strip().upper()}])

            async def __aexit__(self, *exc):
                return False

        return Ctx()


class HedgedSearchTests(unittest.TestCase):
    def run_query(self, session, query="t"):
        stats = app.make_search_stats()
        with patch.dict(os.environ, {"SEARCH_HEDGE_SECONDS": "0.05"}):
            started = time.monotonic()
            result = asyncio.run(app.fetch_tournaments_by_query_async(session, query, stats))
        return result, stats, time.monotonic() - started

    def test_spellings_are_equivalent_variants(self):
        self.assertEqual(app.query_spellings("t"), ["t", "T", " t"])
        self.assertEqual(app.query_spellings("5"), ["5", " 5"])
        self.assertEqual(app.query_spellings("ß"), ["ß", " ß"])
        self.assertEqual(app.query_spellings("ж"), ["ж", "Ж", " ж"])

    def test_stalled_request_is_overtaken_by_next_spelling(self):
        session = FakeSession({"t": 5})
        result, stats, elapsed = self.run_query(session)
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], [{"tag": "#T"}])
        self.assertEqual(session.requested, ["t", "T"])
        self.assertEqual(stats["hedged_requests"], 1)
        self.assertLess(elapsed, 1)

    def test_fast_answers_send_no_hedge(self):
        session = FakeSession({})
        result, stats, _ = self.run_query(session)
        self.assertTrue(result["ok"])
        self.assertEqual(session.requested, ["t"])
        self.assertEqual(stats["hedged_requests"], 0)

    def test_server_error_moves_on_to_next_spelling(self):
        session = FakeSession({}, statuses={"t": 503})
        result, stats, _ = self.run_query(session)
        self.assertTrue(result["ok"])
        self.assertEqual(session.requested, ["t", "T"])
        self.assertEqual(result["attempts"], 1)

    def test_rate_limit_stops_hedging_and_backs_off(self):
        session = FakeSession({}, statuses={"t": 429, "T": 429, " t": 429})
        with patch.object(app, "get_retry_delay_seconds", return_value=0):
            result, stats, _ = self.run_query(session)
        self.assertFalse(result["ok"])
        self.assertEqual(session.requested, ["t"] * 4)
        self.assertEqual(stats["rate_limits"], 4)


class GameModeTests(unittest.TestCase):
    def test_internal_names_are_humanized(self):
        self.assertEqual(app.humanize_mode_name("Chaos_1v1_MegaDraft_All"), "Chaos Mega Draft")
        self.assertEqual(app.humanize_mode_name("Showdown_Friendly"), "Showdown")
        self.assertEqual(app.humanize_mode_name("TeamVsTeam_2v2_Draft"), "Team Vs Team 2v2 Draft")

    def test_endpoint_offers_every_known_mode(self):
        app.app.config.update(TESTING=True)
        with patch.object(app, "APP_PASSWORD", ""):
            modes = app.app.test_client().get("/api/game-modes").get_json()
        self.assertEqual(modes["72000065"], "1v1 Showdown")
        self.assertEqual(modes["72000031"], "Sudden Death Battle")
        self.assertEqual(modes["72000512"], "Chaos Mega Draft")

    def test_unknown_mode_is_named_from_battle_log_and_persisted(self):
        tournaments = [
            {"tag": "#PREP", "status": "inPreparation", "capacity": 50, "gameMode": {"id": 72099999}},
            {"tag": "#LIVE", "status": "inProgress", "capacity": 5, "gameMode": {"id": 72099999}},
            {"tag": "#KNOWN", "status": "inProgress", "gameMode": {"id": 72000009}},
        ]
        details = {"#LIVE": {"membersList": [{"tag": "#IDLE", "score": 0}, {"tag": "#ACTIVE", "score": 3}]}}
        requested = []

        async def detail(session, tag):
            requested.append(tag)
            return details.get(tag)

        async def api_json(session, path):
            requested.append(path)
            if path == "/players/%23ACTIVE/battlelog":
                return [{"gameMode": {"id": 72099999, "name": "Event_Spooky_Draft"}}]
            return []

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "learned.json")
            with patch.object(app, "LEARNED_MODES_PATH", path), \
                    patch.dict(app.LEARNED_GAME_MODES, clear=True), \
                    patch.object(app, "_get_cached_tournament_detail_async", detail), \
                    patch.object(app, "fetch_api_json_async", api_json):
                learned = app.resolve_unknown_game_modes(tournaments)
                self.assertEqual(app.get_mode_name(72099999), "Spooky Draft")
                with open(path) as f:
                    self.assertEqual(json.load(f), {"72099999": "Spooky Draft"})
                # Known now: a second crawl does not look it up again.
                requested.clear()
                self.assertEqual(app.resolve_unknown_game_modes(tournaments), {})
                self.assertEqual(requested, [])

        self.assertEqual(learned, {"72099999": "Spooky Draft"})

    def test_unresolvable_mode_is_not_retried_immediately(self):
        tournaments = [{"tag": "#PREP", "status": "inPreparation", "gameMode": {"id": 72088888}}]
        with patch.dict(app._mode_lookup_failed_at, clear=True), \
                patch.dict(app.LEARNED_GAME_MODES, clear=True):
            self.assertEqual(app.resolve_unknown_game_modes(tournaments), {})
            self.assertIn("72088888", app._mode_lookup_failed_at)
            with patch.object(app, "fetch_mode_name_from_battles_async") as lookup:
                app.resolve_unknown_game_modes(tournaments)
            lookup.assert_not_called()
        self.assertEqual(app.get_mode_name(72088888), "Unknown (72088888)")


if __name__ == "__main__":
    unittest.main()
