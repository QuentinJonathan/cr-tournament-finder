"""Durable, single-user tournament watches. Cloud Tasks runs production ticks.

No browser heartbeat or background CPU is required in production. State updates
use GCS generation preconditions; local development uses an atomic JSON file.
"""
import base64
import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger('TournamentFinder')
INTERVAL = 10
MAX_PINS = 10


def watch_event(event, **fields):
    # Only explicitly selected fields: no API keys, response bodies or push endpoints.
    log.info(json.dumps({'event': event, **fields}, separators=(',', ':')))


class WatchRetryAfter(ValueError):
    def __init__(self, seconds):
        super().__init__('API temporarily unavailable. Please retry later.')
        self.seconds = seconds


def retry_after_seconds(value, now):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return None


def empty_state():
    return {'pins': {}, 'subscriptions': {}}


class StateStore:
    def __init__(self, path, bucket=None):
        self.path = Path(path)
        self.bucket = bucket
        self.lock = threading.RLock()
        self._client = None

    def _blob(self):
        from google.cloud import storage
        if self._client is None:
            self._client = storage.Client()
        return self._client.bucket(self.bucket).blob('watch-state.json')

    def _read(self):
        if self.bucket:
            from google.api_core.exceptions import NotFound
            blob = self._blob()
            try:
                body = blob.download_as_bytes()
                return json.loads(body), int(blob.generation)
            except NotFound:
                return empty_state(), 0
        if self.path.exists():
            return json.loads(self.path.read_text()), None
        return empty_state(), None

    def read(self):
        with self.lock:
            return self._read()[0]

    def update(self, change):
        with self.lock:
            for attempt in range(8):
                state, generation = self._read()
                result = change(state)
                body = json.dumps(state, separators=(',', ':'))
                if self.bucket:
                    from google.api_core.exceptions import PreconditionFailed
                    try:
                        self._blob().upload_from_string(body, content_type='application/json',
                                                        if_generation_match=generation)
                    except PreconditionFailed:
                        continue
                else:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temp = self.path.with_suffix('.tmp')
                    temp.write_text(body)
                    temp.chmod(0o600)
                    temp.replace(self.path)
                return result
            raise RuntimeError('Watch state is busy; please retry')


def normalize_tag(tag):
    tag = str(tag or '').strip().upper().lstrip('#')
    if not re.fullmatch(r'[0289PYLQGRJCUV]{3,16}', tag):
        raise ValueError('Invalid tournament tag')
    return '#' + tag


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid push subscription')
    endpoint = value.get('endpoint', '')
    parsed = urlparse(endpoint)
    # Never POST user-supplied subscription data to arbitrary/internal URLs.
    host = parsed.hostname or ''
    allowed = (host == 'web.push.apple.com' or host.endswith('.push.apple.com') or
               host == 'fcm.googleapis.com' or host == 'updates.push.services.mozilla.com')
    if (not allowed or parsed.scheme != 'https' or parsed.port not in (None, 443) or
            parsed.username or parsed.password or len(endpoint) > 4096):
        raise ValueError('Unsupported push endpoint')
    keys = value.get('keys', {})
    for name, length in [('p256dh', 65), ('auth', 16)]:
        encoded = keys.get(name, '')
        if not isinstance(encoded, str) or len(encoded) > 128:
            raise ValueError('Invalid push keys')
        try:
            decoded = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        except Exception as exc:
            raise ValueError('Invalid push keys') from exc
        if len(decoded) != length:
            raise ValueError('Invalid push keys')
    return {'endpoint': endpoint, 'keys': {k: keys[k] for k in ('p256dh', 'auth')}}


