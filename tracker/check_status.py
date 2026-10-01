"""Short, honest check receipts, replying to an actual delivered price alert."""
from .provider import Quote
from .store import stamp
from .trends import load_watches, latest_alert

TELEGRAM_REPLY_HINT = 'Tap the quoted message to jump to the price alert (if it still exists).'


def queue_check_status(store, config, scope, run_id, verified, now, summary, demo=False):
    if summary['queued_deals'] or summary['status'] == 'expired':
        return False
    if store.db.execute("SELECT 1 FROM outbox WHERE run_id=? AND kind='check_status'", (run_id,)).fetchone():
        return False
    watches = load_watches(store, config, scope, now)
    compared, missing, changes, targets = [], [], [], []
    for value in watches.values():
        q = Quote(**value)
        label = q.origin + (' non-stop' if q.category == 'nonstop' else ' with stops')
        current = verified.get((q.origin, q.departure, q.return_date, q.category))
        prior = latest_alert(store, scope, q, config)
        if current is None or prior is None or (prior['departure'], prior['return_date']) != (q.departure, q.return_date):
            missing.append(label)
            continue
        target = store.db.execute("SELECT id,created,status,message_id FROM outbox WHERE id=?",
                                  (prior['outbox_id'],)).fetchone()
        if target['status'] != 'sent' or not target['message_id']:
            missing.append(label)
            continue
        compared.append(current)
        targets.append(target)
        delta = current.price - prior['price']
        if delta:
            changes.append(f"{label}: {current.price / 100:.2f} EUR ({delta / 100:+.2f} EUR since the alert)")
    incomplete = summary['status'] != 'ok' or bool(missing) or not compared
    lines = ['DEMO - synthetic check status' if demo else f'{config.destination} check status', stamp(now)]
    if incomplete:
        lines.append('Check incomplete – unchanged prices are not confirmed for every offer.')
        lines.append(f"Search blocks: {summary['calendar_queries_ok']}/{summary['calendar_queries_planned']}; "
                     f"confirmed price comparisons: {len(compared)}/{len(watches)}.")
    elif changes:
        lines.append('Only small price changes below the alert threshold. No new price alert.')
    else:
        lines.append('No price change for the watched offers since the last price alerts.')
    lines.extend(changes[:6])
    if targets:
        target = max(targets, key=lambda t: t['created'])
        lines.append('Latest related price alert: ' + target['created'][:16].replace('T', ' ') + ' UTC.')
        lines.append(TELEGRAM_REPLY_HINT)
    else:
        target = None
        lines.append('No delivered price alert for these comparison dates yet.')
    # A newer check replaces an undelivered older receipt of this trip; fare alerts are untouched.
    store.expire_check_status(scope)
    status_id = store.enqueue(run_id, 'check_status', now, '\n'.join(lines),
                              [Quote(**v) for v in watches.values()], scope)
    if target:
        store.db.execute('INSERT INTO outbox_replies VALUES(?,?)', (status_id, target['id']))
    return True
