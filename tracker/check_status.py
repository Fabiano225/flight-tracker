"""Short, honest check receipts, replying to an actual delivered price alert."""
from .provider import Quote
from .store import stamp
from .trends import load_watches, latest_alert


def queue_check_status(store, config, scope, run_id, verified, now, summary, demo=False):
    if summary['queued_deals'] or summary['status'] == 'expired':
        return False
    if store.db.execute("SELECT 1 FROM outbox WHERE run_id=? AND kind='check_status'", (run_id,)).fetchone():
        return False
    watches = load_watches(store, config, scope, now)
    compared, missing, changes, targets = [], [], [], []
    for value in watches.values():
        q = Quote(**value)
        label = q.origin + (' Direkt' if q.category == 'nonstop' else ' Umstieg')
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
            changes.append(f"{label}: {current.price / 100:.2f} EUR ({delta / 100:+.2f} EUR seit Preisalarm)")
    incomplete = summary['status'] != 'ok' or bool(missing) or not compared
    lines = ['DEMO - synthetischer Suchstatus' if demo else 'BKK Suchstatus', stamp(now)]
    if incomplete:
        lines.append('Prüfung unvollständig – unveränderte Preise sind nicht für alle Angebote bestätigt.')
        lines.append(f"Suchblöcke: {summary['calendar_queries_ok']}/{summary['calendar_queries_planned']}; "
                     f"bestätigte Preisvergleiche: {len(compared)}/{len(watches)}.")
    elif changes:
        lines.append('Nur kleine Preisänderungen unterhalb der Alarmschwelle. Kein neuer Preisalarm.')
    else:
        lines.append('Keine Preisänderung bei den beobachteten Angeboten seit den letzten Preisalarmen.')
    lines.extend(changes[:6])
    if targets:
        target = max(targets, key=lambda t: t['created'])
        lines.append('Letzter zugehöriger Preisalarm: ' + target['created'][:16].replace('T', ' ') + ' UTC.')
        lines.append('Tippe auf die zitierte Nachricht, um zum Preisalarm zu springen (sofern noch vorhanden).')
    else:
        target = None
        lines.append('Noch kein zugestellter Preisalarm für diese Vergleichsdaten verfügbar.')
    # A newer check replaces an undelivered older receipt; fare alerts are untouched.
    store.db.execute("UPDATE outbox SET status='expired' WHERE kind='check_status' AND status='pending'")
    status_id = store.enqueue(run_id, 'check_status', now, '\n'.join(lines),
                              [Quote(**v) for v in watches.values()], scope)
    if target:
        store.db.execute('INSERT INTO outbox_replies VALUES(?,?)', (status_id, target['id']))
    return True
