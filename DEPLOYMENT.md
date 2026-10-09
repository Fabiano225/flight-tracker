# Operations guide

This document describes the live deployment of the BKK flight tracker. It is
written for the repository maintainer; the user-facing overview is in
[README.md](README.md).

## Current production configuration

- **Repository:** `Fabiano225/flight-tracker`
- **Schedule:** **Search schedule** starts **Track flights** about every 6 hours: at the
  times of an external timer (see [Exact search times](#exact-search-times-with-an-external-timer)),
  with an hourly check as the fallback. Track flights has no schedule of its own.
- **Search window:** departures 20–23 October 2026; 14–21-day trips
- **Routes:** DUS/FRA/AMS → BKK, one adult, economy, EUR (as in `config.json`)
- **Duration guard:** below 21 hours in each direction
- **State branch:** `tracker-state`
- **Search settings:** `config.json` on `main`, or the `search-config` branch once the
  website form has been used (see below)
- **Runtime switch:** repository variable `TRACKER_ENABLED`

The current code records stable date watches and sends change alerts instead of a
full price digest every six hours. Existing quote history is retained when the
notification policy changes.

## Exact search times with an external timer

GitHub starts scheduled workflows only on a best-effort basis: under load they run
hours late or not at all. The **Search schedule** workflow therefore runs every hour
as a fallback and starts **Track flights** when the last saved search is at least
5 h 45 min old. For searches at fixed times, let an external timer start that workflow
four times a day; it then starts a search when the last one is at least 5 hours old.
Both paths share the same check, so a search is never started twice.

The steps below use [cron-job.org](https://cron-job.org) (free). Any service or computer
that can send an HTTPS request on a schedule works the same way.

### 1. Create a token that can only start workflows

1. On GitHub: your profile picture → **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**.
2. Name it e.g. `flight-tracker timer`, choose an expiration and note the date.
3. **Repository access:** *Only select repositories* → `flight-tracker`.
4. **Permissions → Repository permissions → Actions:** *Read and write*. Leave
   everything else at *No access* (*Metadata: read-only* is added automatically).
5. **Generate token** and copy it. It is shown only once.

The token can start, cancel and re-run workflows of this repository and nothing else.
Keep it only in the timer service; never put it in the repository, an issue or a
message.

### 2. Create the timer on cron-job.org

1. Sign up and choose **Create cronjob**.
2. **URL:**
   `https://api.github.com/repos/<user>/<repository>/actions/workflows/search-schedule.yml/dispatches`
   (for this repository: `Fabiano225/flight-tracker`).
3. **Execution schedule:** custom, every day at **00:05, 06:05, 12:05 and 18:05** (any
   four times six hours apart; avoid full hours, when many timers fire).
4. **Advanced:**
   - **Request method:** `POST`
   - **Headers:**
     - `Authorization: Bearer <your token>`
     - `Accept: application/vnd.github+json`
     - `X-GitHub-Api-Version: 2022-11-28`
     - `Content-Type: application/json`
   - **Request body:** `{"ref":"main"}`
5. Turn on the notification for failed executions, save, and use **Test run**: GitHub
   answers `204 No Content`, and a **Search schedule** run appears under Actions a few
   seconds later.

The same request from a terminal, e.g. for a test:

```bash
curl -X POST -H "Authorization: Bearer <your token>" -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/Fabiano225/flight-tracker/actions/workflows/search-schedule.yml/dispatches \
  -d '{"ref":"main"}'
```

### When it fails

- `401 Unauthorized`: the token expired or was deleted. Create a new one and replace it
  in the timer.
- `403` or `404`: the token lacks *Actions: Read and write* or access to this repository,
  or the URL has a typo.
- `422`: the workflow is disabled. GitHub disables scheduled workflows in public
  repositories after 60 days without activity; enable it again under **Actions →
  Search schedule**.

Without the external timer the hourly fallback alone still searches about every 6–7
hours.

## First-time deployment checklist

- [ ] `DISCORD_WEBHOOK_URL` exists as a repository Actions secret for a server text channel.
- [ ] The **Test Discord** workflow completes successfully and its message arrives.
- [ ] `NOTIFICATION_CHANNEL` is absent / `auto` (or explicitly `discord`).
- [ ] For Telegram instead: set `NOTIFICATION_CHANNEL=telegram`, configure
      `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, send `/start` and run **Test Telegram**.
- [ ] The **Tests** workflow is green on the default branch.
- [ ] `TRACKER_ENABLED` is absent or set to `true`.
- [ ] The **Track flights** workflow has write permission for the `tracker-state`
      branch (`contents: write` in the workflow).

## Normal run sequence

The tracking job intentionally follows this order:

1. Checkout code and install hash-pinned dependencies (`requirements.txt`).
2. Run offline tests.
3. Restore the complete state database from `tracker-state`.
4. Search calendars and verify a bounded set of round trips.
5. Save history and pending alerts **before** sending notifications.
6. Deliver pending messages and save delivery receipts in a second state commit.
7. Upload a seven-day recovery artifact.

If the source or notification service fails, the state remains inspectable and pending messages
are not marked as sent. A later run can retry delivery.

## Reading a run

Open the [Actions page](https://github.com/Fabiano225/flight-tracker/actions) and
inspect these steps in order:

- **Run offline tests:** code and dependency regressions.
- **Restore durable history:** state branch availability and SQLite validation.
- **Search calendars and verify shortlisted itineraries:** source health, request
  budget, accepted quotes and alert candidates.
- **Checkpoint history and pending alerts:** whether the pre-delivery commit was
  written safely.
- **Deliver pending notifications:** Discord/Telegram acknowledgement and message count.
- **Recovery snapshot:** downloadable state files for incident recovery.

The job summary and `state/report.md` contain counts without exposing credentials.
An unknown fare is recorded as unknown; it is never converted to zero.

## Common situations

### No Discord message

Confirm the secret is named exactly `DISCORD_WEBHOOK_URL` and that
`NOTIFICATION_CHANNEL` is absent, `auto` or `discord`. Run **Test Discord**.
If the test fails with HTTP 401/403/404, recreate the webhook in the desired server
text channel and replace the secret. No webhook URL or response body is logged.
Rate limits are retried with the server's requested delay; long delays defer to the
next run. A valid webhook with a failed send never falls back to Telegram.

The next scan delivers either price updates or a concise check receipt. A receipt
links to its associated price message; when changing channels, that historical
reference is copied once and explicitly labelled with its original observation time.
If Discord's optional channel-metadata lookup fails, the status still arrives with
an explanation that its message link is unavailable. A deleted reference can leave
a dead link, but does not block delivery. Notification scheduling is unchanged.

If the message is visible but there is no phone notification, check Discord's
server/channel mute and notification settings. There are no mass mentions.

### No Telegram message (when Telegram is selected)

Check that both secret names are exact, the bot is not blocked, and the chat ID is
the recipient's private chat ID. Run **Test Telegram**. A successful search with no
price change now sends a short check receipt replying to the last applicable price
alert. Incomplete checks say so explicitly. If the trip window has ended, the
tracker stops searching and does not send these receipts.

### A price appears to rise

The tracker reports a rise only for the same airport, exact dates and actual flight
type. Missing results do not produce rises. The alert shows the previous measured
price and timestamp so the comparison can be audited.

### Google source errors or a partial run

Treat RPC errors, consent pages and rate limits as source-health events, not as “no
flights”. Completed batches remain in SQLite. Wait for a later scheduled run rather
than deleting state or starting several concurrent scans.

An isolated Google RPC 13 (internal source error) gets the normal short retries,
then one deferred recheck after the other dates in that batch and a 30-second
pause. Only failed dates are repeated, at most two per batch; successful dates
are retained in memory. Three such date failures stop that batch. The existing
run-wide request/time limits still apply. Access/rate denials, parser errors and
budget exhaustion do not qualify for this deferred pass. An unresolved date
still leaves the batch incomplete and the workflow red; no error is treated as
an empty flight result. The run summary counts dates recovered by this recheck.

The final **Surface partial scans or failed deliveries** step deliberately exits
with code 1 when the scan or delivery was incomplete. Its generic message is not
the underlying cause: inspect the scan summary and the earlier scan/delivery
step. A red scan can still contain valid quotes and successful notification delivery.

### State restore or push failure

Do not delete `history.sqlite3`. Keep the recovery artifact, pause the workflow with
`TRACKER_ENABLED=false`, inspect the state branch and resolve the Git conflict before
resuming. The state writer rejects concurrent updates rather than overwriting them.

Every 30 saves, `scripts/state_git.py save` replaces the `tracker-state` branch with a
single commit (counted by the `State-Commits:` line in each commit message). The push
uses `--force-with-lease` on the restored commit, so it fails rather than overwriting
a concurrent save; if the force push is refused (for example by a branch rule), the
save continues as a normal fast-forward. Earlier states are in the recovery
artifacts. Each scan also deletes observations older than the comparison period (at
least 33 days; verified fares after 120 days) and vacuums the database; a database
over 95 MB is refused before it reaches GitHub's 100 MB file limit.

### Pause tracking

Set the repository Actions variable `TRACKER_ENABLED` to `false`. The workflow file
can remain in place; no scheduled scan will run until the variable is removed or
changed.

## Maintenance

### GitHub Pages dashboard

Pages uses **GitHub Actions** as its publishing source. `Publish dashboard` runs
after `Track flights` completes (including failed scans), on changes to the website,
the tracker code or its airport and airline data, or on manual dispatch. GitHub doesn't
start it after a `Track flights` run that another workflow (search schedule, settings form)
started with its workflow token, so such a run starts it itself as
its last step (this needs `actions: write`). It reads the latest trusted `main` and
`tracker-state` branches, not workflow artifacts or pull-request code.

`scripts/build_site.py` exports only configuration, scan counts and verified fare
history. It fills the route texts in `index.html` (destination, country, origins,
dates, cabin, thresholds) from the primary trip; `data.json` holds every trip with its
own texts, offers and histories, and the page switches between them in the browser
(`?trip=<id>`). The deployment artifact
contains static assets, `data.json`, `search-config.json` (the public settings for the
form) and `airports.json` only: no SQLite database, `.git`, notification messages,
message IDs, channel/chat IDs or tokens. The UI does not call a private API, store
cookies or start additional flight searches. Its refresh button reloads the published
snapshot; it does not trigger a new scan.

If publication fails, inspect the `Publish dashboard` workflow. Existing data stays
online and becomes visibly stale after 12 hours without a fresh scan. A failed scan
with no verified quotes can retain the last available offers, explicitly labelled
with their original timestamp. Turning off tracking does not remove the dashboard.

To preview locally, use Python 3.12 with an existing **live** state database and an
empty output directory:

```bash
python scripts/build_site.py --state state/history.sqlite3 --output _site
python -m http.server 8765 --directory _site --bind 127.0.0.1
```

Synthetic demo state is deliberately rejected by the public exporter. Website
logic tests run with `node --test tests/site_model.test.mjs tests/search_model.test.mjs`
(Node.js 22+).

### Several trips in config.json

A `config.json` with a single trip uses the flat layout (one object with all
settings). Several trips (at most five) use a `trips` list. Each trip has an `id`
(lowercase letters, digits and dashes) and its own route, dates, filters and price
targets; `primary_trip` names the trip the website shows first. The request settings
(`max_http_attempts_per_run`, `max_run_seconds`, `http_timeout_seconds`,
`http_attempts`, `request_interval_seconds`, `max_parallel_requests`,
`pending_ttl_hours`) are shared by all trips and set once:

```json
{
  "primary_trip": "main",
  "trips": [
    {"id": "main", "origins": ["DUS", "FRA", "AMS"], "destination": "BKK", "...": "..."},
    {"id": "ams", "origins": ["FRA"], "destination": "AMS", "...": "..."}
  ],
  "max_http_attempts_per_run": 1600,
  "max_run_seconds": 2400,
  "...": "the other request settings"
}
```

The original single search is the trip `main`; it keeps its price history. Every
other trip's id is part of its history scope, so renaming a trip's id starts a new
history.

### Changing the search from the website

The dashboard page **Change search** (`settings.html`)
opens a prefilled issue. The **Apply search settings** workflow applies only issues
opened by the repository owner that contain the form marker. `scripts/search_settings.py
apply` validates every setting (supported airports and airlines, ranges, a departure
day from tomorrow on, the request and time budget), then commits `config.json` to the
`search-config` branch with a normal fast-forward push. With several trips or
destinations their requests are added up against the shared budget (each destination
of a trip is searched on its own), and a trip whose departure window has
ended must be given new dates or removed. The form sends all trips in one issue; an
issue with a single trip's settings (as older versions of the form sent) replaces only
the primary trip. It replies with the changes,
closes the issue and dispatches **Track flights**, which publishes the dashboard with
the first prices when it ends. While searches are paused (`TRACKER_ENABLED=false`) it
dispatches only **Publish dashboard**, so the website still shows the new settings. Rejected
settings are answered and closed as not planned; the running search is unchanged.
Settings issues from anyone else are closed as not planned by a separate job that has
only `issues: write` and never reads their content.

`Track flights` and `Publish dashboard` run `scripts/search_settings.py use` first: if
the `search-config` branch exists, its `config.json` replaces the checkout's copy for
that run. Invalid stored settings stop the run instead of silently searching the old
route. To return to `config.json` on `main`, delete the `search-config` branch. While
the branch exists, edits to `config.json` on `main` have no effect.

The workflow needs no secrets. It uses the job token with `contents`, `issues` and
`actions` write permissions. Disabling issues on the repository disables the form.

### Website baggage follow-up

After base prices and notification delivery receipts have been saved, the tracking
workflow runs `python scripts/scan_baggage.py`. It searches at most 18 shortlisted date/category combinations and inspects up to
three concrete itineraries per query through booking details. Included-bag
profiles are derived from those vendor offers, not separate bag-filter searches.
All detail requests share a paced
360-request / 600-second extra budget; no extra full calendar sweeps are run.
The step has a 12-minute timeout and the overall job a 60-minute timeout.
Partial bag observations are checkpointed separately and never enqueue notification
messages or modify the base quote/history tables. A failed baggage step does not
turn the base notification run into a failure; inspect the step and per-profile
website status for baggage coverage.

For a website-only refresh, manually dispatch **Track flights** with
`baggage_only` enabled. This restores the existing state and refreshes baggage
views only; no base calendar scan or notification delivery is performed. A recent
base scan (under 12 hours old) is required. Scheduled runs use the full pipeline.

The SQLite tables `baggage_runs`, `baggage_quotes` and `baggage_checks` are created additively; old
databases export empty bag views until their first follow-up. Public export uses
explicit fields only. No raw responses, booking tokens, chat IDs or secrets are
published. Profiles have separate `tariff-v1` history identifiers. Legacy unconfirmed
filter prices are excluded. Base prices show baggage assessments only for the
exact same run, itinerary and price, checked within 12 hours. The conservative
booking-detail decoder distinguishes included/chargeable/not included/unknown;
unrecognized shapes or enum values stay unknown. Kilogram limits stay unknown.
No airline-wide rules, guessed fees or manually researched one-off prices enter
the tracker. Source coverage is incomplete: a successful detail request with no
qualifying offer is not evidence that no such fare exists.

### Routine maintenance checklist

- Review Dependabot pull requests for pinned actions and Python dependencies.
- Disable the **Search schedule** workflow (and the external timer) after the trip
  window if this repository is not being reused.
- Remove old recovery artifacts when they are no longer useful.
- Keep secrets in GitHub Secrets only; never paste them into issues, logs or commits.
- Run the full offline test suite before changing search or notification logic.

## Verification record

The public deployment was validated with the offline suite and a migration check
against an isolated copy of the existing live state. The migration check preserved
all existing verified quote rows, produced no new price alert for unchanged prices and detected
simulated rises. The authoritative current checks are the green `Tests` workflow and
the run summaries linked from the Actions page; historical acceptance runs remain
available there for context.
