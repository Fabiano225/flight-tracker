# Deployment and acceptance evidence

Last updated: September 19, 2026 (Europe/Berlin). Evidence is not an upstream uptime guarantee.

## Verified

- Complete project published to `Fabiano225/flight-tracker`, default branch `main`.
- Both Telegram secret names were confirmed through GitHub; values were never retrieved, printed, or committed.
- [Telegram delivery test](https://github.com/Fabiano225/flight-tracker/actions/runs/35404325725) succeeded using those secrets. The sender requires Telegram's message-ID acknowledgement.
- [Initial GitHub CI](https://github.com/Fabiano225/flight-tracker/actions/runs/35404326216) passed. Current local suite: 44 offline tests, including real local Git persistence tests.
- The streaming Google calendar endpoint returned RPC error 13. The adapter now uses the public shopping-prefetch RPC from Google's own search document.
- Live date checks returned EUR fares for FRA and AMS. DUS nonstop searches correctly returned unknown/no offered fare, not zero.
- A selected FRA-BKK round trip, October 15-29, was verified at EUR 659: Etihad, one stop each way, 1,215 minutes outbound and 945 minutes return. This was a search observation, not a reservation or price guarantee, and exceeds the EUR 650 deal threshold.
- The grid contains 648 route/date pairs, searched under both nonstop and any-stops filters: 1,296 initial searches, plus bounded return-selection checks.
- History and alerts enforce separate actual nonstop/layover categories and strictly under 21 hours in both directions. Synthetic prices are isolated from live state.
- Schedule is on `main`: 00:17, 06:17, 12:17, 18:17 UTC. `TRACKER_ENABLED=true`.

## In progress

- [First complete live scan](https://github.com/Fabiano225/flight-tracker/actions/runs/35404770103)
- Verify creation of `tracker-state` and saved Telegram delivery receipts.
- Verify a second run restores and appends history, retaining alert suppression.
- Inspect the first scheduled execution.

## Cost and reliability boundary

The repository is **private**; its visibility was not changed. No flight API fee, paid fallback, proxy, or purchased service is configured. Private-repository GitHub Actions minutes/storage consume the account's included allowance and may incur charges above it depending on billing settings. A 35-minute scan budget is a safety cap, not a measured runtime or guarantee of fitting the free allowance. [GitHub billing documentation](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

The free source is unofficial. Source changes, rate limits, GitHub outages, or an exhausted Actions allowance can interrupt tracking. Failure is surfaced rather than converted to fabricated prices. Only 18 date/profile candidates per run have return itineraries expanded (up to three outbound options each); date-grid coverage does not imply exhaustive coverage of all airlines, fares, or combinations.
