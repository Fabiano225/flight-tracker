"""Website-only follow-up; base scan and Telegram remain separate."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracker.baggage import scan_baggage
from tracker.config import Settings
from tracker.provider import FreeProvider, GuardedClient, cache_file
from tracker.store import Store, utcnow

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--state-dir',default='state')
    args=parser.parse_args()
    trips=Settings.load(args.config).searches()
    # All trips share one bounded client: 360 requests and 10 minutes per run.
    http=GuardedClient(replace(trips[0],max_http_attempts_per_run=360,max_run_seconds=600))
    results={}
    try:
        with Store(args.state_dir) as store:
            for trip in trips:
                provider=FreeProvider(trip,cache_path=cache_file(args.state_dir,trip),http=http)
                results[f'{trip.id} to {trip.destination}']=scan_baggage(trip,store,provider,utcnow())
    finally:
        http.close()
    print(json.dumps(next(iter(results.values())) if len(trips)==1 else results))
    sys.exit(1 if any(result['status']=='partial' for result in results.values()) else 0)
