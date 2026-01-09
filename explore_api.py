#!/usr/bin/env python3
"""
Clash Royale API Explorer
Run with: python explore_api.py
"""

import requests
import json
import os
from urllib.parse import quote

API_BASE = "https://api.clashroyale.com/v1"

# Get API key from environment variable
API_KEY = os.environ.get("CR_API_KEY")

if not API_KEY:
    print("❌ No API key found!")
    print("Set it with: export CR_API_KEY='your_key_here'")
    print("Or paste it below:")
    API_KEY = input("API Key: ").strip()

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}


def pretty_print(data):
    """Pretty print JSON data"""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def search_tournaments(name_query="a", limit=10):
    """Search for tournaments by name"""
    print(f"\n🔍 Searching tournaments with query: '{name_query}'")
    print("-" * 50)

    params = {"name": name_query, "limit": limit}
    resp = requests.get(f"{API_BASE}/tournaments", headers=HEADERS, params=params, timeout=10)

    if resp.status_code != 200:
        print(f"❌ Error {resp.status_code}: {resp.text}")
        return None

    data = resp.json()
    return data


def get_tournament_details(tag):
    """Get details for a specific tournament"""
    # Tags need to be URL encoded (# becomes %23)
    encoded_tag = quote(tag, safe='')
    print(f"\n📋 Getting details for tournament: {tag}")
    print("-" * 50)

    resp = requests.get(f"{API_BASE}/tournaments/{encoded_tag}", headers=HEADERS, timeout=10)

    if resp.status_code != 200:
        print(f"❌ Error {resp.status_code}: {resp.text}")
        return None

    return resp.json()


def explore_tournament_fields(tournament):
    """Show all available fields in a tournament object"""
    print("\n📊 Available fields in tournament object:")
    print("-" * 50)
    for key, value in tournament.items():
        value_type = type(value).__name__
        value_preview = str(value)[:60] + "..." if len(str(value)) > 60 else str(value)
        print(f"  {key} ({value_type}): {value_preview}")


if __name__ == "__main__":
    print("=" * 60)
    print("🏆 CLASH ROYALE API EXPLORER")
    print("=" * 60)

    # Test 1: Search for tournaments
    results = search_tournaments("turnier", limit=5)

    if results and "items" in results:
        tournaments = results["items"]
        print(f"\n✅ Found {len(tournaments)} tournaments")

        if tournaments:
            # Show first tournament raw data
            print("\n🔹 First tournament (RAW JSON):")
            pretty_print(tournaments[0])

            # Analyze fields
            explore_tournament_fields(tournaments[0])

            # Get detailed info for first tournament
            first_tag = tournaments[0].get("tag")
            if first_tag:
                details = get_tournament_details(first_tag)
                if details:
                    print("\n🔹 Tournament DETAILS (may have more fields):")
                    pretty_print(details)

                    # Compare fields
                    print("\n📊 Additional fields in detailed view:")
                    search_keys = set(tournaments[0].keys())
                    detail_keys = set(details.keys())
                    new_keys = detail_keys - search_keys
                    if new_keys:
                        for key in new_keys:
                            print(f"  + {key}: {details[key]}")
                    else:
                        print("  (no additional fields)")

    elif results:
        print("\n⚠️ Response structure:")
        pretty_print(results)

    print("\n" + "=" * 60)
    print("Done! Review the output above to understand the API structure.")
    print("=" * 60)
