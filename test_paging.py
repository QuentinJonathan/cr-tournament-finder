#!/usr/bin/env python3
"""
Test: Prüft ob die Clash Royale API Paging für Tournament-Suche unterstützt.

Führe aus: python3 test_paging.py
"""

import requests
import json

with open('config.json', 'r') as f:
    config = json.load(f)
    API_KEY = config['api_key']

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}
API_BASE = "https://api.clashroyale.com/v1"

def test_paging(q):
    print(f"\n{'='*50}")
    print(f"TEST: Query '{q}'")
    print('='*50)

    all_tags = set()
    page = 1
    after = None

    while True:
        params = {"name": q, "limit": 25}
        if after:
            params["after"] = after

        resp = requests.get(f"{API_BASE}/tournaments", headers=headers, params=params)

        if resp.status_code == 403:
            print("ERROR 403: API-Key ist IP-gebunden!")
            print("-> Erstelle einen neuen Key auf https://developer.clashroyale.com")
            return None
        elif resp.status_code != 200:
            print(f"ERROR {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        items = data.get('items', [])
        paging = data.get('paging', {})

        new_tags = {t['tag'] for t in items}
        truly_new = new_tags - all_tags
        all_tags.update(new_tags)

        print(f"\nSeite {page}:")
        print(f"  Ergebnisse: {len(items)}")
        print(f"  Davon NEU: {len(truly_new)}")
        print(f"  Gesamt bisher: {len(all_tags)}")
        print(f"  Paging-Objekt: {paging}")

        after = paging.get('cursors', {}).get('after')

        if not after:
            print(f"\n-> Kein 'after'-Cursor mehr vorhanden")
            break

        if page >= 5:
            print(f"\n-> Abbruch nach {page} Seiten (Test-Limit)")
            break

        page += 1

    print(f"\n{'='*50}")
    print(f"ERGEBNIS für '{q}': {len(all_tags)} Turniere total")

    if page > 1:
        print("✅ PAGING FUNKTIONIERT!")
    else:
        print("❌ Kein Paging - nur 1 Seite verfügbar")

    return all_tags

if __name__ == "__main__":
    print("\n" + "="*50)
    print("CLASH ROYALE API - PAGING TEST")
    print("="*50)

    # Test mit verschiedenen Queries
    test_paging("aa")      # 2-Buchstaben (sollte viele Treffer haben)
    test_paging("torneo")  # Beliebtes Wort
