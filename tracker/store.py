from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import uuid


# Observations are deleted once no comparison reads them. Alerts, delivery
# receipts and runs are kept: they are small and anchor "since the last alert".
MIN_WINDOW_DAYS = 31
QUOTE_RETENTION_DAYS = 120
SIZE_LIMIT_BYTES = 60_000_000
SAFETY_CALENDAR_DAYS = 7


def utcnow():
    return datetime.now(timezone.utc)


def stamp(now):
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, directory, mode="live"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "history.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1}:
            self.db.close()
            raise ValueError("Unknown database version; preserve state and update code")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY, started TEXT NOT NULL, scope TEXT NOT NULL,
            status TEXT NOT NULL, summary TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS calendar(
            run_id TEXT REFERENCES runs(id), scope TEXT NOT NULL, observed TEXT NOT NULL,
            origin TEXT NOT NULL, departure TEXT NOT NULL, return_date TEXT NOT NULL,
            profile TEXT NOT NULL, price INTEGER,
            PRIMARY KEY(run_id,origin,departure,return_date,profile));
          CREATE INDEX IF NOT EXISTS calendar_history ON calendar(scope,origin,departure,return_date,profile,observed);
          CREATE TABLE IF NOT EXISTS quotes(
            run_id TEXT REFERENCES runs(id), scope TEXT NOT NULL, observed TEXT NOT NULL,
            origin TEXT NOT NULL, departure TEXT NOT NULL, return_date TEXT NOT NULL,
            category TEXT NOT NULL, price INTEGER NOT NULL, details TEXT NOT NULL,
            PRIMARY KEY(run_id,origin,departure,return_date,category));
          CREATE INDEX IF NOT EXISTS quote_history ON quotes(scope,origin,departure,return_date,category,observed);
          CREATE TABLE IF NOT EXISTS outbox(
            id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), kind TEXT NOT NULL,
            created TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL,
            sent TEXT, message_id TEXT);
          CREATE TABLE IF NOT EXISTS alert_items(
            outbox_id TEXT REFERENCES outbox(id), scope TEXT NOT NULL, origin TEXT NOT NULL,
            departure TEXT NOT NULL, return_date TEXT NOT NULL, category TEXT NOT NULL,
            price INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox_replies(
            outbox_id TEXT PRIMARY KEY REFERENCES outbox(id),
            target_id TEXT NOT NULL REFERENCES outbox(id));
          CREATE TABLE IF NOT EXISTS delivery_receipts(
            outbox_id TEXT REFERENCES outbox(id), destination TEXT NOT NULL,
            message_id TEXT NOT NULL, sent TEXT NOT NULL,
            PRIMARY KEY(outbox_id,destination));
          PRAGMA user_version=1;
        """)
        stored_mode = self.get_meta("mode")
        if stored_mode and stored_mode != mode:
            self.db.close()
            raise ValueError("Demo and live history must use different state directories")
        self.set_meta("mode", mode)
        self.db.commit()

    def get_meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def previous_low(self, table, scope, origin, dep, ret, category, now, window, run_id):
        if table not in {"calendar", "quotes"}:
            raise ValueError("Unknown history table")
        field = "profile" if table == "calendar" else "category"
        return self.db.execute(f"""SELECT MIN(price) FROM {table}
          WHERE scope=? AND origin=? AND departure=? AND return_date=? AND {field}=?
          AND observed>=? AND observed<=? AND run_id<>?""",
          (scope, origin, dep, ret, category, stamp(now - timedelta(days=window)), stamp(now), run_id)).fetchone()[0]

    def alerted_low(self, scope, quote):
        return self.db.execute("""SELECT MIN(a.price) FROM alert_items a JOIN outbox o ON o.id=a.outbox_id
          WHERE a.scope=? AND a.origin=? AND a.departure=? AND a.return_date=? AND a.category=?
          AND o.status IN ('sent','pending')""",
          (scope, quote.origin, quote.departure, quote.return_date, quote.category)).fetchone()[0]

    def expire(self, now, ttl_hours, scopes=None):
        """Expire stale messages, and alerts of searches that are no longer active.

        `scopes` names every active trip's scope; alerts of other scopes expire."""
        self.db.execute("UPDATE outbox SET status='expired' WHERE status='pending' AND created<?",
                        (stamp(now - timedelta(hours=ttl_hours)),))
        if scopes:
            scopes = [scopes] if isinstance(scopes, str) else sorted(scopes)
            self.db.execute(f"""UPDATE outbox SET status='expired' WHERE status='pending' AND kind IN ('deal','trend','check_status')
              AND id IN (SELECT outbox_id FROM alert_items WHERE scope NOT IN ({','.join('?' * len(scopes))}))""", scopes)

    def expire_check_status(self, scope):
        """A newer check status replaces an undelivered older one of the same trip."""
        self.db.execute("""UPDATE outbox SET status='expired' WHERE kind='check_status' AND status='pending'
          AND run_id IN (SELECT id FROM runs WHERE scope=?)""", (scope,))

    def enqueue(self, run_id, kind, now, text, quotes=(), scope=""):
        message_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO outbox(id,run_id,kind,created,status,text) VALUES(?,?,?,?,?,?)",
                        (message_id, run_id, kind, stamp(now), "pending", text))
        self.db.executemany("INSERT INTO alert_items VALUES(?,?,?,?,?,?,?)",
            [(message_id, scope, q.origin, q.departure, q.return_date, q.category, q.price) for q in quotes])
        return message_id

    def expire_outside_search(self, trips):
        # Preserve compatible price history when dates change, but never deliver
        # a previously queued digest containing a now-excluded trip.
        mode = self.get_meta('mode') or 'live'
        by_scope = {config.scope(mode): config for config in trips}
        for message in self.db.execute("SELECT id,kind,run_id FROM outbox WHERE status='pending' AND kind IN ('deal','trend','check_status')").fetchall():
            items=self.db.execute("SELECT scope,origin,departure,return_date FROM alert_items WHERE outbox_id=?",(message[0],)).fetchall()
            # A message belongs to the trip of its items' scope; a check status to the trip of its run.
            run = None
            if message['kind'] == 'check_status':
                run = self.db.execute('SELECT scope,summary FROM runs WHERE id=?', (message['run_id'],)).fetchone()
                scope = run['scope'] if run else None
            else:
                scope = items[0]['scope'] if items else None
            config = by_scope.get(scope)
            valid = config is not None and all(item['scope'] == scope for item in items)
            if valid and run:
                settings = json.loads(run['summary']).get('config', {})
                valid = all(settings.get(k) == config.public_dict()[k] for k in
                            ('departure_start', 'departure_end', 'min_trip_days', 'max_trip_days'))
                valid = valid and settings.get('origins') == list(config.origins)
            for item in items if valid else ():
                try:
                    duration=(datetime.fromisoformat(item['return_date'])-datetime.fromisoformat(item['departure'])).days
                    valid = valid and item['origin'] in config.origins and config.departure_start <= item['departure'] <= config.departure_end and config.min_trip_days <= duration <= config.max_trip_days
                except (ValueError,TypeError):
                    valid=False
            if not valid:
                self.db.execute("UPDATE outbox SET status='expired' WHERE id=?",(message[0],))

    def prune(self, now, window_days):
        """Delete observations that no price comparison reads anymore and shrink the file."""
        keep = max(window_days, MIN_WINDOW_DAYS) + 2
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        limits = [("calendar", keep), ("quotes", max(keep, QUOTE_RETENTION_DAYS)),
                  # The website reads baggage checks of the last 12 hours only.
                  ("baggage_checks", 2), ("baggage_quotes", keep), ("baggage_runs", keep)]
        removed = 0
        for table, days in limits:
            if table in tables:
                removed += self.db.execute(f"DELETE FROM {table} WHERE observed<?",
                                           (stamp(now - timedelta(days=days)),)).rowcount
        self.db.commit()
        if removed:
            self.db.execute("VACUUM")
        if (self.directory / "history.sqlite3").stat().st_size > SIZE_LIMIT_BYTES:
            # Very wide searches: the date grid only ranks candidates, so it may shrink first.
            removed += self.db.execute("DELETE FROM calendar WHERE observed<?",
                                       (stamp(now - timedelta(days=SAFETY_CALENDAR_DAYS)),)).rowcount
            self.db.commit()
            self.db.execute("VACUUM")
        return removed

    def close(self):
        self.db.commit()
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.db.rollback()
        self.close()
