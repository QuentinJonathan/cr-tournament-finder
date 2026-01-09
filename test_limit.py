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

def test_limit(q, limit):
    resp = requests.get(f"{API_BASE}/tournaments", headers=headers, params={"name": q, "limit": limit})
    if resp.status_code == 200:
        data = resp.json()
        items = data.get('items', [])
        print(f"Query '{q}' with limit={limit}: {len(items)} items")
        # Check for paging fields
        paging = data.get('paging', {})
        if paging:
            print(f"  Paging: {paging}")
        return items
    return []

print("--- Testing 'a' with different limits ---")
test_limit("a", 10)
test_limit("a", 20)
test_limit("a", 50)
test_limit("a", 100)

print("\n--- Testing 'torneo' with different limits ---")
test_limit("torneo", 10)
test_limit("torneo", 100)
