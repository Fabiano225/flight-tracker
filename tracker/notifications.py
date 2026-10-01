"""One active transport, durable receipts and channel-safe price references."""
import os

from .config import trips_of
from .network import ServiceError
from .store import stamp

ARCHIVE_PREFIX = "Copied price update"
DASHBOARD = "https://fabiano225.github.io/flight-tracker/"
SETTINGS = DASHBOARD + "settings.html"


def message_labels(settings):
    """(fallback airport code, display names) for message titles.

    Price alerts, check statuses and notices name their own destination; a code
    for all other messages exists only while a single trip is searched."""
    trips = trips_of(settings)
    names = {}
    for trip in reversed(trips):  # The first trip's names win for a shared airport.
        names.update(trip.display_names)
    return (trips[0].destination if len(trips) == 1 else None), names


def configured_sender(settings=None):
    channel = os.environ.get("NOTIFICATION_CHANNEL", "auto").strip().lower() or "auto"
    if channel == "auto":
        channel = "discord" if os.environ.get("DISCORD_WEBHOOK_URL", "").strip() else "telegram"
    if channel == "discord":
        from .discord import Discord
        if settings is None:
            return Discord()
        code, names = message_labels(settings)
        return Discord(code=code, names=names)
    if channel == "telegram":
        from .telegram import Telegram
        return Telegram()
    raise ServiceError("NOTIFICATION_CHANNEL must be auto, discord or telegram")


def deliver(store, settings, now, sender):
    """Send pending messages of all trips; `settings` may also be a single Config."""
    trips = trips_of(settings)
    store.expire(now, trips[0].pending_ttl_hours, {trip.scope() for trip in trips})
    store.expire_outside_search(trips)
    store.db.commit()
    destination = getattr(sender, "destination", "telegram")

    def receipt(outbox_id, message_id):
        store.db.execute("INSERT OR REPLACE INTO delivery_receipts VALUES(?,?,?,?)",
                         (outbox_id, destination, message_id, stamp(now)))
        store.db.commit()

    sent = 0
    for row in store.db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY created,rowid").fetchall():
        target = store.db.execute("""SELECT o.* FROM outbox_replies r
            JOIN outbox o ON o.id=r.target_id WHERE r.outbox_id=? AND o.status='sent'""",
            (row["id"],)).fetchone()
        reply_id = None
        if target:
            known = store.db.execute("SELECT message_id FROM delivery_receipts WHERE outbox_id=? AND destination=?",
                                     (target['id'], destination)).fetchone()
            if known:
                reply_id = known[0]
            elif destination == "telegram" and not store.db.execute(
                    "SELECT 1 FROM delivery_receipts WHERE outbox_id=?", (target['id'],)).fetchone():
                # Before multi-channel support all receipts were Telegram IDs.
                reply_id = target['message_id']
            else:
                # Move only the referenced price alert, not the entire history.
                # Mark it as historical, and persist before sending the status.
                archive = (ARCHIVE_PREFIX + " – NOT a new price alert.\n"
                    "Historical message, not rechecked; from " + target['created'] + ".\n\n" + target['text'])
                if destination == 'telegram' and len(archive) > 4096:
                    archive = archive[:4000] + '\n(Shortened; more prices on the dashboard.)'
                reply_id = sender.send(archive)
                receipt(target['id'], reply_id)
                sent += 1
        message_id = (sender.send(row['text'], reply_to_message_id=reply_id)
                      if reply_id else sender.send(row['text']))
        store.db.execute("UPDATE outbox SET status='sent',sent=?,message_id=? WHERE id=?",
                         (stamp(now), message_id, row['id']))
        receipt(row['id'], message_id)
        sent += 1
    return sent
