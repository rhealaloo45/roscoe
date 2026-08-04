"""One product of a suite, offered as its own connector.

A whole suite behind a single catalogue entry reads as one option when it is
really a dozen. "Google Workspace" hid Calendar, Tasks and Drive completely:
nothing named them until after it had been added, and then all thirteen
methods arrived at once whatever the agent was for. Each product is now its
own entry, running on the same class and credentials, narrowed to its own
tools.
"""

import pytest

from roscoe.connectors.catalog import CATALOG, catalog
from roscoe.workflow.registry import build_connectors, get_connector_class

OAUTH = {"client_id": "id", "client_secret": "secret", "refresh_token": "token"}

PRODUCTS = {
    "gmail": {"send_email", "read_emails"},
    "google_calendar": {"list_events", "create_event", "update_event", "delete_event"},
    "google_tasks": {"list_tasks", "create_task", "complete_task", "delete_task"},
    "google_drive": {"search_drive", "read_drive_file", "upload_drive_file"},
}


@pytest.mark.parametrize(("type_name", "expected"), sorted(PRODUCTS.items()))
def test_a_product_connector_offers_only_its_own_tools(type_name, expected):
    built = build_connectors({"g": {"type": type_name, **OAUTH}})

    assert {t.name for t in built["g"].tools} == expected


def test_the_whole_suite_is_still_available_as_one_connector():
    """Splitting the suite up must not take the all-in-one option away — a
    project already pointing at `google_workspace` keeps working untouched."""
    built = build_connectors({"g": {"type": "google_workspace", **OAUTH}})

    names = {t.name for t in built["g"].tools}
    assert names == set().union(*PRODUCTS.values())


def test_every_product_shares_the_suite_connector_class():
    suite = get_connector_class("google_workspace")

    for type_name in PRODUCTS:
        assert get_connector_class(type_name) is suite


def test_a_narrowed_connector_still_delegates_everything_else():
    """Only the tool list is narrowed; the wrapper must not shadow the real
    connector's own attributes."""
    built = build_connectors({"g": {"type": "gmail", **OAUTH}})

    assert built["g"].config["client_id"] == "id"
    assert built["g"]._auth_mode == "oauth"  # noqa: SLF001 — checking delegation


@pytest.mark.parametrize("type_name", sorted(PRODUCTS))
def test_each_product_is_in_the_picker_with_its_own_mark(type_name):
    entry = next(e for e in catalog() if e["type"] == type_name)

    assert entry["label"]
    assert entry["icon"].startswith("<svg")
    assert entry["color"], "a real brand mark should carry its brand colour"
    # Same two ways in as the suite itself — one credential set covers all.
    assert [m["key"] for m in entry["auth_modes"]] == ["oauth", "service_account"]


def test_products_are_spread_across_the_categories_they_belong_to():
    """Filing all four under Communication would just move the pile — Calendar
    and Tasks are productivity, Drive is documents."""
    by_type = {e["type"]: e["category"] for e in catalog()}

    assert by_type["gmail"] == "Communication"
    assert by_type["google_calendar"] == "Productivity"
    assert by_type["google_tasks"] == "Productivity"
    assert by_type["google_drive"] == "Documents"


def test_the_catalogue_has_no_duplicate_labels():
    labels = [spec["label"] for spec in CATALOG.values()]

    assert len(labels) == len(set(labels))
