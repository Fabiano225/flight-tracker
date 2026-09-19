# Deployment and acceptance evidence

## Current date-window revision

User correction on September 19: departure October 15-20 with +/- 3 days means
**October 12-23 inclusive**, each with **14-21 days** between departures.
This is 288 route/date combinations and 576 profile searches. Latest return: November 13.
Compatible price history is retained, but pending digests containing out-of-window
trips expire before delivery. The original acceptance evidence and runtime figures
below describe the previous, broader window, not the current configuration.

## Original deployment evidence

Verified September 19, 2026. These observations establish the deployed behavior, not an upstream uptime guarantee.

## Successful live runs

- [Manual acceptance run](https://github.com/Fabiano225/flight-tracker/actions/runs/35405980332): success, code `5256302`, completed September 19 at 00:04 UTC.
- [Automatic scheduled run](https://github.com/Fabiano225/flight-tracker/actions/runs/35422068076): GitHub event `schedule`, success, started 04:43 UTC and completed 05:16 UTC.
- Both runs completed **48/48 checkpoint batches and exactly 1,296 date/profile searches**, covering all **648 route/date pairs** with nonstop and any-stops filters.
- Each full run recorded 1,080 quoted prices and 216 unknown/no-offer slots. Missing fares are not zero prices.
- Each full run verified 18 selected outbound/return itineraries; both completed without recorded errors.
- The earlier [partial attempt](https://github.com/Fabiano225/flight-tracker/actions/runs/35404770103) is retained in history, not presented as complete coverage. Longer pacing and bounded retries for transient RPC INTERNAL errors resolved the observed interruption in both subsequent full runs.

## Requirement-by-requirement audit

| Requirement | Evidence |
|---|---|
| DUS/FRA/AMS to BKK | Live database keys and verified itinerary endpoints; no DMK substitution |
| October 15-November 10 departures; 14-21 days | Exact set equality against all 1,296 expected profile/date/route keys in each successful run; latest return departure December 1 |
| Historical prices | `tracker-state` survived two restores and appended rather than replaced history: 3 runs, 3,078 date observations, 48 verified quotes |
| Separate nonstop/layover categories | Every stored verified quote was checked: nonstop iff both stop counts are zero |
| Exclude 21h+ | All 48 stored verified quotes have both directions strictly below 1,260 minutes, including layovers |
| Good deals and price drops | EUR 650 separately configurable by category; prior 30-day low, 10% AND EUR 50 drop tests; initial observations do not invent a drop baseline |
| Telegram | Repository-secret connection test plus 3 acknowledged live deal digests containing 18 deal items; 2 acknowledged health/recovery messages; no pending messages |
| Avoid unchanged repeat alerts | Audit of all 18 sent deal items found no repeat lacking the required further EUR 25 improvement; offline tests also cover suppression and qualifying drops |
| Persistent state integrity | SQLite quick check and foreign-key check passed; prior run/receipt rows remain present |
| Four daily GitHub Actions triggers | Published default-branch cron: 00:17, 06:17, 12:17, 18:17 UTC; workflow active and `TRACKER_ENABLED=true`; actual `schedule` event completed successfully |
| Automated validation | 46 offline tests passed locally and in [GitHub CI](https://github.com/Fabiano225/flight-tracker/actions/runs/35405980282), including real local Git persistence integration tests |
| Free flight source | Public Google Flights shopping-prefetch RPC through pinned Fli/curl-cffi dependencies; no API key, subscription, proxy, or paid fallback |

Price-drop logic is tested with controlled history; these live runs do not claim a naturally occurring qualifying market drop. Telegram API acknowledgement proves accepted delivery, not that the recipient read the messages.

## Source and coverage boundaries

The streaming Google calendar endpoint returned RPC error 13 during development. The adapter uses the public shopping-prefetch RPC observed in Google's search document, querying each date/profile individually. Full date-grid coverage is not exhaustive enumeration of every airline, fare or itinerary: only 18 date/profile candidates per run have return choices expanded, with up to three outbound options each. Only checked round trips enter deal alerts. Prices remain search observations, not reservations or guaranteed bookable fares.

The source is unofficial. Format changes, rate limits, outages or exhausted Actions allowance can interrupt tracking. Explicit access/rate denials stop further source requests during that run; no proxy rotation or CAPTCHA handling is implemented. Partial results and errors are retained and reported rather than fabricated.

## Schedule and cost boundaries

GitHub scheduling is best effort, not a precise clock. The observed scheduled run began at 04:43 UTC, later than a nominal cron slot. The workflow contains four daily triggers; GitHub can delay or skip executions under load.

The repository remains **private**. Successful full jobs took approximately **33-34 minutes**. At four runs every day with the initial search-window size, that projects to about **4,000 Actions minutes per 30 days**, before CI or other repositories; the date window shrinks after October 15. This can exceed the account's included allowance. No billing settings, spending limits, repository visibility, or paid subscriptions were changed. A 40-minute source budget and 45-minute job timeout bound each run.

The flight source has no API fee; private-repository Actions minutes/storage may incur charges above the included allowance depending on account settings. Check [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) and configure a spending budget if a strict cost cap is required. A cost cap may stop tracking when the allowance is exhausted.
