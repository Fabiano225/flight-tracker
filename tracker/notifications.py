"""One main channel, optional channels per trip, durable receipts and channel-safe price references."""
import hashlib
import json
import os

from .config import TRIP_ID, searches_of
from .network import ServiceError
from .project import dashboard_url
from .store import stamp

ARCHIVE_PREFIX = "Copied price update"
DASHBOARD = dashboard_url()
SETTINGS = DASHBOARD + "settings.html"


def message_labels(settings):
    """(fallback airport code, display names) for message titles.

    Price alerts, check statuses and notices name their own destination; a code
    for all other messages exists only while a single trip is searched."""
    searches = searches_of(settings)
    names = {}
    for search in reversed(searches):  # The first trip's names win for a shared airport.
        names.update(search.display_names)
    return (searches[0].destination if len(searches) == 1 else None), names


# Secrets with a channel per trip: {"trip id": Discord webhook URL or Telegram chat ID}.
TRIP_CHANNELS = ("DISCORD_WEBHOOKS", "TELEGRAM_CHAT_IDS")


def notifications_configured():
    """Whether any message channel is set up. Without one, messages wait undelivered and
    expire; an explicitly chosen channel (NOTIFICATION_CHANNEL) must be set up."""
    channel = os.environ.get("NOTIFICATION_CHANNEL", "auto").strip().lower() or "auto"
    return channel != "auto" or any(os.environ.get(name, "").strip() for name in
                                     ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", *TRIP_CHANNELS))


def trip_channels(name):
    """The trip id -> target map of a TRIP_CHANNELS secret; empty while it is not set.
    Errors never repeat a value: the targets are credentials."""
    text = os.environ.get(name, "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        raise ServiceError(f'{name} must be JSON like {{"trip-id": "..."}}') from None
    if not isinstance(value, dict):
        raise ServiceError(f'{name} must be JSON like {{"trip-id": "..."}}')
    targets = {}
    for trip, target in value.items():
        if not TRIP_ID.fullmatch(trip) or type(target) not in (str, int) or not str(target).strip():
            raise ServiceError(f"{name}: every key must be a trip id (as in the website's ?trip= links) "
                               "and every value a non-empty text")
        targets[trip] = str(target).strip()
    return targets


class Router:
    """Which channel gets a message: the trip's own one (DISCORD_WEBHOOKS, else
    TELEGRAM_CHAT_IDS), otherwise the main channel. Health messages concern the whole
    tracker and always go to the main channel. Channels are made on first use, so one
    that is not set up or broken only holds back its own messages."""
    def __init__(self, main=None, trips=None):
        # main and every trip's entry: a function making the sender, or None (not set up).
        self.main, self.trips, self.made = main, trips or {}, {}

    @classmethod
    def single(cls, sender):
        return cls(lambda: sender)

    def key(self, trip):
        """The trip id if the trip has its own channel, else None (the main channel)."""
        return trip if trip in self.trips else None

    def channel(self, key):
        """The sender for a key, or None while that channel is not set up."""
        if key not in self.made:
            make = self.trips[key] if key else self.main
            self.made[key] = make() if make else None
        return self.made[key]


def configured_router(settings=None):
    """The main channel (see configured_sender) and every trip's own one."""
    from .discord import Discord
    from .telegram import Telegram
    code, names = message_labels(settings) if settings is not None else (None, None)

    def discord(trip, url):
        def make():
            try:
                return Discord(url, code=code, names=names)
            except ServiceError:
                raise ServiceError(f"DISCORD_WEBHOOKS: the entry for trip {trip} is not a Discord "
                                   "text-channel webhook URL") from None
        return make

    def telegram(chat):
        def make():
            if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
                raise ServiceError("TELEGRAM_CHAT_IDS needs the secret TELEGRAM_BOT_TOKEN as well")
            bot = Telegram(chat_id=chat)
            # Its own receipts: a reply never points at a message in another chat.
            bot.destination = "telegram:" + hashlib.sha256(chat.encode()).hexdigest()
            return bot
        return make

    trips = {trip: telegram(chat) for trip, chat in trip_channels("TELEGRAM_CHAT_IDS").items()}
    trips.update({trip: discord(trip, url) for trip, url in trip_channels("DISCORD_WEBHOOKS").items()})
    channel = os.environ.get("NOTIFICATION_CHANNEL", "auto").strip().lower() or "auto"
    main_set_up = channel != "auto" or any(os.environ.get(name, "").strip() for name in
                                          ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))
    return Router((lambda: configured_sender(settings)) if main_set_up else None, trips)


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
    """Send pending messages of all trips; `settings` may also be a single Config.
    `sender` is one channel for every message, or a Router with a channel per trip.

    A channel that fails keeps its remaining messages pending for the next run; the
    other channels still get theirs, and the failure is raised at the end."""
    trips = searches_of(settings)
    store.expire(now, trips[0].pending_ttl_hours, {trip.scope() for trip in trips})
    store.expire_outside_search(trips)
    store.db.commit()
    router = sender if isinstance(sender, Router) else Router.single(sender)
    mode = store.get_meta("mode") or "live"
    trip_of = {trip.scope(mode): trip.id for trip in trips}
    failed = {}
    sent = 0
    for row in store.db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY created,rowid").fetchall():
        # A message belongs to the trip whose search queued it.
        run = store.db.execute("SELECT scope FROM runs WHERE id=?", (row["run_id"],)).fetchone()
        key = router.key(None if row["kind"] == "health" or run is None else trip_of.get(run["scope"]))
        if key in failed:
            continue
        try:
            channel = router.channel(key)
            if channel is None:
                continue  # No channel set up for it: the message waits and expires.
            sent += send(store, now, channel, row)
        except ServiceError as exc:
            failed[key] = exc
    if failed:
        raise ServiceError(f"Delivered {sent} messages; failed: " + "; ".join(
            f"{'trip ' + key if key else 'main channel'}: {exc}" for key, exc in failed.items()))
    return sent


def send(store, now, sender, row):
    """Deliver one pending message, after the price alert it refers to if this channel
    has not got that one; returns the number of messages sent."""
    destination = getattr(sender, "destination", "telegram")

    def receipt(outbox_id, message_id):
        store.db.execute("INSERT OR REPLACE INTO delivery_receipts VALUES(?,?,?,?)",
                         (outbox_id, destination, message_id, stamp(now)))
        store.db.commit()

    sent = 0
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
            if destination.startswith('telegram') and len(archive) > 4096:
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
