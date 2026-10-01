"""Build an allowlisted, credential-free Pages artifact from read-only live state."""
import argparse
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
import hashlib
import html
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tracker.config import Config, INT_LIMITS, FLOAT_LIMITS, MAX_DISPLAY_NAME
from tracker.places import AIRPORTS, places
from tracker.alerts import search_link
from tracker.provider import Quote
from tracker.baggage import PROFILES
from tracker.fare_baggage import covers, public_baggage
from scripts.search_settings import CLASSES, LABELS, MARKER

ASSETS = ('index.html', 'styles.css', 'app.js', 'model.mjs', 'favicon.svg', '.nojekyll',
          'suche.html', 'search.js', 'search-model.mjs')
GENERATED = ('data.json', 'search-config.json', 'airports.json')
MONTHS = ('Jan.', 'Feb.', 'März', 'Apr.', 'Mai', 'Juni', 'Juli', 'Aug.', 'Sept.', 'Okt.', 'Nov.', 'Dez.')
RAW_PLACEHOLDERS = {'route_origins'}


def euro_text(value):
    amount = Decimal(str(value))
    if amount == amount.to_integral_value():
        return f'{int(amount):,} €'.replace(',', '.')
    return f'{amount:,.2f} €'.replace(',', '_').replace('.', ',').replace('_', '.')


def date_range(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    month = lambda d: MONTHS[d.month - 1]
    if a == b:
        return f'{a.day}. {month(a)} {a.year}'
    if (a.year, a.month) == (b.year, b.month):
        return f'{a.day}.–{b.day}. {month(b)} {b.year}'
    if a.year == b.year:
        return f'{a.day}. {month(a)} – {b.day}. {month(b)} {b.year}'
    return f'{a.day}. {month(a)} {a.year} – {b.day}. {month(b)} {b.year}'


def page_values(config):
    names = places(config)
    city, country = names[config.destination]['city'], names[config.destination]['country']
    cities = list(dict.fromkeys(names[code]['city'] for code in config.origins))
    days = (f'{config.min_trip_days}–{config.max_trip_days} Tage' if config.min_trip_days != config.max_trip_days
            else f"{config.min_trip_days} Tag{'' if config.min_trip_days == 1 else 'e'}")
    minutes = config.max_direction_minutes
    return dict(
        code=config.destination, city=city, region_upper=(country or city).upper(),
        ticket_place=f'{country.upper()} / {config.destination}' if country else config.destination,
        origin_cities=', '.join(cities[:-1]) + ' und ' + cities[-1] if len(cities) > 1 else cities[0],
        route_origins=''.join(f'<span>{html.escape(code)}</span>' for code in config.origins),
        trip_dates=date_range(config.departure_start, config.departure_end), trip_days=days,
        travel_class=CLASSES[config.travel_class],
        budget=(euro_text(config.good_deal_nonstop_eur) if config.good_deal_nonstop_eur == config.good_deal_layover_eur
                else 'Getrennte Ziele'),
        realert=euro_text(config.realert_improvement_eur), history_days=str(config.history_window_days),
        duration_limit=(f'unter {(minutes + 1) // 60} Stunden' if (minutes + 1) % 60 == 0
                        else f'bis {minutes // 60} h {minutes % 60:02d} min'))


def render_page(template, config):
    """Fill {{name}} placeholders; every value except prebuilt markup is HTML-escaped."""
    values = page_values(config)

    def fill(match):
        if match.group(1) not in values:
            raise ValueError(f'Unknown page placeholder {match.group(1)}')
        value = values[match.group(1)]
        return value if match.group(1) in RAW_PLACEHOLDERS else html.escape(value)
    return re.sub(r'\{\{(\w+)\}\}', fill, template)


def repository():
    value = os.environ.get('GITHUB_REPOSITORY', '')
    return value if re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9._-]+', value) else 'Fabiano225/flight-tracker'


