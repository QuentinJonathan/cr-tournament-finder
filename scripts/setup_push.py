"""Provision the existing Cloud Run app's watch queue and VAPID secret.

Run with the Google account that owns cr-tournament-finder. Does not deploy code;
use the deployment launcher afterwards. No private keys are printed.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--account', required=True)
parser.add_argument('--project', default='cr-tournament-finder')
parser.add_argument('--region', default='europe-west3')
parser.add_argument('--gcloud', default='/opt/homebrew/share/google-cloud-sdk/bin/gcloud')
args = parser.parse_args()


def run(*command, check=True, capture=True):
    return subprocess.run([args.gcloud, *command, '--project', args.project, '--account', args.account, '--quiet'],
                          check=check, text=True, capture_output=capture)


def exists(*command):
    response = run(*command, check=False)
    if response.returncode == 0:
        return True
    if 'NOT_FOUND' in response.stderr or 'does not exist' in response.stderr:
        return False
    raise RuntimeError(response.stderr)


service = json.loads(run('run', 'services', 'describe', 'cr-tournament-finder', '--region', args.region,
                         '--format=json').stdout)
origin = service['status']['url']
runtime = service['spec']['template']['spec']['serviceAccountName']
identity = f'cr-watch-dispatch@{args.project}.iam.gserviceaccount.com'
print('Enabling Cloud Tasks and preparing the watch queue…', flush=True)
run('services', 'enable', 'cloudtasks.googleapis.com', 'secretmanager.googleapis.com')
if not exists('iam', 'service-accounts', 'describe', identity):
    run('iam', 'service-accounts', 'create', 'cr-watch-dispatch', '--display-name=CR watch task dispatcher')
run('projects', 'add-iam-policy-binding', args.project, '--member=serviceAccount:' + runtime,
    '--role=roles/cloudtasks.enqueuer', '--condition=None')
run('iam', 'service-accounts', 'add-iam-policy-binding', identity,
    '--member=serviceAccount:' + runtime, '--role=roles/iam.serviceAccountUser')
run('run', 'services', 'add-iam-policy-binding', 'cr-tournament-finder', '--region', args.region,
    '--member=serviceAccount:' + identity, '--role=roles/run.invoker')
queue_command = 'update' if exists('tasks', 'queues', 'describe', 'cr-start-watches', '--location', args.region) else 'create'
run('tasks', 'queues', queue_command, 'cr-start-watches', '--location', args.region,
    '--max-concurrent-dispatches=1', '--max-dispatches-per-second=1',
    '--min-backoff=10s', '--max-backoff=60s', '--max-attempts=100', '--max-retry-duration=3600s')
if not exists('secrets', 'describe', 'cr-vapid-private-key'):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    private = ec.generate_private_key(ec.SECP256R1())
    encoded = base64.b64encode(private.private_bytes(serialization.Encoding.DER,
                               serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with tempfile.NamedTemporaryFile() as secret_file:
        secret_file.write(encoded)
        secret_file.flush()
        run('secrets', 'create', 'cr-vapid-private-key', '--replication-policy=automatic',
            '--data-file=' + secret_file.name)
run('secrets', 'add-iam-policy-binding', 'cr-vapid-private-key',
    '--member=serviceAccount:' + runtime, '--role=roles/secretmanager.secretAccessor')
config = {
    'WATCH_QUEUE_PATH': f'projects/{args.project}/locations/{args.region}/queues/cr-start-watches',
    'WATCH_ORIGIN': origin,
    'WATCH_SERVICE_ACCOUNT': identity,
    'WATCH_STATE_BUCKET': 'cr-tournament-finder-config',
    'VAPID_SUBJECT': origin,
}
Path('.runtime').mkdir(exist_ok=True)
Path('.runtime/watch-deploy-env.json').write_text(json.dumps(config, indent=2))
print('Watch queue and VAPID secret prepared. Deployment settings saved locally.', flush=True)
