# DUS / FRA / AMS → Bangkok flight tracker

Free-source flight monitoring with price history, separate nonstop/layover alerts,
and four scheduled GitHub Actions runs per day.

**Deployment evidence:** see [DEPLOYMENT.md](DEPLOYMENT.md). Real EUR date searches,
selected outbound/return itineraries and the GitHub Telegram test have succeeded.
Full scheduled-run acceptance is in progress. Demo prices are synthetic.

## Search rules

| Setting | Value |
|---|---|
| Origins | DUS, FRA, AMS — separate airport searches |
| Destination | BKK, not DMK |
| Outbound departure dates | 2026-10-15 through 2026-11-10, inclusive |
| Trip length | 14–21 calendar days between outbound and return departures |
| Latest return departure | 2026-12-01 |
| Travelers / cabin / currency | 1 adult / economy / EUR |
| Duration limit | **1,259 minutes per direction**, including layovers; 21h excluded |
| Good deal | ≤ €650 round-trip, separately configurable for nonstop and layover |
| Price drop | ≥10% **and** ≥€50 below the previous 30-day observed low |
| Repeat alert | Requires at least another €25 reduction from an already queued/sent alert |
| Alert volume | Up to 6 deals per run, balanced across airports and actual connection types |

“Nonstop” means **zero stops in both directions**. A trip with a connection in
either direction is labeled “layover”; the message includes both stop counts.
These are not interchangeable historical baselines.

Trip length is not the number of nights actually spent in Thailand: overnight
flights and local arrival times can reduce the stay. Returns are allowed beyond
November 10. Same-day and past outbound departures are skipped.

## Telegram einrichten

