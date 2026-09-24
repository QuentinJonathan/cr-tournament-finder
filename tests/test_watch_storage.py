import json
import unittest
from unittest.mock import patch
from google.api_core.exceptions import NotFound, PreconditionFailed
from watch import StateStore, verify_task_request


class StorageTests(unittest.TestCase):
    def test_gcs_compare_and_swap_retries_without_losing_other_device_updates(self):
        class Blob:
            generation = 1
            state = {'pins': {}, 'subscriptions': {}}
            attempts = 0
            def download_as_bytes(self):
                return json.dumps(self.state).encode()
            def upload_from_string(self, body, content_type, if_generation_match):
                self.attempts += 1
                if self.attempts == 1:
                    self.state['subscriptions']['other-device'] = {'endpoint': 'existing'}
                    self.generation += 1
                    raise PreconditionFailed('concurrent write')
                assert if_generation_match == self.generation
                self.state = json.loads(body)
        blob = Blob()
        store = StateStore('/unused', bucket='test')
        with patch.object(store, '_blob', return_value=blob):
            store.update(lambda state: state['pins'].update({'tag': {'state': 'watching'}}))
            self.assertIn('other-device', store.read()['subscriptions'])
            self.assertIn('tag', store.read()['pins'])
            self.assertEqual(blob.attempts, 2)

    def test_internal_task_checks_signature_audience_and_dispatcher_identity(self):
        config = {'WATCH_QUEUE_PATH': 'queue', 'WATCH_ORIGIN': 'https://finder.example',
                  'WATCH_SERVICE_ACCOUNT': 'dispatcher@example.iam.gserviceaccount.com'}
        with patch.dict('os.environ', config), patch('google.oauth2.id_token.verify_oauth2_token') as verify:
            verify.return_value = {'email': config['WATCH_SERVICE_ACCOUNT'], 'email_verified': True}
            self.assertTrue(verify_task_request('Bearer signed-token'))
            self.assertEqual(verify.call_args.args[2], 'https://finder.example')
            verify.return_value = {'email': 'someone-else@example.com', 'email_verified': True}
            self.assertFalse(verify_task_request('Bearer signed-token'))
            self.assertFalse(verify_task_request(''))
            verify.side_effect = ValueError('invalid signature')
            self.assertFalse(verify_task_request('Bearer bad-token'))
