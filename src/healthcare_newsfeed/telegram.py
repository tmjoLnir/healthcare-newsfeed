"""Telegram Bot API client.

Only needs sendMessage against a channel the bot administers. Messages cap
at 4096 characters, so digests arrive as an ordered burst rather than one
message — see digest/render.py.

Two things shape the rest of this module.

**The token is in the URL.** api.telegram.org authenticates by path, not by
header, so any error that quotes a URL leaks the bot's credentials into CI
logs — where they are readable by anyone who can see the run, and where
rotating them is the only remedy. Every message out of here is redacted.

**A burst is ordered, and half of one is worse than none.** Telegram flood
limits are per-chat and answer with 429 and a `retry_after`, so a digest
that fails on its fourth message leaves an issue posted with its ethics
section missing and no way to append. Retrying transient failures matters
more here than failing fast; what cannot be retried is reported precisely
enough for `newsfeed publish` to say how much went out.
"""

from __future__ import annotations

import os
import time

import httpx

from .config import ConfigError

API = "https://api.telegram.org"
TIMEOUT = 30.0

# The API's hard cap on one message, and the unit it counts in.
MESSAGE_LIMIT = 4096

TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
CHAT_VAR = "TELEGRAM_CHAT_ID"

# Transient by nature: a flood limit we were told to wait out, or Telegram
# itself faltering. Anything else — a bad token, a chat the bot cannot post
# to, malformed HTML — will fail identically however many times it is sent.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
FALLBACK_RETRY_AFTER = 5.0
MAX_RETRY_AFTER = 60.0        # a longer wait than a publish run should sit through

# Telegram's per-chat ceiling is around 20 messages a minute; a digest is a
# handful, so a small gap keeps a burst under the limit rather than
# discovering it through a 429 halfway down the issue.
MIN_GAP = 1.0


def length(text: str) -> int:
    """Message length as Telegram measures it: UTF-16 code units.

    Every section heading in the digest template opens with an emoji, and
    those sit outside the BMP — two units each, not one. The difference is
    small against a 4,096 cap, but digest/render.py fits messages by
    construction, and that only holds if the ruler is the API's.
    """
    return len(text.encode("utf-16-le")) // 2


class TelegramError(RuntimeError):
    """A message could not be delivered."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TelegramClient:
    def __init__(self, token: str, chat_id: str, *,
                 client: httpx.Client | None = None, gap: float = MIN_GAP) -> None:
        if not token or not chat_id:
            raise TelegramError(f"both {TOKEN_VAR} and {CHAT_VAR} are required")
        self.token = token
        self.chat_id = chat_id
        self.gap = gap
        self._client = client
        self._owns_client = client is None
        self._last_sent: float | None = None

    @classmethod
    def from_env(cls, **kwargs) -> TelegramClient:
        """Build from the two variables .env.example documents."""
        token = os.environ.get(TOKEN_VAR, "").strip()
        chat_id = os.environ.get(CHAT_VAR, "").strip()
        missing = [name for name, value in ((TOKEN_VAR, token), (CHAT_VAR, chat_id)) if not value]
        if missing:
            raise ConfigError(
                f"{' and '.join(missing)} not set — create the bot with @BotFather, "
                f"add it to the channel as an administrator with 'Post messages', "
                f"and see .env.example"
            )
        return cls(token, chat_id, **kwargs)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
            self._client = httpx.Client(verify=ca or True)
        return self._client

    def redact(self, text: str) -> str:
        """Strip the bot token out of anything on its way to a log."""
        return text.replace(self.token, "<token>") if self.token else text

    def send(self, text: str, *, parse_mode: str = "HTML",
             disable_preview: bool = False) -> dict:
        """POST sendMessage. Retries on 429 using Telegram's retry_after."""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            # The modern spelling of disable_web_page_preview. A digest links
            # several stories per message and Telegram would preview only the
            # first, so publish turns previews off rather than illustrating
            # six items with one arbitrary thumbnail.
            "link_preview_options": {"is_disabled": disable_preview},
        }
        self._pace()

        last: TelegramError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result, retry_after = self._attempt(payload)
            except TelegramError as exc:
                if exc.status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    raise
                last, retry_after = exc, None
            else:
                if retry_after is None:
                    self._last_sent = time.monotonic()
                    return result
                if attempt == MAX_ATTEMPTS:
                    break
                last = TelegramError("rate limited", status=429)

            time.sleep(min(retry_after or FALLBACK_RETRY_AFTER * attempt, MAX_RETRY_AFTER))

        raise TelegramError(
            f"giving up after {MAX_ATTEMPTS} attempts: {last}", status=last.status if last else None
        )

    def _attempt(self, payload: dict) -> tuple[dict, float | None]:
        """One POST. Returns (result, None), or ({}, retry_after) to wait."""
        try:
            response = self.client.post(f"{API}/bot{self.token}/sendMessage",
                                        json=payload, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            # Network failures carry the request URL, and the URL is the token.
            raise TelegramError(self.redact(str(exc)), status=503) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramError(
                f"HTTP {response.status_code}: response was not JSON "
                f"(a proxy or captive portal, most likely)",
                status=response.status_code,
            ) from exc

        if body.get("ok"):
            return body.get("result") or {}, None

        description = self.redact(str(body.get("description") or "no description"))
        wait = (body.get("parameters") or {}).get("retry_after")
        if response.status_code == 429:
            return {}, float(wait or FALLBACK_RETRY_AFTER)
        raise TelegramError(f"HTTP {response.status_code}: {description}",
                            status=response.status_code)

    def _pace(self) -> None:
        """Hold MIN_GAP between messages so a burst stays under the flood limit."""
        if self.gap <= 0 or self._last_sent is None:
            return
        remaining = self.gap - (time.monotonic() - self._last_sent)
        if remaining > 0:
            time.sleep(remaining)

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
        self._client = None

    def __enter__(self) -> TelegramClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class DryRunClient(TelegramClient):
    """Prints to stdout instead of posting. Used by `newsfeed publish --dry-run`."""

    def __init__(self, *, gap: float = 0.0) -> None:
        # Placeholder credentials, then blanked: previewing an issue is
        # exactly what someone does before they have a token, and demanding
        # one to print to stdout would make the dry run useless for the case
        # it exists for. Nothing here opens a connection.
        super().__init__("dry-run", "(dry run)", gap=gap)
        self.token = ""
        self.sent: list[str] = []

    def send(self, text: str, *, parse_mode: str = "HTML",
             disable_preview: bool = False) -> dict:
        self.sent.append(text)
        print(f"── message {len(self.sent)} "
              f"({length(text)}/{MESSAGE_LIMIT} chars, {parse_mode}) {'─' * 22}")
        print(text)
        print()
        return {"message_id": len(self.sent), "dry_run": True}
