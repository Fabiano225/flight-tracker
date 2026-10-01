"""Discord webhook delivery. Credentials and channel metadata stay in memory."""
import hashlib
import json
import os
import re

from .network import JsonHttp, ServiceError
from .places import city

DASHBOARD = "https://fabiano225.github.io/flight-tracker/"
GREEN, RED, AMBER, BLUE = 0x22C55E, 0xEF4444, 0xF59E0B, 0x5865F2


def readable(text):
    for old, new in (("GUENSTIGERE", "GÜNSTIGERE"), ("PRUEFEN", "PRÜFEN"),
                     ("Gegenueber", "Gegenüber"), ("gegenueber", "gegenüber"),
                     ("Rueckgang", "Rückgang"), ("zurueck", "zurück"),
                     ("Gepaeck", "Gepäck"), ("pruefen", "prüfen"),
                     ("fuer", "für"), ("ueber", "über"), ("Hin/Rueck", "Hin/Rück"),
                     (" -> ", " → ")):
        text = text.replace(old, new)
    return text


def payload_for(text, reference_url=None, code=None, names=None):
    if not text or len(text) > 4700:
        raise ValueError("Discord notification text length is out of bounds")
    archived = text.startswith("Übernommener Preisstand")
    # A queued message names its own destination; older ones may predate a route change.
    kind = re.match(r"([A-Z]{3}) (Preisalarm|Suchstatus)\n", text)
    code = kind.group(1) if kind else code
    place = city(code, names) + " · " if code else ""
    text = readable(text)
    text = text.replace('Flight tracker needs attention.', 'Die Flugsuche braucht Aufmerksamkeit.')
    text = text.replace('No missing or unverified prices are sent as deals. Check the GitHub Actions run.',
                        'Fehlende oder ungeprüfte Preise werden nicht als Angebote gemeldet. Bitte den Suchlauf prüfen.')
    text = text.replace('Flight tracker recovered: calendar searches and itinerary checks succeeded.',
                        'Die Flugsuche ist wieder erreichbar. Datumsabfragen und Flugprüfungen waren erfolgreich.')
    text = text.replace('Tippe auf die zitierte Nachricht, um zum Preisalarm zu springen (sofern noch vorhanden).', '')
    if reference_url:
        text += '\n\n[Zum zugehörigen Preisstand](' + reference_url + ')'
    elif 'Letzter zugehöriger Preisalarm:' in text:
        text += '\nDer Nachrichtenverweis ist derzeit nicht verfügbar. Preise im Dashboard prüfen.'
    color = BLUE
    title = "✈️ " + place + "Flight Tracker"
    if 'Prüfung unvollständig' in text:
        title, color = '⚠️ Prüfung unvollständig', AMBER
    elif text.startswith('Die Flugsuche braucht Aufmerksamkeit'):
        title, color = '⚠️ Flugsuche braucht Aufmerksamkeit', AMBER
    elif 'Keine Preisänderung' in text:
        title, color = '✅ Keine Preisänderung', GREEN
    elif 'Nur kleine Preisänderungen' in text:
        title = '↔️ Kleine Preisänderungen'
    elif text.startswith('Discord verbunden'):
        title, color = '✅ Discord verbunden', GREEN
    elif text.startswith('Die Flugsuche ist wieder erreichbar'):
        title, color = '✅ Flugsuche wieder erreichbar', GREEN

    embeds = []
    # The outbox text is also the durable, human-readable audit record. Split
    # only the known price-block format; unknown/legacy messages remain intact.
    if kind and kind.group(2) == 'Preisalarm' and not archived:
        lines = text.split('\n', 2)
        sections = lines[2].split('\n\n')
        embeds.append({'title': '✈️ ' + place + 'Preisupdate', 'description': 'Neue Beobachtung · ' + lines[1],
                       'color': BLUE})
        for section in sections:
            parts = section.splitlines()
            if len(parts) > 1 and ' | ' in parts[0]:
                heading, *body = parts
                card = {'title': heading, 'color': GREEN if heading.startswith('PREIS GESUNKEN') else
                        RED if heading.startswith('PREIS GESTIEGEN') else BLUE}
                if body[-1].startswith('https://www.google.com/travel/flights'):
                    card['url'] = body.pop()
                    body.append('[Flugpreis prüfen](' + card['url'] + ')')
                body[0] = '**' + body[0] + '**'
                for i, line in enumerate(body):
                    if line.startswith(('KAUF PRÜFEN:', 'BEOBACHTEN:', 'IM BUDGET', 'STARKER DEAL:')):
                        body[i] = '**' + line + '**'
                card['description'] = '\n'.join(body)
                embeds.append(card)
            elif section.strip():
                embeds[0]['footer'] = {'text': section.strip()}
    if not embeds:
        if archived:
            title, color = '📌 Übernommener Preisstand · historisch', BLUE
        embeds = [{'title': title, 'description': text[i:i+3900], 'color': color}
                  for i in range(0, len(text), 3900)]
    total = sum(len(e.get('title', '')) + len(e.get('description', '')) +
                len(e.get('footer', {}).get('text', '')) for e in embeds)
    if (len(embeds) > 10 or total > 6000 or any(len(e.get('description', '')) > 4096 or
            len(e.get('title', '')) > 256 or len(e.get('footer', {}).get('text', '')) > 2048 for e in embeds)):
        raise ValueError('Discord embed limits exceeded')
    return {'username': (code + ' ' if code else '') + 'Flight Tracker', 'allowed_mentions': {'parse': []},
            'content': '[Dashboard öffnen](<' + DASHBOARD + '>) · '
                       '[Suchläufe](<https://github.com/Fabiano225/flight-tracker/actions/workflows/track-flights.yml>)',
            'embeds': embeds}


class Discord:
    def __init__(self, webhook_url=None, http=None, code=None, names=None):
        value = (webhook_url or os.environ.get('DISCORD_WEBHOOK_URL', '')).strip()
        # Pin host/path; refuse query parameters, redirects and credentials in
        # authority. A webhook token must never be sent to an arbitrary host.
        match = re.fullmatch(r'https://discord\.com/api/(?:v\d+/)?webhooks/(\d+)/([A-Za-z0-9_-]+)', value)
        if not match:
            raise ServiceError('Set DISCORD_WEBHOOK_URL to a Discord text-channel webhook URL in GitHub Actions secrets')
        self.url = value
        self.destination = 'discord:' + hashlib.sha256(value.encode()).hexdigest()
        self.http = http or JsonHttp(attempts=3, timeout=30, budget=40, interval=1)
        self.metadata = None
        self.code, self.names = code, names

    def reference_url(self, message_id):
        if not str(message_id).isdigit():
            return None
        if self.metadata is None:
            try:
                self.metadata = self.http.get_json(self.url)
            except ServiceError:
                # An optional link must not block an otherwise deliverable status.
                return None
        guild, channel = self.metadata.get('guild_id'), self.metadata.get('channel_id')
        if not all(str(v).isdigit() for v in (guild, channel)):
            return None
        return f'https://discord.com/channels/{guild}/{channel}/{message_id}'

    def send(self, text, reply_to_message_id=None):
        link = self.reference_url(reply_to_message_id) if reply_to_message_id else None
        payload = payload_for(text, link, self.code, self.names)
        data = self.http.get_json(self.url + '?wait=true',
            {'Content-Type': 'application/json', 'User-Agent': 'BKKFlightTracker/1.0'},
            json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        if not isinstance(data.get('id'), str) or not data['id'].isdigit():
            raise ServiceError('Discord did not acknowledge delivery')
        return data['id']
