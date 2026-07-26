"""The optional ``ui:`` block — branding the built-in web page, and form mode.

The settings are interpolated straight into HTML and CSS, so the escaping and the
colour check matter as much as the rendering.
"""

from roscoe.cli.run_web import _clean_fields, _css_colour, _render_page, _UI_DEFAULTS


def _page(settings=None, fields=None):
    return _render_page({**_UI_DEFAULTS, **(settings or {})}, fields or [])


# --- settings reach the page ---


def test_defaults_render_without_any_config():
    page = _page()

    assert "__TITLE__" not in page and "__ACCENT__" not in page
    assert "roscoe" in page


def test_every_setting_is_substituted():
    page = _page({
        "title": "Ham Ventures", "subtitle": "IT desk", "heading": "Request access",
        "intro": "Fill this in.", "greeting": "Hello there", "placeholder": "Ask…",
        "submit": "Go",
    })

    for value in ("Ham Ventures", "IT desk", "Request access", "Fill this in.",
                  "Hello there", "Ask…", "Go"):
        assert value in page


def test_markup_in_a_setting_cannot_break_out():
    page = _page({"title": '<script>alert(1)</script>'})

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_accent_is_applied_as_a_css_variable():
    assert "--accent:#7c3aed" in _page({"accent": "#7c3aed"})


def test_only_colour_shaped_values_reach_the_stylesheet():
    assert _css_colour("#7c3aed") == "#7c3aed"
    assert _css_colour("#abc") == "#abc"
    # Anything else falls back rather than being interpolated into CSS.
    assert _css_colour("red; } body { display:none") == _UI_DEFAULTS["accent"]
    assert _css_colour("javascript:alert(1)") == _UI_DEFAULTS["accent"]
    assert _css_colour(None) == _UI_DEFAULTS["accent"]


# --- form fields ---


def test_fields_get_sensible_defaults_from_their_name():
    [field] = _clean_fields([{"name": "employee_id"}])

    assert field["label"] == "Employee id"
    assert field["type"] == "text"
    assert field["required"] is False


def test_a_bare_string_is_accepted_as_a_field():
    assert _clean_fields(["topic"])[0]["name"] == "topic"


def test_malformed_fields_are_dropped_not_fatal():
    fields = _clean_fields([{"label": "no name"}, None, 42, {"name": "ok"}])

    assert [f["name"] for f in fields] == ["ok"]


def test_select_fields_keep_their_options():
    [field] = _clean_fields([
        {"name": "action", "type": "select", "options": ["grant", "revoke"], "default": "grant"}
    ])

    assert field["options"] == ["grant", "revoke"]
    assert field["default"] == "grant"


def test_fields_are_embedded_for_the_page_to_render():
    page = _page(fields=_clean_fields([{"name": "employee_id", "required": True}]))

    assert "__FIELDS__" not in page
    assert '"employee_id"' in page


def test_no_fields_leaves_an_empty_list_so_chat_mode_stays():
    assert "const FIELDS=[];" in _page()
