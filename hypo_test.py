import requests
import json
import os

with open('config.json', 'r') as f:
    config = json.load(f)
    API_KEY = config['api_key']

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}
API_BASE = "https://api.clashroyale.com/v1"

def test_query(q):
    resp = requests.get(f"{API_BASE}/tournaments", headers=headers, params={"name": q, "limit": 100})
    if resp.status_code == 200:
        items = resp.json().get('items', [])
        print(f"Query '{q}': {len(items)} results")
        for item in items:
            print(f"  - {item['name']} ({item['tag']})")
        return items
    else:
        print(f"Query '{q}': Error {resp.status_code}")
        return []

print("--- Testing 'bad' ---")
bad_results = test_query("bad")
bad_tags = {t['tag'] for t in bad_results}

print("\n--- Testing 'bada' ---")
bada_results = test_query("bada")
bada_tags = {t['tag'] for t in bada_results}

missing_in_bad = bada_tags - bad_tags
if missing_in_bad:
    print(f"\n⚠️ FOUND INCONSISTENCY! {len(missing_in_bad)} tournaments found in 'bada' but NOT in 'bad'")
    for tag in missing_in_bad:
        for t in bada_results:
            if t['tag'] == tag:
                 print(f"  - {t['name']} ({t['tag']})")
else:
    print("\n✅ Consistency check passed: all 'bada' results were in 'bad' results.")

print("\n--- Testing 'b' (single char) ---")
b_results = test_query("b")
b_tags = {t['tag'] for t in b_results}
missing_in_b = bada_tags - b_tags
if missing_in_b:
    print(f"⚠️ {len(missing_in_b)} 'bada' results MISSING in 'b' search")
