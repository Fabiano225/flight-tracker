from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import uuid


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

    def expire(self, now, ttl_hours, scope=None):
        self.db.execute("UPDATE outbox SET status='expired' WHERE status='pending' AND created<?",
                        (stamp(now - timedelta(hours=ttl_hours)),))
        if scope:
            self.db.execute("""UPDATE outbox SET status='expired' WHERE status='pending' AND kind IN ('deal','trend','check_status')
              AND id IN (SELECT outbox_id FROM alert_items WHERE scope<>?)""", (scope,))

    def enqueue(self, run_id, kind, now, text, quotes=(), scope=""):
        message_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO outbox(id,run_id,kind,created,status,text) VALUES(?,?,?,?,?,?)",
                        (message_id, run_id, kind, stamp(now), "pending", text))
        self.db.executemany("INSERT INTO alert_items VALUES(?,?,?,?,?,?,?)",
            [(message_id, scope, q.origin, q.departure, q.return_date, q.category, q.price) for q in quotes])
        return message_id

    def expire_outside_search(self, config):
        # Preserve compatible price history when dates change, but never deliver
        # a previously queued digest containing a now-excluded trip.
        for message in self.db.execute("SELECT id,kind,run_id FROM outbox WHERE status='pending' AND kind IN ('deal','trend','check_status')").fetchall():
            items=self.db.execute("SELECT origin,departure,return_date FROM alert_items WHERE outbox_id=?",(message[0],)).fetchall()
            valid=bool(items)
            if message['kind'] == 'check_status':
                run = self.db.execute('SELECT scope,summary FROM runs WHERE id=?', (message['run_id'],)).fetchone()
                settings = json.loads(run['summary']).get('config', {}) if run else {}
                valid = bool(run) and run['scope'] == config.scope(self.get_meta('mode') or 'live') and all(
                    settings.get(k) == config.public_dict()[k] for k in
                    ('departure_start', 'departure_end', 'min_trip_days', 'max_trip_days'))
                valid = valid and settings.get('origins') == list(config.origins)
            for item in items:
                try:
                    duration=(datetime.fromisoformat(item['return_date'])-datetime.fromisoformat(item['departure'])).days
                    valid = valid and item['origin'] in config.origins and config.departure_start <= item['departure'] <= config.departure_end and config.min_trip_days <= duration <= config.max_trip_days
                except (ValueError,TypeError):
                    valid=False
            if not valid:
                self.db.execute("UPDATE outbox SET status='expired' WHERE id=?",(message[0],))

    def close(self):
        self.db.commit()
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.db.rollback()
        self.close()
