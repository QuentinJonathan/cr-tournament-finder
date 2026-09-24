"""Authenticated production smoke test. Temporary pin is removed in finally.

Reads the existing login password via gcloud without printing or saving it.
Does not subscribe a device or send a test push.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path
import requests

parser = argparse.ArgumentParser()
parser.add_argument('--live', action='store_true', required=True)
parser.add_argument('--account', required=True)
parser.add_argument('--url', default='https://cr-tournament-finder-98463050344.europe-west3.run.app')
args = parser.parse_args()
password = subprocess.run(['/opt/homebrew/share/google-cloud-sdk/bin/gcloud', 'secrets', 'versions', 'access', 'latest',
                           '--secret=cr-finder-password', '--project=cr-tournament-finder', '--account=' + args.account],
                          check=True, capture_output=True, text=True).stdout.strip()
client = requests.Session()
client.post(args.url + '/login', data={'password': password}, timeout=30).raise_for_status()
del password

def api(path, method='GET', body=None):
    response = client.request(method, args.url + path, json=body, timeout=90)
    response.raise_for_status()
    return response.json()

config = api('/api/push/config')
assert config['enabled'] and config['background'] and config['publicKey'], 'Push configuration not ready'
assert requests.post(args.url + '/internal/watch/tick', timeout=20).status_code == 401
existing = {p['tag'] for p in api('/api/watches')['pins']}
results = api('/api/tournaments/search')
created = None
report = {'pushConfigured': True, 'unauthorizedTickRejected': True, 'samples': []}
try:
    candidates = [t for t in results['tournaments'] if t['status'] == 'inPreparation' and t['tag'] not in existing]
    for candidate in candidates[:5]:
        response = client.post(args.url + '/api/watches', json={'tag': candidate['tag']}, timeout=30)
        if response.status_code == 200:
            created = candidate['tag']
            pin = next(p for p in response.json()['pins'] if p['tag'] == created)
            report['samples'].append({'checkedAt': pin['checkedAt'], 'state': pin['state']})
            break
        if response.status_code != 400:
            response.raise_for_status()
    assert created, 'No preparing tournament available for smoke test'
    for _ in range(8):
        time.sleep(5)
        pin = next(p for p in api('/api/watches')['pins'] if p['tag'] == created)
        sample = {'checkedAt': pin['checkedAt'], 'state': pin['state'], 'failures': pin['failures']}
        report['samples'].append(sample)
        print(json.dumps(sample), flush=True)
        if pin['state'] != 'watching' or len({s['checkedAt'] for s in report['samples']}) >= 3:
            break
    assert len({s['checkedAt'] for s in report['samples']}) >= 2, 'Background tick did not refresh the pin'
finally:
    if created:
        remaining = api('/api/watches', 'DELETE', {'tag': created})
        report['testPinRemoved'] = all(p['tag'] != created for p in remaining['pins'])
    Path('.runtime').mkdir(exist_ok=True)
    Path('.runtime/watch-smoke.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