class WatchService:
    def __init__(self, store, fetch_detail, serialize, send_push=None, schedule=None, clock=time.time):
        self.store, self.fetch_detail, self.serialize = store, fetch_detail, serialize
        self.send_push, self.schedule, self.clock = send_push, schedule, clock
        self.tick_lock = threading.Lock()
        self.local_lock = threading.Lock()
        self.local_thread = None

    def public_state(self):
        state = self.store.read()
        return {'pins': list(state['pins'].values()), 'intervalSeconds': INTERVAL}

    def pin(self, tag):
        tag = normalize_tag(tag)
        existing = self.store.read()['pins'].get(tag)
        if existing:
            self.kick()
            return existing
        detail = self.fetch_detail(tag)
        if not detail:
            raise ValueError('Tournament could not be checked. Please retry.')
        if detail.get('status') != 'inPreparation':
            raise ValueError('This tournament is no longer preparing. Refresh its details.')
        now = self.clock()
        pin = {'id': uuid.uuid4().hex, 'tag': tag, 'state': 'watching',
               'tournament': self.serialize(detail), 'createdAt': now,
               'expiresAt': now + 86400, 'checkedAt': now, 'failures': 0,
               'nextCheckAt': (int(now // INTERVAL) + 1) * INTERVAL, 'delivered': []}
        def add(state):
            if tag in state['pins']:
                return state['pins'][tag]
            if len(state['pins']) >= MAX_PINS:
                raise ValueError('Remove a pin first (maximum 10).')
            state['pins'][tag] = pin
            return pin
        result = self.store.update(add)
        self.kick()
        return result

    def remove(self, tag):
        self.store.update(lambda state: state['pins'].pop(normalize_tag(tag), None))

    def subscribe(self, value):
        subscription = validate_subscription(value)
        key = hashlib.sha256(subscription['endpoint'].encode()).hexdigest()
        def save(state):
            if key not in state['subscriptions'] and len(state['subscriptions']) >= 10:
                raise ValueError('Too many registered devices')
            state['subscriptions'][key] = subscription
        self.store.update(save)
        return key

    def unsubscribe(self, endpoint):
        key = hashlib.sha256(str(endpoint).encode()).hexdigest()
        self.store.update(lambda state: state['subscriptions'].pop(key, None))

    def needs_work(self, state):
        now = self.clock()
        return any(p['state'] == 'watching' or
                   (p['state'] == 'live' and now < p.get('detectedAt', 0) + 300 and
                    any(k not in p['delivered'] for k in state['subscriptions']))
                   for p in state['pins'].values())

    def kick(self):
        if self.schedule:
            self.schedule(0, uuid.uuid4().hex)
        else:
            with self.local_lock:
                if self.local_thread and self.local_thread.is_alive():
                    return
                self.local_thread = threading.Thread(target=self._local_loop, daemon=True)
                self.local_thread.start()

    def _local_loop(self):
        # Local mode runs while the Python process is alive, even with no browser.
        while True:
            try:
                self.tick()
            except Exception:
                log.error('Local watch tick failed', exc_info=False)
            with self.local_lock:
                if not self.needs_work(self.store.read()):
                    self.local_thread = None
                    return
            time.sleep(INTERVAL)

    def tick(self):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            state = self.store.read()
            if not self.needs_work(state):
                return
            if self.schedule:
                # Enqueue BEFORE API/push work: crashes and request timeouts cannot
                # silently kill the chain. Deterministic slots merge duplicate ticks.
                slot = int(self.clock() // INTERVAL) + 1
                self.schedule(max(1, slot * INTERVAL - self.clock()), 'slot-' + str(slot))
            due = {tag: pin for tag, pin in state['pins'].items()
                   if pin['state'] == 'watching' and self.clock() >= pin.get('nextCheckAt', 0)}
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {tag: executor.submit(self.fetch_detail, tag) for tag, pin in due.items()
                           if self.clock() < pin['expiresAt']}
                details = {}
                for tag, future in futures.items():
                    try:
                        details[tag] = future.result()
                    except WatchRetryAfter as exc:
                        details[tag] = exc
                    except Exception:
                        details[tag] = None
            for tag, pin in due.items():
                if pin['state'] != 'watching' or self.clock() < pin.get('nextCheckAt', 0):
                    continue
                now = self.clock()
                detail = details.get(tag)
                def apply(current):
                    p = current['pins'].get(tag)
                    if not p or p['id'] != pin['id'] or p['state'] != 'watching':
                        return
                    if now >= p['expiresAt']:
                        p['state'] = 'expired'
                        return copy.deepcopy(p)
                    retry = detail.seconds if isinstance(detail, WatchRetryAfter) else None
                    if retry is not None or not detail or detail.get('status') not in ('inPreparation', 'inProgress', 'ended'):
                        p['failures'] += 1
                        p['nextCheckAt'] = (now + max(INTERVAL, retry) if retry is not None
                                            else (int(now // INTERVAL) + 1) * INTERVAL)
                        return copy.deepcopy(p)
                    p.update(tournament=self.serialize(detail), checkedAt=now, failures=0,
                             nextCheckAt=(int(now // INTERVAL) + 1) * INTERVAL)
                    if detail['status'] == 'inProgress':
                        p.update(state='live', detectedAt=now)
                    elif detail['status'] == 'ended':
                        p['state'] = 'ended'
                    return copy.deepcopy(p)
                saved = self.store.update(apply)
                if saved and saved['id'] == pin['id']:
                    watch_event('watch_decision', tag=tag, watchId=pin['id'], at=self.clock(),
                                state=saved['state'], failures=saved['failures'],
                                nextCheckAt=saved.get('nextCheckAt'), detectedAt=saved.get('detectedAt'))
            self.deliver()
        finally:
            self.tick_lock.release()

    def deliver(self):
        if not self.send_push:
            return
        state = self.store.read()
        for tag, pin in state['pins'].items():
            if pin['state'] != 'live' or self.clock() >= pin['detectedAt'] + 300:
                continue
            for key, subscription in state['subscriptions'].items():
                if key in pin['delivered']:
                    continue
                payload = {'title': 'Turnier gestartet!',
                           'body': (pin['tournament'].get('name') or tag) + ' ist jetzt live.',
                           'tag': 'cr-start-' + pin['id'],
                           'url': '/?tournament=' + tag.lstrip('#')}
                try:
                    watch_event('watch_push_attempt', tag=tag, watchId=pin['id'], at=self.clock())
                    outcome = self.send_push(subscription, payload)
                except Exception:
                    # Push subscription endpoints are credentials; never log them.
                    watch_event('watch_push_failed', tag=tag, watchId=pin['id'], at=self.clock())
                    continue
                watch_event('watch_push_result', tag=tag, watchId=pin['id'], at=self.clock(),
                            outcome=outcome, detectedAt=pin['detectedAt'])
                def record(current):
                    if outcome == 'gone':
                        current['subscriptions'].pop(key, None)
                    p = current['pins'].get(tag)
                    if p and p['id'] == pin['id'] and key not in p['delivered']:
                        p['delivered'].append(key)
                self.store.update(record)


def vapid_keys():
    from cryptography.hazmat.primitives import serialization
    encoded = os.environ.get('VAPID_PRIVATE_KEY', '')
    if not encoded:
        return None, None
    private = serialization.load_der_private_key(base64.b64decode(encoded), password=None)
    public = private.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint)
    return encoded, base64.urlsafe_b64encode(public).decode().rstrip('=')


def push_sender(subscription, payload):
    from pywebpush import webpush, WebPushException
    private, _ = vapid_keys()
    if not private:
        raise RuntimeError('Push is not configured')
    try:
        webpush(subscription_info=subscription, data=json.dumps(payload),
                vapid_private_key=private,
                vapid_claims={'sub': os.environ['VAPID_SUBJECT']},
                ttl=120, timeout=8, headers={'Urgency': 'high'})
        return 'sent'
    except WebPushException as exc:
        if exc.response is not None and exc.response.status_code in (404, 410):
            return 'gone'
        raise RuntimeError('Push service unavailable') from None


def cloud_schedule(delay, task_id):
    from google.cloud import tasks_v2
    from google.protobuf.timestamp_pb2 import Timestamp
    from google.api_core.exceptions import AlreadyExists
    parent = os.environ['WATCH_QUEUE_PATH']
    origin = os.environ['WATCH_ORIGIN'].rstrip('/')
    timestamp = Timestamp()
    timestamp.FromMilliseconds(int((time.time() + delay) * 1000))
    task = {'name': parent + '/tasks/watch-' + task_id, 'schedule_time': timestamp,
            'http_request': {'http_method': tasks_v2.HttpMethod.POST,
                             'url': origin + '/internal/watch/tick',
                             'oidc_token': {'service_account_email': os.environ['WATCH_SERVICE_ACCOUNT'],
                                            'audience': origin}}}
    try:
        tasks_v2.CloudTasksClient().create_task(parent=parent, task=task, timeout=10)
    except AlreadyExists:
        pass


def verify_task_request(authorization):
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token
    if not authorization.startswith('Bearer ') or not os.environ.get('WATCH_QUEUE_PATH'):
        return False
    try:
        claims = id_token.verify_oauth2_token(authorization[7:], Request(), os.environ['WATCH_ORIGIN'].rstrip('/'))
        return (claims.get('email') == os.environ['WATCH_SERVICE_ACCOUNT'] and
                claims.get('email_verified') is True)
    except Exception:
        return False
