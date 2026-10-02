# ✈️ Flightwatch – a free flight price tracker

**[Open the dashboard ↗](https://fabiano225.github.io/flight-tracker/)**

[![Tests](https://github.com/Fabiano225/flight-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/Fabiano225/flight-tracker/actions/workflows/tests.yml)
[![Track flights](https://github.com/Fabiano225/flight-tracker/actions/workflows/track-flights.yml/badge.svg)](https://github.com/Fabiano225/flight-tracker/actions/workflows/track-flights.yml)

Flightwatch watches round-trip fares for flexible travel dates, keeps a real price
history and tells you on Discord or Telegram when a price is worth a look. It runs
entirely on GitHub: no server, no paid flight API, no account to sign up for.

Right now it tracks flights from **Düsseldorf, Frankfurt and Amsterdam to Bangkok**
in October 2026. The trip can be changed on the website at any time, and up to five
trips can be tracked at once.

> A personal travel project. Prices come from Google Flights' public (unofficial)
> search, which can change without notice. Always check the fare before booking.

## What you get

- **A dashboard** with the cheapest checked flights, filters for airport, dates, trip
  length, stops and airlines, and a price-history chart for every flight comparison.
- **Price alerts** when a fare drops, rises, reaches your price target or hits a new
  low, plus a short "no change" note after searches without news.
- **Flexible dates:** every departure day in your window is combined with every trip
  length you allow (for example 14–21 days).
- **Fair comparisons:** non-stop and connecting flights are kept apart, overly long
  journeys are filtered out, and only actually checked round trips count.
- **Baggage views** that show fares whose booking offer includes a cabin bag, a
  checked bag or both.
- **Favorites** saved in your browser, **light/dark mode**, and a layout that works on
  phones.

## How it works

1. Four times a day a GitHub Actions run searches every date combination.
2. The most promising flights are checked in detail (real round trip, dates, stops,
   travel time).
3. Prices are stored and compared with the last 30 days.
4. If something changed meaningfully, you get a message; the dashboard updates a few
   minutes later.

## The dashboard

- **Trips:** with several trips, a bar at the top switches between them. Each trip has
  its own link, e.g. `…/flight-tracker/?trip=ams`.
- **Filters:** departure airport, connection type, outbound day, trip length and
  airlines (show only or hide selected airlines).
- **Price history:** pick a flight to see every checked price, your price target and
  the lowest price of the comparison period.
- **Baggage:** choose *Base price*, *1 cabin bag*, *1 checked bag* or both. Baggage views
  only list offers that state the bags are included for the whole trip; they never
  guess a fee.
- **Favorites:** save a flight with the star. Favorites stay in this browser only.
- **Theme:** the selector in the header switches between *Auto* (follows your device),
  *Light* and *Dark*.

The page clearly says when a search was incomplete or the data is older than 12 hours.

## Price alerts

Alerts go to a Discord channel (or a Telegram chat) and name their trip in the title.

| Message | Meaning |
|---|---|
| **PRICE DROPPED** / **PRICE ROSE** | The same flight dates changed by at least €25 since the last alert. |
| **CHECK TO BUY** | At or below your price target and close to the lowest price seen. |
| **WITHIN BUDGET, but …** | Within your target, but it has been at least €25 cheaper. |
| **WATCH** | Still above your target. |
| **STRONG DEAL** | At least 10% **and** €50 below the lowest price of the last 30 days. |
| **CHEAPER ALTERNATIVE** | Other dates are clearly cheaper than the ones you were alerted about. |

Searches without a new alert send a short **"No price change"** or **"Only small price
changes"** note, linked to the last alert. When a trip's travel window has passed, one
message says so and links to the settings page. A missing search result is never
treated as a price change.

## Search settings (website)

Open **Change search** on the dashboard to change what is tracked: departure airports,
destination, travel dates, trip length, cabin, stops, airlines, maximum travel time,
price targets and when to alert. Airports can be searched by city, airport name or
code (German names such as "München" work too).

- **Several trips:** use **+ Add trip** for up to five trips, e.g. Bangkok in October
  and a weekend in Amsterdam. Each trip has its own price targets, alerts and history;
  choose which one the website shows first.
- **Price targets per airport:** prices can differ a lot between departure airports
  (for Bangkok, Amsterdam is often well over €100 cheaper than Düsseldorf). Optionally
  give an airport its own target; alerts and the website then judge each flight by
  the target of its airport.
- **Suggested prices:** once a trip has checked prices, the form suggests targets per
  airport from them (the price a quarter of them reached) and fills them in with one
  click. A new trip only gets a rough guide from the flight distance, which knows
  nothing about differences between airports.
- **Request estimate:** the form shows how many searches a run needs. All trips share
  one budget, so very wide date ranges or many trips are rejected before anything is
  sent.
- **Applying:** the form opens a prefilled GitHub issue. Once you create it, a workflow
  checks the settings, saves them, replies in the issue and starts a new search. First
  prices appear after about 10–40 minutes.

Only issues from the repository owner are applied; anyone else's are closed
automatically. Settings are stored on the `search-config` branch; deleting that
branch returns to `config.json` on `main`.

## Get notifications

### Discord (recommended)

1. In a Discord server text channel, open **Edit Channel → Integrations → Webhooks →
   New Webhook** and copy the webhook URL.
2. Add it as the repository secret **`DISCORD_WEBHOOK_URL`**
   (**Settings → Secrets and variables → Actions**). Treat it like a password.
3. Run the **Test Discord** workflow to receive a test message.

### Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and add its token
   as the secret **`TELEGRAM_BOT_TOKEN`**.
2. Send `/start` to your bot, then run the **Telegram setup** workflow; it replies with
   your chat ID.
3. Save that as the secret **`TELEGRAM_CHAT_ID`** and run **Test Telegram**.

If both are set up, Discord is used. The repository variable `NOTIFICATION_CHANNEL`
(`auto`, `discord` or `telegram`) picks one explicitly.

## Run your own copy

You need a free GitHub account; no programming and no code changes. All links,
the website address and the settings form adjust to your copy automatically.

1. **Fork** this repository (button at the top right on GitHub). Keep "Copy the `main`
   branch only" ticked, so you start with your own empty price history.
2. In your copy, open the **Actions** tab and click **"I understand my workflows, go
   ahead and enable them"**.
3. **Settings → Pages:** under *Build and deployment*, choose **GitHub Actions** as the
   source.
4. **Settings → General → Features:** tick **Issues** (the settings form uses them).
5. **Actions → Check setup → Run workflow.** The run shows a checklist of what is
   done and what is still missing, with links to the right settings pages.
6. **Actions → Track flights → Run workflow** for the first search. Your website is
   at `https://<your-user-name>.github.io/<repository-name>/`.
7. Open **Change search** on your website and set your own trip. Optional: set up
   Discord or Telegram (above) for price alerts; without them only the website
   updates.

Good to know:

- Searches then run by themselves four times a day. To pause them, add the
  repository variable `TRACKER_ENABLED` with the value `false`
  (**Settings → Secrets and variables → Actions → Variables**).
- Using your own domain for GitHub Pages? Add the variable `DASHBOARD_URL` (for example
  `https://flights.example.org/`) so links in messages point there.
- Only you, the owner of the copy, can change its search through the form.

More on operation and troubleshooting: [DEPLOYMENT.md](DEPLOYMENT.md).

## Costs and privacy

- **Free:** GitHub-hosted runners cost nothing for public repositories
  ([GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)).
  No paid API, proxy or CAPTCHA service is used.
- **Public data:** the repository, the website and the `tracker-state` branch show the
  tracked routes, dates and prices. Don't use it for trips you want to keep private.
- **Secrets stay secret:** webhook URLs, bot tokens and chat IDs exist only as GitHub
  secrets; they are never written to the repository, the state or the website.
- **No tracking:** the website uses no cookies or analytics. Favorites and the theme
  choice are stored only in your browser.

## Limitations

- GitHub can start scheduled runs late or skip them.
- Google may limit or change its search; the tracker then reports an incomplete search
  instead of guessing.
- Only a shortlist of flights is checked in detail, so a cheaper itinerary can be
  missed.
- Prices are search results, not reservations, and can change before you book.

## For developers

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python -m unittest discover -s tests -v
node --test tests/site_model.test.mjs tests/search_model.test.mjs
python -m tracker demo --as-of 2026-09-18   # offline demo with synthetic prices
```

Other commands: `python -m tracker plan`, `report`, `export --output history.csv`,
`discord-test`, `telegram-test`. Don't run a live scan locally while the GitHub
workflow is running.

| Workflow | Purpose |
|---|---|
| `Track flights` | Searches, checks flights, saves the history and sends messages |
| `Publish dashboard` | Builds the website from the saved data |
| `Apply search settings` | Applies settings issues from the owner |
| `Tests` | Offline tests; no secrets, no live searches |
| `Check setup` | Checklist of what a copy still needs |
| `Test Discord` / `Test Telegram` / `Telegram setup` | Notification setup and tests |

History lives in a SQLite database on the `tracker-state` branch. Old observations are
cleaned up automatically, so it stays small. The search uses the open-source
[`fli`](https://github.com/punitarani/fli) client. Details on the data model, alerts,
multi-trip runs and the website: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).
