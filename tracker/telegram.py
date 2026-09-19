import json
import os
from .network import JsonHttp, ServiceError
from .store import stamp


class Telegram:
    def __init__(self, token=None, chat_id=None, http=None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not self.token or not self.chat_id:
            raise ServiceError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as GitHub Actions secrets")
        self.http = http or JsonHttp(attempts=3, timeout=30, budget=30, interval=1)

    def send(self, text, reply_to_message_id=None):
        if not 1 <= len(text) <= 4096:
            raise ValueError("Telegram text length is out of bounds")
        payload = {"chat_id": self.chat_id, "text": text,
                   "link_preview_options": {"is_disabled": True}}
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": int(reply_to_message_id),
                                            "allow_sending_without_reply": True}
        data = self.http.get_json("https://api.telegram.org/bot" + self.token + "/sendMessage",
            {"Content-Type": "application/json"}, json.dumps(payload).encode())
        if data.get("ok") is not True or not isinstance(data.get("result"), dict) or "message_id" not in data["result"]:
            raise ServiceError("Telegram did not acknowledge delivery")
        return str(data["result"]["message_id"])


def deliver(store, config, now, sender):
    store.expire(now, config.pending_ttl_hours, config.scope())
    store.expire_outside_search(config)
    store.db.commit()
    sent = 0
    for row in store.db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY created,id").fetchall():
        target = store.db.execute("""SELECT o.message_id FROM outbox_replies r
            JOIN outbox o ON o.id=r.target_id WHERE r.outbox_id=? AND o.status='sent'""",
            (row["id"],)).fetchone()
        if target and target[0]:
            message_id = sender.send(row["text"], reply_to_message_id=target[0])
        else:
            message_id = sender.send(row["text"])
        store.db.execute("UPDATE outbox SET status='sent',sent=?,message_id=? WHERE id=?",
                         (stamp(now), message_id, row["id"]))
        store.db.commit()
        sent += 1
    return sent