def form_data(config):
    # Everything here is already public in config.json; no state or credentials.
    return dict(version=1, repository=repository(), marker=MARKER,
                config={**config.file_dict(), 'display_names': dict(config.display_names)},
                labels=LABELS, travel_classes=CLASSES,
                limits=dict(int=INT_LIMITS, float=FLOAT_LIMITS, display_name=MAX_DISPLAY_NAME))


def instant(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('Observation timestamps must include a timezone')
    return dt.astimezone(timezone.utc)


def export_data(db_path, config, now=None, variant=None):
    if variant is not None and variant not in PROFILES:
        raise ValueError('Unknown baggage profile')
    now = now or datetime.now(timezone.utc)
    public_config = {key: getattr(config, key) for key in (
        'origins', 'destination', 'departure_start', 'departure_end', 'min_trip_days',
        'max_trip_days', 'max_direction_minutes', 'good_deal_nonstop_eur',
        'good_deal_layover_eur', 'history_window_days', 'realert_improvement_eur')}
    result = dict(version=1, generated_at=now.isoformat(), config=public_config,
                  scan=None, offers_as_of=None, offers=[], histories={})
    db = sqlite3.connect(f'{Path(db_path).resolve().as_uri()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        mode = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
        if not mode or mode[0] != 'live':
            raise ValueError('Only live state may be published')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('State integrity check failed')
        scope = config.scope()
        assessments = {}
        if not variant and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='baggage_checks'").fetchone():
            for check in db.execute('SELECT * FROM baggage_checks WHERE scope=? ORDER BY rowid', (scope,)):
                detail = json.loads(check['details'])
                bag = public_baggage(detail.get('baggage'))
                if bag and now-timedelta(hours=12) <= instant(check['observed']) <= now:
                    identity = (check['run_id'],detail.get('itinerary_id'),detail.get('price'))
                    assessments[identity] = dict(bag, checked_at=check['observed'])
        if variant:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='baggage_runs'").fetchone():
                return result
            runs = db.execute('''SELECT *,observed AS started FROM baggage_runs
                WHERE scope=? AND variant=? ORDER BY rowid''', (scope,variant)).fetchall()
        else:
            runs = db.execute('SELECT * FROM runs WHERE scope=? ORDER BY rowid', (scope,)).fetchall()
        latest = max(runs, key=lambda r: instant(r['started'])) if runs else None
        if latest:
            summary = (dict(calendar_queries_ok=latest['completed'],calendar_queries_planned=latest['planned'],
                       errors=[None]*latest['issues']) if variant else json.loads(latest['summary']))
            result['scan'] = dict(at=instant(latest['started']).isoformat(timespec='seconds'), status=latest['status'],
                batches_ok=summary.get('calendar_queries_ok', 0),
                batches_planned=summary.get('calendar_queries_planned', 0),
                verified=summary.get('verified_quotes', 0), issues=len(summary.get('errors', [])))
            if variant:
                base = db.execute('SELECT started FROM runs WHERE id=?',(latest['run_id'],)).fetchone()
                result['base_at'] = instant(base['started']).isoformat(timespec='seconds') if base else None
        since = now - timedelta(days=config.history_window_days)
        if variant:
            rows = db.execute('''SELECT q.* FROM baggage_quotes q JOIN runs r ON r.id=q.run_id
                WHERE q.scope=? AND q.variant=? AND q.departure BETWEEN ? AND ? ORDER BY q.rowid''',
                (scope,variant,config.departure_start,config.departure_end)).fetchall()
        else:
            rows = db.execute('''SELECT q.* FROM quotes q JOIN runs r ON r.id=q.run_id
            WHERE q.scope=? AND q.departure BETWEEN ? AND ? ORDER BY q.rowid''',
            (scope, config.departure_start, config.departure_end)).fetchall()
        by_run = {}
        for row in sorted(rows, key=lambda r: instant(r['observed'])):
            observed = instant(row['observed'])
            if not since <= observed <= now:
                continue
            observed_at = observed.isoformat(timespec='seconds')
            q = Quote(**json.loads(row['details']))
            bag = public_baggage(q.baggage)
            if variant and (not q.itinerary_id or not covers(bag,variant)):
                continue  # Legacy filter prices are not confirmed baggage fares.
            days = (date.fromisoformat(q.return_date) - date.fromisoformat(q.departure)).days
            if (q.origin not in config.origins or not config.min_trip_days <= days <= config.max_trip_days
                    or q.category not in ('nonstop', 'layover') or type(q.price) is not int or q.price <= 0
                    or not 0 < q.outbound_minutes <= config.max_direction_minutes
                    or not 0 < q.inbound_minutes <= config.max_direction_minutes):
                continue
            key = hashlib.sha256(f'{q.origin}|{q.departure}|{q.return_date}|{q.category}'.encode()).hexdigest()[:16]
            if variant:
                key = variant + ':tariff-v1:' + key
            result['histories'].setdefault(key, []).append(dict(at=observed_at, price=q.price))
            # No outbox, message/chat identifiers, arbitrary metadata or raw errors
            # are exported. Research URLs are rebuilt, never trusted from state.
            item = {k: getattr(q, k) for k in ('origin', 'departure', 'return_date', 'category',
                    'price', 'outbound_minutes', 'inbound_minutes', 'outbound_stops', 'inbound_stops', 'airlines')}
            item.update(id=key, days=days, at=observed_at, link=search_link(q, config.destination, config.travel_class))
            item['itinerary_id'] = q.itinerary_id if isinstance(q.itinerary_id,str) and re.fullmatch(r'[a-f0-9]{64}',q.itinerary_id) else None
            if variant:
                item['baggage'] = dict(bag, profile=variant, allowance_confirmed=True, checked_at=observed_at)
            elif q.itinerary_id:
                item['baggage'] = assessments.get((row['run_id'],q.itinerary_id,q.price))
            by_run.setdefault(row['run_id'], []).append(item)
        if by_run:
            # A failed/empty baggage check must not silently reuse old variant prices.
            offers = by_run.get(latest['run_id'],[]) if variant and latest else max(by_run.values(), key=lambda items: items[0]['at'])
            result['offers'] = sorted(offers, key=lambda q: (q['price'], q['origin'], q['departure']))
            result['offers_as_of'] = offers[0]['at'] if offers else None
        return result
    finally:
        db.close()


def build(db_path, output, config_path=ROOT / 'config.json'):
    output = Path(output).resolve()
    # Never copy a repository tree or runtime state into a public artifact.
    if output.exists() and any(output.iterdir()):
        raise ValueError('Build output must be empty; use a fresh output directory')
    config = Config.load(config_path)
    data = export_data(db_path, config)
    data['baggage_profiles'] = {variant: export_data(db_path,config,variant=variant) for variant in PROFILES}
    data['places'] = places(config)
    conclusion = os.environ.get('PUBLIC_TRACK_RUN_CONCLUSION', '')
    if conclusion in ('success', 'failure', 'cancelled', 'timed_out', 'skipped', 'action_required'):
        data['workflow_conclusion'] = conclusion
    output.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(ROOT / 'website' / name, output / name)
    template = (ROOT / 'website' / 'index.html').read_text(encoding='utf-8')
    (output / 'index.html').write_text(render_page(template, config), encoding='utf-8')
    (output / 'data.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    (output / 'search-config.json').write_text(json.dumps(form_data(config), ensure_ascii=False), encoding='utf-8')
    shutil.copyfile(AIRPORTS, output / 'airports.json')
    print(f"Built dashboard: {len(data['offers'])} verified offers; {len(data['histories'])} history series")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='state/history.sqlite3')
    parser.add_argument('--output', default='_site')
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    args = parser.parse_args()
    build(args.state, args.output, args.config)
