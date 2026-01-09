import time
import json
import os
import sys

# Add current directory to path so we can import app
sys.path.append(os.getcwd())

from app import fetch_all_tournaments

def verify():
    print("🚀 Starting verification of improved crawler...")
    start_time = time.time()
    
    tournaments = fetch_all_tournaments()
    
    elapsed = time.time() - start_time
    total = len(tournaments)
    
    print(f"\n📊 RESULTS:")
    print(f"  - Total unique tournaments found: {total}")
    print(f"  - Time taken: {elapsed:.2f} seconds")
    
    torneo_results = [t for t in tournaments if 'torneo' in t.get('name', '').lower()]
    print(f"  - Tournaments with 'torneo' in name: {len(torneo_results)}")
    
    # Check for "BADA" if it's there
    bada_results = [t for t in tournaments if 'bada' in t.get('name', '').lower()]
    print(f"  - Tournaments with 'bada' in name: {len(bada_results)}")
    
    # Check various game modes to ensure broad coverage
    modes = {}
    for t in tournaments:
        m = t.get('gameMode', {}).get('id', 'unknown')
        modes[m] = modes.get(m, 0) + 1
    
    print(f"  - Found {len(modes)} distinct game modes")
    
    if total > 400:
        print("\n✅ Coverage improved! (Prev was ~380)")
    else:
        print("\n⚠️ Coverage similar to before or lower. API may be highly dynamic.")

if __name__ == "__main__":
    verify()
