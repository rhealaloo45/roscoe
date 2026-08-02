"""Web search, SMTP mail, and Twilio SMS. Mocked at the transport — no live calls."""

import httpx
import pytest

from roscoe.connectors import SMTPConnector, TwilioConnector, WebSearchConnector
from roscoe.workflow.registry import build_connectors


def _tool(conn, name):
    return next(t for t in conn.tools if t.name == name)


# --- web search ---


def _search(provider, payload, *, seen=None):
    def handler(request):
        if seen is not None:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=payload)

    return WebSearchConnector(
        {"provider": provider, "api_key": "k"},
        transport=httpx.MockTransport(handler),
    )


def test_tavily_results_are_normalised():
    conn = _search("tavily", {"results": [
        {"title": "A", "url": "https://a.test", "content": "about A"},
    ]})

    out = _tool(conn, "search").invoke({"query": "a thing"})

    assert out["results"] == [
        {"title": "A", "url": "https://a.test", "snippet": "about A"}]


def test_brave_results_are_normalised_to_the_same_shape():
    """Each provider names these fields differently. Normalising here means a
    prompt reading the results doesn't change when the provider does."""
    conn = _search("brave", {"web": {"results": [
        {"title": "B", "url": "https://b.test", "description": "about B"},
    ]}})

    out = _tool(conn, "search").invoke({"query": "b thing"})

    assert out["results"] == [
        {"title": "B", "url": "https://b.test", "snippet": "about B"}]


def test_serper_results_are_normalised_to_the_same_shape():
    conn = _search("serper", {"organic": [
        {"title": "C", "link": "https://c.test", "snippet": "about C"},
    ]})

    out = _tool(conn, "search").invoke({"query": "c thing"})

    assert out["results"] == [
        {"title": "C", "url": "https://c.test", "snippet": "about C"}]


def test_max_results_is_enforced_even_if_the_provider_ignores_it():
    conn = _search("tavily", {"results": [
        {"title": str(i), "url": "", "content": ""} for i in range(10)]})

    assert len(_tool(conn, "search").invoke({"query": "x", "max_results": 3})["results"]) == 3


def test_tavily_sends_a_bearer_token_and_the_others_send_the_key_bare():
    seen = {}
    _tool(_search("tavily", {"results": []}, seen=seen), "search").invoke({"query": "x"})
    assert seen["headers"]["authorization"] == "Bearer k"

    seen = {}
    _tool(_search("brave", {"web": {}}, seen=seen), "search").invoke({"query": "x"})
    assert seen["headers"]["x-subscription-token"] == "k"


def test_an_unknown_provider_is_rejected_by_name():
    """At construction, not at call time — so a typo surfaces when the project
    loads rather than halfway through someone's first run."""
    with pytest.raises(ValueError) as exc:
        WebSearchConnector({"provider": "askjeeves", "api_key": "k"})
    assert "askjeeves" in str(exc.value)
    assert "tavily" in str(exc.value)   # says what would work


def test_search_without_a_key_says_so():
    with pytest.raises(ValueError) as exc:
        WebSearchConnector({"provider": "tavily"})
    assert "api_key" in str(exc.value)


# --- SMTP ---


