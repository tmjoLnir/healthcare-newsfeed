"""Telegram Bot API client.

Only needs sendMessage against a channel the bot administers. Messages cap
at 4096 characters, so digests arrive as an ordered burst rather than one
message — see digest/render.py.
"""

from __future__ import annotations


class TelegramClient:
    def __init__(self, token: str, chat_id: str) -> None:
        raise NotImplementedError

    def send(self, text: str, *, parse_mode: str = "HTML",
             disable_preview: bool = False) -> dict:
        """POST sendMessage. Retries on 429 using Telegram's retry_after."""
        raise NotImplementedError


class DryRunClient(TelegramClient):
    """Prints to stdout instead of posting. Used by `newsfeed publish --dry-run`."""
