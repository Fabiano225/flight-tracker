import json
import os
from .network import JsonHttp, ServiceError


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
