# Architecture

## Overview

```text
GitHub Actions cron
        |
        v
  tracker scan  --->  unofficial Google Flights adapter
        |
        +-----------> SQLite history and alert outbox
                              |
                   checkpoint before delivery
                              |
                              v
                       Telegram Bot API
                              |
                   save delivery receipt
```

The workflow is the only normal writer of the live state. A dedicated state branch
keeps runtime data separate from the application code on `main`.

## Search pipeline

`tracker/planner.py` creates exact departure/return pairs. `tracker/provider.py`
uses the pinned Fli client and applies the request filters. `tracker/service.py`
records every calendar response, shortlists candidates and verifies actual outbound
and return itineraries. The provider contract rejects wrong dates, airports,
currencies, invalid prices, self-transfers flagged by the source and directions at
or above 21 hours.

Calendar rows are estimates. Only verified `Quote` objects enter the notification
path. A verified quote is classified as `nonstop` only when both directions have
zero stops; otherwise it is `layover`.

## History and alert policy

The SQLite database stores:

- `calendar`: date-grid observations, including unknown prices;
- `quotes`: verified round trips and their details;
- `runs`: configuration and health summaries;
- `outbox`: pending/sent Telegram messages;
- `alert_items`: the quote rows represented by each message;
- `meta`: the current stable date watches and health markers.

`tracker/trends.py` keeps one stable watch per airport and actual flight category.
It rechecks those dates first, then uses remaining verification capacity for new
candidates. A new notification requires a €25 movement from the previous alert or
a crossing of the configured budget. New dates are labelled alternatives and never
masquerade as a drop for a different date pair.

The previous 30-day low is calculated from earlier observations only; the current
run is excluded. History scopes include the settings that affect comparability,
including airport, dates, cabin, currency, baggage filters and duration cap.

## Delivery guarantees

The workflow checkpoints pending alerts before Telegram delivery and records the
Telegram message ID afterwards. This gives at-least-once delivery: a network timeout
after Telegram accepts a message can result in a duplicate on retry. Failed sends
remain pending. Pending messages expire after their configured TTL and are filtered
when a date window changes.

## Failure boundaries

The source adapter has request, time and retry budgets. Repeated access denial or RPC
failure stops the affected run rather than fabricating empty or zero-priced results.
State commits are fast-forward only and reject concurrent writers. Recovery artifacts
are retained for seven days by the workflow.

## Repository layout

```text
tracker/                  application and provider adapter
scripts/state_git.py      durable state restore/save
scripts/telegram_setup.py chat-ID discovery workflow helper
tests/                    offline unit and integration tests
.github/workflows/        scheduled, setup, test and notification workflows
config.json               trip and alert configuration
```