1. Bei **@BotFather** einen Bot mit `/newbot` erstellen.
2. In [GitHub → Settings → Secrets and variables → Actions](https://github.com/Fabiano225/flight-tracker/settings/secrets/actions)
   auf **New repository secret** klicken.
3. `TELEGRAM_BOT_TOKEN` als Name setzen, den BotFather-Token als Wert speichern.
4. Den eigenen Bot in Telegram öffnen und ihm **`/start`** schicken.
5. [Actions → Telegram setup](https://github.com/Fabiano225/flight-tracker/actions/workflows/telegram-setup.yml)
   öffnen, **Run workflow** wählen. Innerhalb von 30 Minuten nach `/start` ausführen.
6. Der Bot antwortet privat mit deiner Chat-ID. Diese Nummer als zweites Secret
   **`TELEGRAM_CHAT_ID`** speichern.
7. Nach erfolgreichem Live-Datentest **Actions → Track flights → Run workflow** starten.

Der Einrichtungslauf gibt keine Chat-ID und keinen Token im Log aus. Er antwortet
auf die jüngste private `/start`-Nachricht; benutze einen eigenen, neuen Bot.
Ein bestehender Telegram-Webhook wird nicht entfernt. Für einen bereits anderweitig
betriebenen Bot stattdessen dessen bekannte Chat-ID verwenden.

## How searches work

The adapter uses [`flights` / Fli](https://github.com/punitarani/fli), an **unofficial**
Google Flights client. No flight API key, subscription, proxy service, or paid
fallback is configured. GitHub Actions minutes/storage remain subject to your
account's own limits. Free data access does not imply a service-level guarantee.

1. Cover all **648 route/date pairs** in two profiles: nonstop and any number of
   stops. This produces **1,296 individual date/profile searches**, grouped into
   48 checkpoint batches, before retries while the full window is future.
   Google's streaming calendar endpoint returned RPC error 13 in live tests.
   The adapter therefore uses the public shopping-prefetch RPC observed on the
   search page, not that broken endpoint. There is no paid fallback.
2. Keep every returned calendar price, including unknown/missing slots, in SQLite.
   The any-stops calendar is **not** a layover-only calendar and is never labeled as one.
3. Select up to **18 date/profile candidates** per run, prioritizing observed
   calendar drops and inexpensive unalerted dates with route/profile diversity.
4. Search actual outbound/return combinations for those candidates, expanding
   up to 3 outbound options each. Reject wrong routes, wrong dates, unknown/wrong
   currencies, invalid prices, and either direction of 21h or longer.
5. Categorize accepted itineraries by actual stop counts. Store their price and
   details separately from calendar estimates. Compare only compatible verified
   itinerary histories for price-drop alerts.
6. Queue deal and health messages, checkpoint state, deliver messages, then persist
   delivery receipts.

This is **full date-grid monitoring with bounded itinerary verification**, not an
exhaustive enumeration of every airline, fare, or return combination. It can miss
deals outside the shortlist or the expanded outbound candidates. Calendar quotes
alone never trigger a deal message. Each verified quote is still a search fare,
not a reservation or guaranteed bookable price.

The initial search fare is the minimum over still-unselected return choices.
Only selected, checked round trips are used for deal alerts. Requests are serial,
spaced by at least 0.7 seconds, bounded to 1,600 HTTP attempts and 35 minutes per
scan. Source access denial/rate limits stop the affected search; no CAPTCHA or
proxy handling is implemented. A full run can take tens of minutes. This approach
uses more requests than a working calendar endpoint (155,520 date searches per
30 days at the initial window size); it has no API subscription fee, but may be
throttled or broken by upstream changes.

The API filter requests the duration cap, and the final itinerary check enforces it
again on both directions. Explicitly flagged self-transfer trips are rejected by
default; unknown self-transfer metadata does not prove a protected connection.
No baggage allowance is assumed. Check baggage and ticket conditions before purchase.

### Price comparison

History keys include airport, exact travel dates, actual nonstop/layover category,
provider, currency, traveler count, cabin, duration cap and baggage filters.
Changing comparable search settings starts a separate history namespace.

The prior 30-day minimum excludes the current run. The initial observation has
no price-drop baseline but can qualify as a good deal. Comparisons describe the
lowest observed fare for that date/category, not the same airline or fare product.
There is no invented or backfilled history before installation.

## GitHub Actions

The workflow is scheduled at **00:17, 06:17, 12:17 and 18:17 UTC**. In Berlin this is
02:17/08:17/14:17/20:17 during summer time, and 01:17/07:17/13:17/19:17 during winter time.

GitHub schedules are best effort and may run late or be skipped under load. The
workflow must be on the repository's default branch; public repositories can have
schedules disabled after prolonged inactivity. See [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

- `Track flights`: scheduled or manually dispatched, one persistent-state writer at a time.
- `Telegram setup`: manually dispatched, sends the chat ID privately.
- `Test Telegram`: manually dispatched, tests both repository secrets with one message.
- `Tests`: offline tests on pushes and pull requests; no secrets or live searches.
- Actions are pinned to resolved commit hashes. Dependabot proposes updates.
- Set repository **Actions variable** `TRACKER_ENABLED` to `false` to stop tracking runs.
- After the departure window ends, source searches stop automatically. Disable the
  workflow afterward to avoid idle runner use; history remains available.

The tracker job needs `contents: write` for its dedicated state branch. Organization
policies or branch rules may deny the push even with this workflow permission.
Allow the bot to update `tracker-state`; never weaken protections for the code branch.

## Durable state and recovery

`tracker-state` holds these files, separate from `main`:

- `history.sqlite3`: all calendar observations, verified quotes, run summaries,
  pending messages and delivery receipts.
- `latest.json` and `latest.csv`: latest scan and verified fare details.
- `report.md`: readable run report; also shown in the Actions job summary.

History uses a Git branch, **not an expiring Actions cache**. Failed remote reads
do not silently initialize a fresh database. Pushes never force-overwrite concurrent
changes. Database integrity is checked on restore/save. Synthetic demo data is
rejected by the live state publisher.

Before sending Telegram messages, the workflow must successfully save pending
alerts and history. A second save records receipts, including partial delivery.
Failed sends remain pending, with a 12-hour expiry to avoid stale deals.

Delivery is **at least once**, not exactly once: a timeout after Telegram accepted
a message, or a crash before its receipt is saved, can cause a duplicate. Ordinary
repeated runs suppress unchanged deal alerts. Telegram errors never mark a message sent.

Every run also uploads a seven-day recovery artifact. If a state push fails, retain
that artifact before it expires. Restore the complete SQLite file only with runs
disabled and after preserving the existing state branch. Do not delete the database
to “fix” an error: that discards price history and alert deduplication.

This trip-specific tracker keeps history indefinitely. If reused for long-running
monitoring, plan database retention and a database service rather than allowing
binary Git history to grow indefinitely. In a public repository the tracked routes,
fare history and alert texts are public; tokens and chat IDs are not stored in state.

## Local commands

Python 3.12 and Git are required. Run from the project directory:

```bash
python -m venv .venv
# Activate .venv using the command appropriate for your shell.
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m tracker plan
```

Offline end-to-end demo, using separate synthetic state:

```bash
python -m tracker demo --as-of 2026-09-18
python -m tracker demo --as-of 2026-09-19 --demo-discount 100
```

Live local commands (do not run concurrently with the GitHub writer):

```bash
python -m tracker scan
python -m tracker report
python -m tracker export --output history.csv
# Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the process environment first.
python -m tracker telegram-test
python -m tracker notify
```

Environment variables are read directly; `.env` files are not automatically loaded.
Never commit secrets. `.gitignore` excludes state, virtual environments and `.env` files.
Local commands do not automatically synchronize the GitHub state branch; the workflow
uses `scripts/state_git.py restore` and `save` for that purpose.

## Troubleshooting

- **No chat-ID message:** send a fresh `/start` directly to your bot and rerun Telegram setup.
- **Telegram 401/403:** check the token, chat ID, bot block status, and whether you started the bot.
- **Google RPC error / consent page / rate limit:** this is a source failure, not “no flights.”
  No proxy rotation or CAPTCHA bypass is implemented. The run stops after repeated
  failures, records diagnostics and sends a health alert at most once per day when
  Telegram is configured. A later successful run sends a recovery message.
- **No nonstop results:** the source may have no nonstop itinerary for those dates;
  an absent calendar price is recorded as unknown, not as a €0 fare.
- **Partial run:** completed calendar chunks are retained. Request and time budgets
  bound the scan; the default 35-minute source budget leaves room for state persistence.
- **Silent schedule:** inspect the Actions page and GitHub notifications. A workflow
  that never starts has no opportunity to send its own Telegram failure alert.

Sources: [Fli project](https://github.com/punitarani/fli),
[Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage),
[Telegram getUpdates](https://core.telegram.org/bots/api#getupdates).
