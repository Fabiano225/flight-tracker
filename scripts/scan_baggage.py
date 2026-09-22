"""Website-only follow-up; base scan and Telegram remain separate."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracker.baggage import scan_baggage
from tracker.config import Config
from tracker.provider import FreeProvider
from tracker.store import Store, utcnow

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--state-dir',default='state')
    args=parser.parse_args()
    config=Config.load(args.config)
    bounded=replace(config,max_http_attempts_per_run=360,max_run_seconds=600)
    with Store(args.state_dir) as store:
        provider=FreeProvider(bounded)
        try:
            result=scan_baggage(config,store,provider,utcnow())
            print(json.dumps(result))
        finally:
            provider.close()
    sys.exit(1 if result['status']=='partial' else 0)