class _FakeSMTP:
    """Stands in for smtplib.SMTP, recording the conversation."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def _mail(**overrides):
    config = {"host": "smtp.test", "username": "me@test", "password": "pw"}
    config.update(overrides)
    return SMTPConnector(config)


def test_smtp_sends_the_message_it_was_given(fake_smtp):
    _tool(_mail(), "send_email").invoke(
        {"to": "you@test", "subject": "Hi", "body": "Hello there"})

    message = fake_smtp.instances[0].sent[0]
    assert message["To"] == "you@test"
    assert message["From"] == "me@test"
    assert message["Subject"] == "Hi"
    assert "Hello there" in message.get_content()


def test_port_587_upgrades_with_starttls(fake_smtp):
    _tool(_mail(port=587), "send_email").invoke({"to": "a@b", "subject": "s", "body": "b"})

    assert fake_smtp.instances[0].started_tls is True


def test_port_465_uses_ssl_directly_without_starttls(fake_smtp):
    """SMTP_SSL is already encrypted — calling starttls on it is an error."""
    _tool(_mail(port=465), "send_email").invoke({"to": "a@b", "subject": "s", "body": "b"})

    assert fake_smtp.instances[0].started_tls is False


def test_a_separate_from_address_is_honoured(fake_smtp):
    _tool(_mail(**{"from": "noreply@test"}), "send_email").invoke(
        {"to": "a@b", "subject": "s", "body": "b"})

    assert fake_smtp.instances[0].sent[0]["From"] == "noreply@test"


def test_cc_is_included_only_when_given(fake_smtp):
    send = _tool(_mail(), "send_email")

    send.invoke({"to": "a@b", "subject": "s", "body": "b"})
    assert fake_smtp.instances[0].sent[0]["Cc"] is None

    send.invoke({"to": "a@b", "subject": "s", "body": "b", "cc": "c@d"})
    assert fake_smtp.instances[-1].sent[0]["Cc"] == "c@d"


@pytest.mark.parametrize("missing", ["host", "username", "password"])
def test_smtp_names_the_setting_it_is_missing(missing):
    config = {"host": "h", "username": "u", "password": "p"}
    del config[missing]
    with pytest.raises(ValueError) as exc:
        SMTPConnector(config)
    assert missing in str(exc.value)


# --- Twilio ---


def _twilio(handler, **overrides):
    config = {"account_sid": "AC1", "auth_token": "tok", "from": "+15550000000"}
    config.update(overrides)
    return TwilioConnector(config, transport=httpx.MockTransport(handler))


def test_sms_posts_form_encoded_to_the_accounts_messages_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

    out = _tool(_twilio(handler), "send_sms").invoke(
        {"to": "+447700900123", "body": "Deploy finished"})

    assert "/2010-04-01/Accounts/AC1/Messages.json" in seen["url"]
    assert "To=%2B447700900123" in seen["body"]      # form-encoded, not JSON
    assert "Deploy+finished" in seen["body"]
    assert seen["auth"].startswith("Basic ")
    assert out["sid"] == "SM1"


def test_sms_without_a_from_number_says_so_rather_than_failing_at_twilio():
    conn = _twilio(lambda r: httpx.Response(200, json={}))
    conn.config.pop("from")

    with pytest.raises(ValueError) as exc:
        _tool(conn, "send_sms").invoke({"to": "+1", "body": "x"})
    assert "from" in str(exc.value)


@pytest.mark.parametrize("missing", ["account_sid", "auth_token"])
def test_twilio_names_the_credential_it_is_missing(missing):
    config = {"account_sid": "AC1", "auth_token": "tok"}
    del config[missing]
    with pytest.raises(ValueError) as exc:
        TwilioConnector(config)
    assert missing in str(exc.value)


def test_twilio_exposes_no_way_to_unsend():
    """A sent SMS cannot be recalled — a tool implying otherwise would lie."""
    conn = _twilio(lambda r: httpx.Response(200, json={}))

    assert {t.name for t in conn.tools} == {"send_sms"}


# --- wiring ---


@pytest.mark.parametrize("type_name,expected", [
    ("web_search", "WebSearchConnector"),
    ("search", "WebSearchConnector"),
    ("smtp", "SMTPConnector"),
    ("email", "SMTPConnector"),
    ("twilio", "TwilioConnector"),
    ("sms", "TwilioConnector"),
])
def test_each_is_reachable_from_a_connectors_block(type_name, expected):
    config = {
        "web_search": {"api_key": "k"}, "search": {"api_key": "k"},
        "smtp": {"host": "h", "username": "u", "password": "p"},
        "email": {"host": "h", "username": "u", "password": "p"},
        "twilio": {"account_sid": "a", "auth_token": "t"},
        "sms": {"account_sid": "a", "auth_token": "t"},
    }[type_name]

    built = build_connectors({"thing": {"type": type_name, **config}})

    assert type(built["thing"]).__name__ == expected
