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

def test_case(q):
    resp = requests.get(f"{API_BASE}/tournaments", headers=headers, params={"name": q, "limit": 100})
    if resp.status_code == 200:
        items = resp.json().get('items', [])
        print(f"Query '{q}': {len(items)} results")
        return items
    return []

print("--- Case Sensitivity Test ---")
test_case("bada")
test_case("BADA")
test_case("Bada")

print("\n--- Common Search Test ---")
test_case("torneo")
test_case("TORNEO")
