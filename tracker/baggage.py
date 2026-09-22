"""Bounded website-only baggage searches. Never creates Telegram alerts."""
from datetime import date, timedelta
import json

from .network import BudgetError, ServiceError
from .provider import Quote
from .fare_baggage import covers, public_baggage
from .store import stamp

PROFILES = {'cabin': (1, 0), 'checked': (0, 1), 'both': (1, 1)}


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS baggage_checks(
            run_id TEXT, scope TEXT, observed TEXT, details TEXT);
        CREATE TABLE IF NOT EXISTS baggage_runs(
            run_id TEXT REFERENCES runs(id), variant TEXT, scope TEXT, observed TEXT,
            status TEXT, planned INTEGER, completed INTEGER, issues INTEGER,
            PRIMARY KEY(run_id,variant));
        CREATE TABLE IF NOT EXISTS baggage_quotes(
            run_id TEXT, variant TEXT, scope TEXT, observed TEXT,
            origin TEXT, departure TEXT, return_date TEXT, category TEXT,
            price INTEGER, details TEXT,
            PRIMARY KEY(run_id,variant,origin,departure,return_date,category));
    ''')


def eligible(q, config, today):
    return (q.origin in config.origins and config.departure_start <= q.departure <= config.departure_end
            and q.departure > today.isoformat()
            and config.min_trip_days <= (date.fromisoformat(q.return_date)-date.fromisoformat(q.departure)).days <= config.max_trip_days
            and q.category in ('layover', 'nonstop') and type(q.price) is int and q.price > 0
            and 0 < q.outbound_minutes <= config.max_direction_minutes
            and 0 < q.inbound_minutes <= config.max_direction_minutes)


def scan_baggage(config, store, provider, now):
    """Check actual booking offers once, then project supported baggage profiles."""
    db = store.db
    initialize(db)
    run = db.execute('SELECT * FROM runs WHERE scope=? ORDER BY started DESC,rowid DESC LIMIT 1',
                     (config.scope(),)).fetchone()
    if not run or run['started'] < stamp(now-timedelta(hours=12)) or run['status'] not in ('ok','partial'):
        return {'status': 'skipped', 'reason': 'No recent completed base search'}
    rows = db.execute('SELECT details FROM quotes WHERE run_id=? ORDER BY price,origin,departure', (run['id'],)).fetchall()
    quotes = [Quote(**json.loads(r['details'])) for r in rows]
    selected = [(q.origin,q.departure,q.return_date,'nonstop' if q.category=='nonstop' else 'any')
                for q in quotes if eligible(q,config,now.date())][:config.max_verifications_per_run]
    db.execute('DELETE FROM baggage_checks WHERE run_id=?',(run['id'],))
    for variant in PROFILES:
        db.execute('DELETE FROM baggage_quotes WHERE run_id=? AND variant=?',(run['id'],variant))
        db.execute('INSERT OR REPLACE INTO baggage_runs VALUES(?,?,?,?,?,?,?,?)',
                   (run['id'],variant,config.scope(),stamp(now),'running',len(selected),0,0))
    db.commit()
    stopped = False
    error_streak = 0
    try:
        for origin, dep, ret, profile in selected:
            try:
                found = provider.baggage_offers(origin,dep,ret,profile)
                for q in found:
                    if (not eligible(q,config,now.date()) or (q.origin,q.departure,q.return_date)!=(origin,dep,ret)
                            or (profile=='nonstop' and q.category!='nonstop') or not q.itinerary_id
                            or not public_baggage(q.baggage)):
                        continue
                    db.execute('INSERT INTO baggage_checks VALUES(?,?,?,?)',
                        (run['id'],config.scope(),stamp(now),json.dumps(q.to_dict())))
                    for variant in PROFILES:
                        if not covers(q.baggage,variant):
                            continue
                        db.execute('''INSERT INTO baggage_quotes VALUES(?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(run_id,variant,origin,departure,return_date,category)
                            DO UPDATE SET price=excluded.price,details=excluded.details
                            WHERE excluded.price<baggage_quotes.price''',
                            (run['id'],variant,config.scope(),stamp(now),q.origin,q.departure,q.return_date,
                             q.category,q.price,json.dumps(q.to_dict())))
                db.execute('UPDATE baggage_runs SET completed=completed+1 WHERE run_id=?',(run['id'],))
                error_streak = 0
            except ServiceError as exc:
                db.execute('UPDATE baggage_runs SET issues=issues+1 WHERE run_id=?',(run['id'],))
                error_streak += 1
                stopped = isinstance(exc,BudgetError) or error_streak>=3
            db.commit()
            if stopped:
                break
    finally:
        db.execute('''UPDATE baggage_runs SET status=CASE
            WHEN planned>0 AND completed=planned AND issues=0 THEN 'ok' ELSE 'partial' END
            WHERE run_id=?''',(run['id'],))
        db.commit()
        provider.close()
    stats = [dict(r) for r in db.execute('SELECT variant,status,planned,completed,issues FROM baggage_runs WHERE run_id=?',(run['id'],))]
    return {'status':'partial' if any(r['status']!='ok' for r in stats) else 'ok', 'profiles':stats,
            'http_attempts':provider.http.used}
