"""The Bot API client.

Offline: httpx.MockTransport stands in for api.telegram.org, so the retry
policy, the failure taxonomy and the token redaction are all exercised
without a bot or a network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from healthcare_newsfeed.config import ConfigError
from healthcare_newsfeed.telegram import (
    CHAT_VAR,
    MAX_ATTEMPTS,
    TOKEN_VAR,
    DryRunClient,
    TelegramClient,
    TelegramError,
    length,
)

TOKEN = "123456:AA-SECRET-TOKEN-VALUE"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Retries are real; the sleeps between them are not worth the suite's time."""
    monkeypatch.setattr("healthcare_newsfeed.telegram.time.sleep", lambda seconds: None)


def responder(*responses):
    """Answer each request with the next response, repeating the last."""
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    handle.seen = seen
    return handle


def client(handle, **kwargs) -> TelegramClient:
    transport = httpx.MockTransport(handle)
    return TelegramClient(TOKEN, "@channel", client=httpx.Client(transport=transport),
                          gap=0.0, **kwargs)


def ok(message_id: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def flood(retry_after: int = 3) -> httpx.Response:
    return httpx.Response(429, json={"ok": False, "error_code": 429,
                                     "description": "Too Many Requests",
                                     "parameters": {"retry_after": retry_after}})


# --- sending ----------------------------------------------------------------

def test_a_message_is_posted_to_the_configured_chat():
    handle = responder(ok(7))

    assert client(handle).send("hello") == {"message_id": 7}

    payload = json.loads(handle.seen[0].content)
    assert payload["chat_id"] == "@channel"
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "HTML"
    assert handle.seen[0].url.path.endswith("/sendMessage")


def test_previews_are_off_when_publish_asks_for_that():
    """A digest links several stories; Telegram would preview only the first."""
    handle = responder(ok())

    client(handle).send("hello", disable_preview=True)

    assert json.loads(handle.seen[0].content)["link_preview_options"] == {"is_disabled": True}


# --- retrying ---------------------------------------------------------------

def test_a_flood_limit_is_waited_out_rather_than_dropping_the_message():
    """Half a digest is worse than a slow one."""
    handle = responder(flood(), flood(), ok(9))

    assert client(handle).send("hello") == {"message_id": 9}
    assert len(handle.seen) == 3


def test_a_persistent_flood_limit_eventually_gives_up():
    handle = responder(flood())

    with pytest.raises(TelegramError, match="giving up"):
        client(handle).send("hello")
    assert len(handle.seen) == MAX_ATTEMPTS


def test_telegram_faltering_is_retried():
    handle = responder(httpx.Response(502, json={"ok": False, "description": "Bad Gateway"}),
                       ok(4))

    assert client(handle).send("hello") == {"message_id": 4}


def test_a_rejected_message_is_not_retried():
    """Malformed HTML fails identically however many times it is sent."""
    handle = responder(httpx.Response(400, json={
        "ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}))

    with pytest.raises(TelegramError, match="can't parse entities"):
        client(handle).send("<b>broken")
    assert len(handle.seen) == 1


def test_a_bad_token_is_not_retried():
    handle = responder(httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))

    with pytest.raises(TelegramError, match="Unauthorized"):
        client(handle).send("hello")
    assert len(handle.seen) == 1


def test_a_response_that_is_not_json_is_reported_as_such():
    handle = responder(httpx.Response(502, text="<html>Bad Gateway</html>"))

    with pytest.raises(TelegramError, match="not JSON"):
        client(handle).send("hello")


# --- the token is in the URL ------------------------------------------------

def test_a_network_error_does_not_leak_the_token():
    """api.telegram.org authenticates by path, and CI logs are readable."""
    def explode(request):
        raise httpx.ConnectError(f"connection refused: {request.url}")

    with pytest.raises(TelegramError) as caught:
        client(explode).send("hello")

    assert TOKEN not in str(caught.value)
    assert "<token>" in str(caught.value)


def test_an_api_error_quoting_the_url_does_not_leak_the_token():
    handle = responder(httpx.Response(404, json={
        "ok": False, "description": f"Not Found: /bot{TOKEN}/sendMessage"}))

    with pytest.raises(TelegramError) as caught:
        client(handle).send("hello")

    assert TOKEN not in str(caught.value)


# --- credentials ------------------------------------------------------------

def test_credentials_come_from_the_documented_variables(monkeypatch):
    monkeypatch.setenv(TOKEN_VAR, TOKEN)
    monkeypatch.setenv(CHAT_VAR, "@channel")

    built = TelegramClient.from_env()

    assert built.token == TOKEN and built.chat_id == "@channel"


@pytest.mark.parametrize("present", [TOKEN_VAR, CHAT_VAR])
def test_a_missing_credential_points_at_the_setup_it_needs(monkeypatch, present):
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    monkeypatch.delenv(CHAT_VAR, raising=False)
    monkeypatch.setenv(present, "value")

    with pytest.raises(ConfigError, match="BotFather"):
        TelegramClient.from_env()


def test_a_blank_credential_counts_as_missing(monkeypatch):
    """An unset Actions secret interpolates to an empty string, not an absence."""
    monkeypatch.setenv(TOKEN_VAR, "  ")
    monkeypatch.setenv(CHAT_VAR, "@channel")

    with pytest.raises(ConfigError):
        TelegramClient.from_env()


# --- dry run ----------------------------------------------------------------

def test_a_dry_run_needs_no_credentials(capsys):
    """Previewing an issue is what someone does before they have a token."""
    dry = DryRunClient()

    dry.send("<b>Issue 12</b>")

    assert dry.sent == ["<b>Issue 12</b>"]
    assert "<b>Issue 12</b>" in capsys.readouterr().out


def test_a_dry_run_opens_no_connection(capsys):
    dry = DryRunClient()
    dry.send("hello")
    dry.close()

    assert dry._client is None


# --- measurement ------------------------------------------------------------

def test_length_counts_utf16_units_the_way_telegram_does():
    assert length("abc") == 3
    assert length("🔬") == 2                      # outside the BMP
    assert length("🔬 Story of the week") == len("🔬 Story of the week") + 1
