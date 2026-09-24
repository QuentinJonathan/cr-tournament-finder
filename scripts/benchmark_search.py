"""Explicit live A/B/A coverage check. Uses the application's configured API key."""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

parser = argparse.ArgumentParser()
parser.add_argument('--live', action='store_true', required=True, help='Send live API searches (legacy/adaptive/legacy)')
args = parser.parse_args()
app.logger.setLevel(logging.WARNING)
runs = []
for strategy in ('legacy', 'adaptive', 'legacy'):
    os.environ['SEARCH_STRATEGY'] = strategy
    started = time.monotonic()
    result = app.fetch_all_tournaments()
    record = {'strategy': strategy, 'seconds': round(time.monotonic() - started, 2),
              'tags': sorted(t['tag'] for t in result), 'stats': app.build_search_stats_payload(app.search_stats)}
    runs.append(record)
    print(json.dumps({k: v for k, v in record.items() if k != 'tags'} | {'tournaments': len(result)}), flush=True)
stable = set(runs[0]['tags']) & set(runs[2]['tags'])
missing = sorted(stable - set(runs[1]['tags']))
report = {'runs': runs, 'stableBaselineTags': len(stable), 'missingStableTags': missing}
Path('.runtime').mkdir(exist_ok=True)
Path('.runtime/search-benchmark.json').write_text(json.dumps(report, indent=2))
print(json.dumps({'stableBaselineTags': len(stable), 'missingStableTags': missing}), flush=True)
