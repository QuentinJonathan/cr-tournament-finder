import base64
import json
import os
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import http_ece
import watch


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


class PushEncryptionTests(unittest.TestCase):
    def test_real_webpush_encryption_and_vapid_signature_with_mocked_transport(self):
        private = ec.generate_private_key(ec.SECP256R1())
        vapid = base64.b64encode(private.private_bytes(serialization.Encoding.DER,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()
        receiver = ec.generate_private_key(ec.SECP256R1())
        public = receiver.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        subscription = {'endpoint': 'https://web.push.apple.com/test', 'keys': {'p256dh': b64(public), 'auth': b64(auth)}}
        watch.validate_subscription(subscription)
        payload = {'title': 'Early start', 'url': '/?tournament=2PQGYYGY'}
        with patch.dict(os.environ, {'VAPID_PRIVATE_KEY': vapid, 'VAPID_SUBJECT': 'https://example.com'}), patch('requests.post') as post:
            post.return_value.status_code = 201
            self.assertEqual(watch.push_sender(subscription, payload), 'sent')
            _, kwargs = post.call_args
            decrypted = http_ece.decrypt(kwargs['data'], private_key=receiver, auth_secret=auth, version='aes128gcm')
            self.assertEqual(json.loads(decrypted), payload)
            headers = {k.lower(): v for k, v in kwargs['headers'].items()}
            self.assertTrue(headers['authorization'].startswith('vapid '))
            self.assertEqual(headers['urgency'], 'high')
            self.assertEqual(kwargs['timeout'], 8)
