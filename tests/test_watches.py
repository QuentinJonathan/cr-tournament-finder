import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app
from watch import StateStore, WatchService, WatchRetryAfter, normalize_tag, validate_subscription, retry_after_seconds

TAG = '#2PQGYYGY'


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(self.temp.name + '/state.json')
        self.now = 1000
        self.detail = {'tag': TAG, 'name': 'Early start', 'status': 'inPreparation'}
        self.sent, self.scheduled, self.fetches = [], [], []
        self.service = WatchService(self.store, self.fetch, copy.deepcopy, self.send,
                                    lambda delay, key: self.scheduled.append((delay, key)), lambda: self.now)
        self.store.update(lambda s: s['subscriptions'].update({'phone': {'endpoint': 'mock'}}))

    def fetch(self, tag):
        self.fetches.append(tag)
        return copy.deepcopy(self.detail)

    def send(self, subscription, data):
        self.sent.append(data)
        return 'sent'

    def test_early_start_survives_restart_and_only_notifies_once(self):
        self.service.pin(TAG)
        self.now += 10
        self.service.tick()
        self.assertFalse(self.sent)
        self.detail.update(status='inProgress', startedTime='20260905T090000.000Z')
        self.now += 10
        self.service.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['url'], '/?tournament=2PQGYYGY')
        restarted = WatchService(StateStore(self.temp.name + '/state.json'), self.fetch, copy.deepcopy, self.send,
                                 lambda *_: None, lambda: self.now)
        restarted.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(restarted.public_state()['pins'][0]['state'], 'live')

    def test_transient_failure_retries_next_slot_and_start_sends_immediately(self):
        self.service.pin(TAG)
        self.detail = None
        self.now += 10
        self.service.tick()
        pin = self.store.read()['pins'][TAG]
        self.assertEqual(pin['state'], 'watching')
        self.assertEqual(pin['checkedAt'], 1000)
        self.assertEqual(pin['nextCheckAt'], 1020)
        count = len(self.fetches)
        self.detail = {'tag': TAG, 'status': 'inProgress'}
        self.now += 10
        self.service.tick()
        self.assertEqual(len(self.fetches), count + 1)
        self.assertEqual(len(self.sent), 1)

    def test_repeated_transient_errors_never_grow_poll_interval(self):
        self.service.pin(TAG)
        self.detail = None
        for _ in range(6):
            self.now += 10
            self.service.tick()
            self.assertEqual(self.store.read()['pins'][TAG]['nextCheckAt'], self.now + 10)
        self.assertFalse(self.sent)

    def test_explicit_api_retry_after_is_respected(self):
        self.service.pin(TAG)
        original = self.service.fetch_detail
        self.service.fetch_detail = lambda _: (_ for _ in ()).throw(WatchRetryAfter(45))
        self.now = 1010
        self.service.tick()
        self.assertEqual(self.store.read()['pins'][TAG]['nextCheckAt'], 1055)
        self.service.fetch_detail = original
        count = len(self.fetches)
        self.now = 1050
        self.service.tick()
        self.assertEqual(len(self.fetches), count)
        self.now = 1060
        self.service.tick()
        self.assertEqual(len(self.fetches), count + 1)

    def test_retry_after_parses_seconds_and_http_dates(self):
        self.assertEqual(retry_after_seconds('45', 1000), 45)
        self.assertEqual(retry_after_seconds('Thu, 01 Jan 1970 00:18:00 GMT', 1000), 80)
        self.assertIsNone(retry_after_seconds('bad', 1000))
        self.assertIsNone(retry_after_seconds(None, 1000))

    def test_logs_correlate_start_and_push_without_subscription_credentials(self):
        self.service.pin(TAG)
        self.detail['status'] = 'inProgress'
        self.now += 10
        with self.assertLogs('TournamentFinder', level='INFO') as logs:
            self.service.tick()
        records = [json.loads(line.split(':', 2)[2]) for line in logs.output]
        self.assertEqual([r['event'] for r in records],
                         ['watch_decision', 'watch_push_attempt', 'watch_push_result'])
        self.assertEqual(records[-1]['at'], records[0]['detectedAt'])
        self.assertNotIn('mock', ''.join(logs.output))

    def test_unpin_during_api_request_does_not_resurrect_or_notify(self):
        self.service.pin(TAG)
        def unpin_and_start(tag):
            self.service.remove(tag)
            return {'tag': tag, 'status': 'inProgress'}
        self.service.fetch_detail = unpin_and_start
        self.now += 10
        self.service.tick()
        self.assertEqual(self.store.read()['pins'], {})
        self.assertFalse(self.sent)

    def test_schedule_happens_before_fallible_work(self):
        self.service.pin(TAG)
        self.scheduled.clear()
        def fetch(tag):
            self.assertEqual(len(self.scheduled), 1)
            raise RuntimeError('network')
        self.service.fetch_detail = fetch
        self.now += 10
        self.service.tick()
        self.assertFalse(self.sent)

    def test_push_failure_retries_without_fetching_started_tournament(self):
        self.service.pin(TAG)
        self.detail['status'] = 'inProgress'
        self.service.send_push = lambda *_: (_ for _ in ()).throw(RuntimeError('offline'))
        self.now += 10
        self.service.tick()
        self.assertEqual(self.store.read()['pins'][TAG]['delivered'], [])
        count = len(self.fetches)
        self.service.send_push = self.send
        self.now += 10
        self.service.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.fetches), count)

    def test_removed_subscription_stops_delivery_retries(self):
        self.service.pin(TAG)
        self.detail['status'] = 'inProgress'
        self.service.send_push = lambda *_: 'gone'
        self.now += 10
        self.service.tick()
        self.assertEqual(self.store.read()['subscriptions'], {})
        self.assertFalse(self.service.needs_work(self.store.read()))

    def test_expired_and_ended_watches_stop_without_start_alert(self):
        self.service.pin(TAG)
        self.now += 86401
        self.service.tick()
        self.assertEqual(self.store.read()['pins'][TAG]['state'], 'expired')
        self.assertFalse(self.service.needs_work(self.store.read()))
        self.assertFalse(self.sent)

    def test_subscriptions_cannot_target_arbitrary_hosts(self):
        for url in ['http://127.0.0.1/', 'https://metadata.google.internal/',
                    'https://web.push.apple.com.evil.test/', 'https://fcm.googleapis.com:444/']:
            with self.assertRaises(ValueError):
                validate_subscription({'endpoint': url})

    def test_tag_validation(self):
        self.assertEqual(normalize_tag('2pqgyygy'), TAG)
        for bad in ['../../../config', '', '#<script>']:
            with self.assertRaises(ValueError):
                normalize_tag(bad)

    def test_watch_routes_require_auth_json_and_keep_subscriptions_private(self):
        client = app.app.test_client()
        with patch.object(app, 'APP_PASSWORD', 'test'):
            self.assertEqual(client.get('/api/watches').status_code, 401)
            self.assertEqual(client.post('/internal/watch/tick').status_code, 401)
        with patch.object(app, 'APP_PASSWORD', ''), patch.object(app, '_watch_service', self.service):
            self.assertEqual(client.post('/api/watches', data={'tag': TAG}).status_code, 415)
            response = client.post('/api/watches', json={'tag': TAG})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('subscriptions', response.json)
            self.assertEqual(client.delete('/api/watches', json={'tag': TAG}).json['pins'], [])

    def test_production_does_not_claim_background_support_without_queue(self):
        with patch.dict(os.environ, {'FLASK_ENV': 'production'}, clear=True), patch.object(app, 'APP_PASSWORD', ''):
            self.assertEqual(app.app.test_client().post('/api/watches', json={'tag': TAG}).status_code, 503)


if __name__ == '__main__':
    unittest.main()
