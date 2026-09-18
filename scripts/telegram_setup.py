"""Reply to the latest recent private /start with its chat ID; log no identifiers."""
import json
import os
import sys
import time
from urllib import request


def call(token, method, payload):
    req = request.Request("https://api.telegram.org/bot" + token + "/" + method,
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=35) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError("Telegram request failed; check bot token and try again") from None
    if result.get("ok") is not True:
        raise RuntimeError("Telegram rejected the request; a webhook may already be configured")
    return result["result"]


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Add TELEGRAM_BOT_TOKEN to repository Actions secrets")
    # Never consume/acknowledge updates or remove an existing webhook.
    updates = call(token, "getUpdates", {"timeout": 0, "limit": 100, "allowed_updates": ["message"]})
    eligible = [u["message"] for u in updates if "message" in u
        and u["message"].get("chat", {}).get("type") == "private"
        and u["message"].get("text", "").split(" ")[0] == "/start"
        and u["message"].get("date", 0) >= time.time() - 1800]
    if not eligible:
        raise RuntimeError("Send /start to your bot in a private chat, then rerun Telegram setup within 30 minutes")
    message = max(eligible, key=lambda m: m["date"])
    chat_id = message["chat"]["id"]
    call(token, "sendMessage", {"chat_id": chat_id, "text":
        f"Deine Telegram-Chat-ID: {chat_id}\n\n"
        "Trage diese Nummer in GitHub als Actions-Secret TELEGRAM_CHAT_ID ein:\n"
        "https://github.com/Fabiano225/flight-tracker/settings/secrets/actions\n\n"
        "Den Bot-Token bitte geheim halten."})
    print("Chat ID sent privately through your bot. Add TELEGRAM_CHAT_ID in GitHub, then run the flight tracker.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
