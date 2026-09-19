# Operations guide

This document describes the live deployment of the BKK flight tracker. It is
written for the repository maintainer; the user-facing overview is in
[README.md](README.md).

## Current production configuration

- **Repository:** `Fabiano225/flight-tracker`
- **Schedule:** 00:17, 06:17, 12:17 and 18:17 UTC
- **Search window:** departures 15–23 October 2026; 14–21-day trips
- **Routes:** DUS/FRA/AMS → BKK, one adult, economy, EUR
- **Duration guard:** below 21 hours in each direction
- **State branch:** `tracker-state`
- **Runtime switch:** repository variable `TRACKER_ENABLED`

The current code records stable date watches and sends change alerts instead of a
full price digest every six hours. Existing quote history is retained when the
notification policy changes.

## First-time deployment checklist

- [ ] `TELEGRAM_BOT_TOKEN` exists as a repository Actions secret.
- [ ] The bot has received `/start` from the intended recipient.
- [ ] `TELEGRAM_CHAT_ID` exists as a repository Actions secret.
- [ ] The **Test Telegram** workflow completes successfully.
- [ ] The **Tests** workflow is green on the default branch.
- [ ] `TRACKER_ENABLED` is absent or set to `true`.
- [ ] The **Track flights** workflow has write permission for the `tracker-state`
      branch (`contents: write` in the workflow).

## Normal run sequence

The tracking job intentionally follows this order:

1. Checkout code and install pinned top-level dependencies.
2. Run offline tests.
3. Restore the complete state database from `tracker-state`.
4. Search calendars and verify a bounded set of round trips.
5. Save history and pending alerts **before** sending Telegram messages.
6. Deliver pending messages and save delivery receipts in a second state commit.
7. Upload a seven-day recovery artifact.

If the source or Telegram fails, the state remains inspectable and pending messages
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
- **Deliver pending Telegram alerts:** Telegram response and message count.
- **Recovery snapshot:** downloadable state files for incident recovery.

The job summary and `state/report.md` contain counts without exposing credentials.
An unknown fare is recorded as unknown; it is never converted to zero.

## Common situations

### No Telegram message

Check that both secret names are exact, the bot is not blocked, and the chat ID is
the recipient's private chat ID. Run **Test Telegram**. A successful search with no
price change is intentionally silent.

### A price appears to rise

The tracker reports a rise only for the same airport, exact dates and actual flight
type. Missing results do not produce rises. The alert shows the previous measured
price and timestamp so the comparison can be audited.

### Google source errors or a partial run

Treat RPC errors, consent pages and rate limits as source-health events, not as “no
flights”. Completed batches remain in SQLite. Wait for a later scheduled run rather
than deleting state or starting several concurrent scans.

### State restore or push failure

Do not delete `history.sqlite3`. Keep the recovery artifact, pause the workflow with
`TRACKER_ENABLED=false`, inspect the state branch and resolve the Git conflict before
resuming. The state writer rejects concurrent updates rather than overwriting them.

### Pause tracking

Set the repository Actions variable `TRACKER_ENABLED` to `false`. The workflow file
can remain in place; no scheduled scan will run until the variable is removed or
changed.

## Maintenance

- Review Dependabot pull requests for pinned actions and Python dependencies.
- Disable the schedule after the October/November trip window if this repository is
  not being reused.
- Remove old recovery artifacts when they are no longer useful.
- Keep secrets in GitHub Secrets only; never paste them into issues, logs or commits.
- Run the full offline test suite before changing search or notification logic.

## Verification record

The public deployment was validated with the offline suite and a migration check
against an isolated copy of the existing live state. The migration check preserved
all existing verified quote rows, produced no alert for unchanged prices and detected
simulated rises. The authoritative current checks are the green `Tests` workflow and
the run summaries linked from the Actions page; historical acceptance runs remain
available there for context.
