# BKK Flight Price Tracker

Automated fare monitoring from **Düsseldorf (DUS), Frankfurt (FRA) and Amsterdam
(AMS) to Bangkok (BKK)**. The tracker searches flexible dates four times per day,
keeps a durable price history and sends Telegram messages only when a fare has
meaningfully changed.

[![Tests](https://github.com/Fabiano225/flight-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/Fabiano225/flight-tracker/actions/workflows/tests.yml)
[![Track flights](https://github.com/Fabiano225/flight-tracker/actions/workflows/track-flights.yml/badge.svg)](https://github.com/Fabiano225/flight-tracker/actions/workflows/track-flights.yml)

> **Status:** personal, trip-specific automation for October/November 2026. The
> upstream Google Flights interface is unofficial and may change without notice.

## What it does

- Searches departures from **15–23 October 2026** (never earlier than 15 October).
- Accepts trips of **14–21 days**; the latest return departure is 13 November.
- Separates **direct flights** (zero stops in both directions) from itineraries
  with a connection.
- Rejects itineraries lasting **21 hours or more in either direction**.
- Tracks one adult, economy fares in EUR and stores observations in SQLite.
- Sends Telegram alerts for meaningful rises, falls, budget crossings and strong
  drops. Unchanged prices stay quiet.
- Runs at 00:17, 06:17, 12:17 and 18:17 UTC through GitHub Actions.

## Telegram alerts, at a glance

The first observation establishes a watch. Later messages include:

| Message | Meaning |
|---|---|
| **PREIS GESUNKEN** / **PREIS GESTIEGEN** | Same airport, dates and flight type changed by at least €25 since the last alert. The message shows `before → now`, euros and percentage. |
| **KAUF PRÜFEN** | Fare is at or below the €650 target and close to the observed low. |
| **IM BUDGET, ABER …** | Fare is within €650 but at least €25 above the observed low. |
| **BEOBACHTEN** | Fare is above the target. |
| **STARKER DEAL** | At least 10% **and** €50 below the previous 30-day low. |
| **GÜNSTIGERE ALTERNATIVE** | A different date pair is materially cheaper; it is not described as a drop for the old dates. |

The tracker never treats a missing search result as a price increase. “Buy” is a
budget signal, not a prediction that prices cannot fall further. Prices are search
observations, not reservations; check baggage, fare rules and availability before
booking.

## Search configuration

| Setting | Value |
|---|---|
| Origins | DUS, FRA, AMS |
| Destination | BKK |
| Departures | 2026-10-15 … 2026-10-23, inclusive |
| Trip length | 14 … 21 calendar days |
| Passenger / cabin / currency | 1 adult / economy / EUR |
| Maximum direction duration | 1,259 minutes (20 h 59 min) |
| Budget | €650, configurable independently for direct and connecting flights |
| Strong-drop rule | ≥10% **and** ≥€50 below the previous 30-day low |
| Notification threshold | €25 since the last alert, or crossing the budget |

Edit `config.json` for a new trip. Changing dates or comparable search settings
creates a separate history scope; it does not rewrite old observations.

## Set up Telegram and GitHub Actions

1. Create a bot with [@BotFather](https://t.me/BotFather) using `/newbot`.
2. In [repository secrets](https://github.com/Fabiano225/flight-tracker/settings/secrets/actions),
   add `TELEGRAM_BOT_TOKEN`.
3. Send `/start` to the bot from the Telegram account that should receive alerts.
4. Run the [Telegram setup workflow](https://github.com/Fabiano225/flight-tracker/actions/workflows/telegram-setup.yml).
   It replies privately with the chat ID.
5. Save that value as `TELEGRAM_CHAT_ID` (the value is never committed).
6. Run [Test Telegram](https://github.com/Fabiano225/flight-tracker/actions/workflows/telegram-test.yml).
7. Trigger [Track flights](https://github.com/Fabiano225/flight-tracker/actions/workflows/track-flights.yml)
   once manually. Scheduled runs then continue automatically.

The setup workflow reads only a recent private `/start` message. It does not print
the bot token, remove a webhook or expose the chat ID in its log.

## How the pipeline works

1. Build the date grid for all airports, trip lengths and two search profiles.
2. Record calendar observations, including unknown/no-offer results.
3. Recheck the stable date watches first, then shortlist additional candidates.
4. Verify actual round trips, dates, currency, stop counts and both flight durations.
5. Compare compatible verified quotes with their 30-day history.
6. Checkpoint SQLite and pending messages before Telegram delivery; save delivery
   receipts afterwards.

The free adapter uses the [`fli`](https://github.com/punitarani/fli) Python client
against an unofficial Google Flights shopping endpoint. It has no API key, paid
fallback, proxy rotation or CAPTCHA handling. Verification is deliberately bounded,
so this project is a practical monitor rather than an exhaustive fare inventory.
See [the architecture notes](docs/ARCHITECTURE.md) for the data model and alert
flow.

## Local development

```bash
python -m venv .venv
# Activate the environment for your shell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m tracker plan
```

Run an offline synthetic demo without network access:

```bash
python -m tracker demo --as-of 2026-09-18
python -m tracker demo --as-of 2026-09-19 --demo-discount 100
```

Useful commands for an already configured local environment:

```bash
python -m tracker report
python -m tracker export --output history.csv
python -m tracker telegram-test
```

Do not run a local live scan while the GitHub Actions state writer is running.

## Workflows and state

| Workflow | Purpose |
|---|---|
| `Track flights` | Scheduled search, verification, state checkpoint and Telegram delivery |
| `Telegram setup` | Finds the chat ID for a recent private `/start` |
| `Test Telegram` | Sends one connectivity test using repository secrets |
| `Tests` | Offline unit/integration tests; no secrets and no live flight search |

The `tracker-state` branch contains SQLite history, latest CSV/JSON output and the
human-readable report. It contains route and fare observations, never the Telegram
token or chat ID. Set the repository variable `TRACKER_ENABLED=false` to pause
scheduled tracking.

## Costs and privacy

The repository is public and uses standard GitHub-hosted Ubuntu runners. GitHub
currently provides standard-runner runtime at no charge for public repositories;
artifact/cache limits and any previously accrued private-repository usage are
separate. See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

The project stores no credentials in source control. Secrets are supplied only as
GitHub Actions secrets. The public repository and `tracker-state` branch do reveal
the configured routes, dates, observed fares and alert text. Treat that as public
data when adapting the project.

## Limitations

- GitHub schedules are best effort and can start late or be skipped.
- Google may rate-limit or change the unofficial endpoint.
- A calendar estimate is never sent as a deal; only verified round trips qualify.
- The shortlist can miss an itinerary outside the bounded verification set.
- Search prices can expire before booking and do not include an assumed baggage
  allowance.

For routine operation and troubleshooting, see [DEPLOYMENT.md](DEPLOYMENT.md).
For contribution guidance, see [CONTRIBUTING.md](CONTRIBUTING.md).
