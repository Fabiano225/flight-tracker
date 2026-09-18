# Deployment and acceptance checklist

Last inspected: 2026-09-18. This file records evidence, not an operational guarantee.

## Completed locally

- All 648 route/date combinations are enumerated; both calendar profiles cover
  1,296 slots using 48 requests before retries.
- Nonstop/layover classification, strict under-21-hour filtering in both directions,
  separate historical baselines, €650 / 10% + €50 rules, and alert suppression are tested.
- SQLite history, pending-message outbox, synthetic/live isolation and Telegram
  acknowledgement handling are implemented.
- Git state restore/save, fast-forward conflict protection and corruption checks
  have offline integration tests using real local repositories.
- Four daily UTC schedule entries, failure propagation and recovery artifacts are implemented.

## External evidence so far

- Repository: https://github.com/Fabiano225/flight-tracker
- Published commit `5c770aa`: Telegram setup workflow and helper only.
- Telegram setup run `35349481938` reached the helper successfully but found no
  recent private `/start` message. It did not deliver a chat-ID message.
- Secret-name check found `TELEGRAM_BOT_TOKEN`; `TELEGRAM_CHAT_ID` was not yet present.
- Free Google Flights tests returned an HTTP 200 response containing RPC error 13,
  not usable prices. An ordinary cookie-consent Reject all flow reached the Flights
  page, but the subsequent calendar RPC still failed.
- The remaining session-field diagnosis, publishing the complete tracker, and
  further external checks were blocked by the automatic approval review's usage
  limit. The stated reset time was 17:40 on September 18.

## Required before completion

- [ ] Obtain usable live calendar prices with the free source and validate EUR pricing.
- [ ] Verify real outbound/return itineraries, actual stop counts, final round-trip
      prices and both direction durations.
- [ ] Publish the complete tracker to `main` and pass its GitHub CI run.
- [ ] Send the chat ID privately after a fresh `/start`; user saves `TELEGRAM_CHAT_ID`.
- [ ] Run `Track flights` successfully in GitHub Actions.
- [ ] Confirm persistent `tracker-state` creation, then a second run that restores
      and appends history without duplicating unchanged alerts.
- [ ] Confirm Telegram delivery using the repository secrets.
- [ ] Inspect the enabled schedule on the default branch and its next scheduled run.

Offline demo output must never be used as proof of live data or Telegram delivery.
