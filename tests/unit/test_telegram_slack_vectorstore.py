"""Telegram, Slack, and the local vector store. Mocked at the transport — no
live calls, and no filesystem beyond pytest's own tmp_path for the store."""

import httpx
import pytest

from roscoe.connectors import SlackConnector, SlackError, TelegramConnector, VectorStoreConnector
from roscoe.workflow.registry import build_connectors


def _tool(conn, name):
    return next(t for t in conn.tools if t.name == name)


# --- Telegram ---


def _telegram(handler, **overrides):
    config = {"bot_token": "t0k3n", **overrides}
    return TelegramConnector(config, transport=httpx.MockTransport(handler))


def test_send_message_posts_to_the_bot_token_path():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    _tool(_telegram(handler), "send_message").invoke({"text": "hi", "chat_id": "42"})

    assert "/bott0k3n/sendMessage" in seen["url"]


def test_send_message_falls_back_to_the_default_chat_id():
    seen = {}

    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    conn = _telegram(handler, default_chat_id="99")
    _tool(conn, "send_message").invoke({"text": "hi"})

    assert seen["body"]["chat_id"] == "99"


def test_send_message_without_any_chat_id_says_so_clearly():
    conn = _telegram(lambda r: httpx.Response(200, json={"ok": True}))

    with pytest.raises(ValueError) as exc:
        _tool(conn, "send_message").invoke({"text": "hi"})
    assert "chat_id" in str(exc.value)


def test_send_photo_includes_the_caption_only_when_given():
    seen = {}

    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    conn = _telegram(handler, default_chat_id="1")
    _tool(conn, "send_photo").invoke({"photo_url": "https://x.test/p.png"})

    assert "caption" not in seen["body"]


def test_a_missing_bot_token_is_refused():
    with pytest.raises(ValueError) as exc:
        TelegramConnector({})
    assert "bot_token" in str(exc.value)


def test_telegram_is_reachable_from_a_connectors_block():
    built = build_connectors({"tg": {"type": "telegram", "bot_token": "t"}})
    assert type(built["tg"]).__name__ == "TelegramConnector"


# --- Slack ---


def _slack(handler, **overrides):
    config = {"bot_token": "xoxb-t", **overrides}
    return SlackConnector(config, transport=httpx.MockTransport(handler))


def test_send_message_uses_a_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    _tool(_slack(handler), "send_message").invoke({"channel": "#general", "text": "hi"})

    assert seen["auth"] == "Bearer xoxb-t"


def test_slack_ok_false_raises_with_the_reported_error():
    """Slack answers HTTP 200 even on failure — {"ok": false, "error": "..."}
    has to be checked explicitly or a caller would think it worked."""
    conn = _slack(lambda r: httpx.Response(200, json={"ok": False, "error": "channel_not_found"}))

    with pytest.raises(SlackError) as exc:
        _tool(conn, "send_message").invoke({"channel": "#nope", "text": "hi"})
    assert "channel_not_found" in str(exc.value)


def test_read_channel_returns_the_messages_list():
    conn = _slack(lambda r: httpx.Response(200, json={"ok": True, "messages": [{"text": "hi"}]}))

    out = _tool(conn, "read_channel").invoke({"channel": "C1"})

    assert out == [{"text": "hi"}]


def test_a_missing_bot_token_is_refused_slack():
    with pytest.raises(ValueError) as exc:
        SlackConnector({})
    assert "bot_token" in str(exc.value)


# --- Vector store ---


def _store(tmp_path, **overrides):
    return VectorStoreConnector({"path": str(tmp_path / "store.db"), **overrides})


def test_remembering_returns_a_generated_id(tmp_path):
    conn = _store(tmp_path)

    out = _tool(conn, "remember").invoke({"text": "the sky is blue"})

    assert out["stored"] is True
    assert out["id"]


def test_recall_ranks_the_closest_text_first(tmp_path):
    conn = _store(tmp_path)
    remember = _tool(conn, "remember")
    remember.invoke({"text": "the quarterly revenue report is due Friday"})
    remember.invoke({"text": "the cafeteria menu changes every Monday"})
    remember.invoke({"text": "revenue grew twelve percent this quarter"})

    out = _tool(conn, "recall").invoke({"query": "revenue quarter", "top_k": 2})

    assert len(out) == 2
    assert "revenue" in out[0]["text"]
    assert "revenue" in out[1]["text"]


def test_recall_on_an_empty_store_returns_nothing(tmp_path):
    conn = _store(tmp_path)

    assert _tool(conn, "recall").invoke({"query": "anything"}) == []


def test_metadata_round_trips(tmp_path):
    conn = _store(tmp_path)
    _tool(conn, "remember").invoke({"text": "ticket 42 is a login bug", "metadata": {"kind": "bug"}})

    out = _tool(conn, "recall").invoke({"query": "login bug"})

    assert out[0]["metadata"] == {"kind": "bug"}


def test_reusing_an_id_overwrites_rather_than_duplicates(tmp_path):
    conn = _store(tmp_path)
    remember = _tool(conn, "remember")
    remember.invoke({"text": "first version", "id": "note-1"})
    remember.invoke({"text": "second version", "id": "note-1"})

    out = _tool(conn, "recall").invoke({"query": "version"})

    assert len(out) == 1
    assert out[0]["text"] == "second version"


def test_the_file_persists_across_connector_instances(tmp_path):
    path = tmp_path / "persisted.db"
    VectorStoreConnector({"path": str(path)}).tools[0].invoke({"text": "persisted note"})

    reopened = VectorStoreConnector({"path": str(path)})
    out = _tool(reopened, "recall").invoke({"query": "persisted"})

    assert out and out[0]["text"] == "persisted note"


def test_vector_store_is_reachable_from_a_connectors_block(tmp_path):
    built = build_connectors({"memory": {"type": "vector_store", "path": str(tmp_path / "s.db")}})
    assert type(built["memory"]).__name__ == "VectorStoreConnector"
