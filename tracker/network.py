from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import time
from urllib import request, error


class ServiceError(RuntimeError):
    """Only sanitized messages belong here; response bodies may contain secrets."""


class BudgetError(ServiceError):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonHttp:
    def __init__(self, attempts=3, timeout=60, budget=30, interval=1, opener=None, sleep=time.sleep):
        self.attempts, self.timeout, self.budget, self.interval = attempts, timeout, budget, interval
        self.opener = opener or request.build_opener(NoRedirect())
        self.sleep = sleep
        self.used = 0

    def get_json(self, url, headers=None, body=None):
        for attempt in range(self.attempts):
            if self.used >= self.budget:
                raise BudgetError("HTTP request budget exhausted")
            if self.used:
                self.sleep(self.interval)
            self.used += 1
            retry_delay = min(2 ** attempt, 30)
            req = request.Request(url, data=body, headers=headers or {})
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read(8_000_001)
                    if len(raw) > 8_000_000:
                        raise ServiceError("API response exceeded size limit")
                    data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ServiceError("Invalid API response structure")
                return data
            except error.HTTPError as exc:
                status = exc.code
                if status not in {408, 429, 500, 502, 503, 504}:
                    raise ServiceError(f"API HTTP {status}; check service settings and credentials") from None
                delay = exc.headers.get("Retry-After", "")
                try:
                    retry_delay = max(retry_delay, float(delay))
                except ValueError:
                    try:
                        retry_delay = max(retry_delay, (parsedate_to_datetime(delay) - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        pass
                try:
                    detail = json.loads(exc.read(16384))
                    retry_delay = max(retry_delay, float(detail.get("parameters", {}).get("retry_after", 0)))
                except (ValueError, TypeError, AttributeError):
                    pass
                # Do not retry sooner than the server requested; defer long waits.
                if retry_delay > 120:
                    raise ServiceError(f"API HTTP {status}; retry deferred to next run") from None
            except (error.URLError, TimeoutError, OSError):
                status = "network timeout/error"
            except (ValueError, UnicodeError):
                raise ServiceError("API returned invalid JSON") from None
            if attempt + 1 == self.attempts:
                raise ServiceError(f"API failed after {self.attempts} attempts ({status})") from None
            self.sleep(retry_delay)
