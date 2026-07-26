"""Database connector — SQLite by default, any DB-API driver by name.

The safety tests matter most: this hands SQL to a tool-calling model, so writes must
be opt-in, values must bind as parameters, and a second statement must not be able to
ride along behind a semicolon.
"""

import sqlite3

import pytest

from roscoe.connectors import DatabaseConnector, DatabaseError


def _db(tmp_path, **config):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE employees (id TEXT PRIMARY KEY, name TEXT, dept TEXT)")
    conn.executemany(
        "INSERT INTO employees VALUES (?, ?, ?)",
        [("E-1", "Rhea", "Engineering"), ("E-2", "Charles", "Marketing")],
    )
    conn.commit()
    conn.close()
    return DatabaseConnector({"path": str(path), **config})


def _tool(connector, name):
    return {t.name: t for t in connector.tools}[name]


# --- reading ---


def test_sqlite_needs_no_driver_and_reads_rows(tmp_path):
    rows = _tool(_db(tmp_path), "query").invoke({"sql": "SELECT * FROM employees"})

    assert len(rows) == 2
    assert rows[0]["name"] == "Rhea"


def test_values_bind_as_parameters(tmp_path):
    rows = _tool(_db(tmp_path), "query").invoke(
        {"sql": "SELECT name FROM employees WHERE id = ?", "params": ["E-2"]}
    )

    assert rows == [{"name": "Charles"}]


def test_list_and_describe_tables(tmp_path):
    connector = _db(tmp_path)

    tables = _tool(connector, "list_tables").invoke({})
    columns = _tool(connector, "describe_table").invoke({"table": "employees"})

    assert {t["name"] for t in tables} == {"employees"}
    assert {c["name"] for c in columns} == {"id", "name", "dept"}


def test_row_results_are_capped(tmp_path):
    from roscoe.connectors.database import MAX_ROWS

    connector = _db(tmp_path)
    connector._conn.executemany(
        "INSERT INTO employees VALUES (?, ?, ?)",
        [(f"E-{i}", f"n{i}", "Eng") for i in range(10, 10 + MAX_ROWS + 20)],
    )
    connector._conn.commit()

    rows = _tool(connector, "query").invoke({"sql": "SELECT * FROM employees"})
    assert len(rows) == MAX_ROWS


# --- writes are opt-in ---


def test_writes_are_disabled_by_default(tmp_path):
    connector = _db(tmp_path)

    # The write tool isn't even offered, so a model cannot reach for it.
    assert "execute" not in {t.name for t in connector.tools}


def test_read_only_connector_refuses_a_write_through_query(tmp_path):
    with pytest.raises(DatabaseError, match="only runs read statements"):
        _tool(_db(tmp_path), "query").invoke({"sql": "DELETE FROM employees"})


def test_writes_work_when_explicitly_enabled(tmp_path):
    connector = _db(tmp_path, read_only=False)

    result = _tool(connector, "execute").invoke(
        {"sql": "INSERT INTO employees VALUES (?, ?, ?)", "params": ["E-3", "Max", "Finance"]}
    )
    rows = _tool(connector, "query").invoke({"sql": "SELECT * FROM employees"})

    assert result == {"rows_affected": 1}
    assert len(rows) == 3


def test_a_writable_connector_still_refuses_a_second_statement(tmp_path):
    connector = _db(tmp_path, read_only=False)

    with pytest.raises(DatabaseError, match="one statement"):
        _tool(connector, "execute").invoke(
            {"sql": "DELETE FROM employees WHERE id='E-1'; DROP TABLE employees"}
        )
    # The table survived.
    assert _tool(connector, "query").invoke({"sql": "SELECT * FROM employees"})


def test_a_trailing_semicolon_is_fine(tmp_path):
    rows = _tool(_db(tmp_path), "query").invoke({"sql": "SELECT * FROM employees;"})
    assert len(rows) == 2


def test_table_names_are_validated_since_they_cannot_be_bound(tmp_path):
    with pytest.raises(DatabaseError, match="not a valid table name"):
        _tool(_db(tmp_path), "describe_table").invoke({"table": "employees; DROP TABLE x"})


def test_empty_sql_is_refused(tmp_path):
    with pytest.raises(DatabaseError, match="Empty SQL"):
        _tool(_db(tmp_path), "query").invoke({"sql": "   "})


# --- configuration ---


def test_sqlite_without_a_path_says_what_is_missing():
    with pytest.raises(DatabaseError, match="needs 'path'"):
        DatabaseConnector({})


def test_memory_databases_work():
    connector = DatabaseConnector({"path": ":memory:"})
    assert _tool(connector, "list_tables").invoke({}) == []


def test_an_unavailable_driver_is_reported_clearly():
    with pytest.raises(DatabaseError, match="not installed"):
        DatabaseConnector({"driver": "not_a_real_driver", "dsn": "x://y"})


def test_a_custom_driver_is_called_with_the_dsn(monkeypatch):
    import sys
    import types

    calls = {}
    fake = types.ModuleType("fake_driver")
    fake.connect = lambda dsn, **kw: calls.setdefault("dsn", dsn) or sqlite3.connect(":memory:")
    monkeypatch.setitem(sys.modules, "fake_driver", fake)

    DatabaseConnector({"driver": "fake_driver", "dsn": "postgresql://x/y"})
    assert calls["dsn"] == "postgresql://x/y"


def test_a_custom_driver_without_connection_details_is_reported():
    import sys
    import types

    fake = types.ModuleType("bare_driver")
    fake.connect = lambda *a, **k: None
    sys.modules["bare_driver"] = fake
    try:
        with pytest.raises(DatabaseError, match="needs 'dsn' or 'params'"):
            DatabaseConnector({"driver": "bare_driver"})
    finally:
        del sys.modules["bare_driver"]


# --- schema bootstrapping ---


SCHEMA = """
CREATE TABLE IF NOT EXISTS people (id TEXT PRIMARY KEY, name TEXT);
INSERT OR IGNORE INTO people VALUES ('p1', 'Rhea');
"""


def _schema_file(tmp_path, body=SCHEMA):
    path = tmp_path / "schema.sql"
    path.write_text(body)
    return str(path)


def test_schema_creates_and_seeds_a_missing_database(tmp_path):
    db = tmp_path / "fresh.db"
    connector = DatabaseConnector({"path": str(db), "schema": _schema_file(tmp_path)})

    rows = _tool(connector, "query").invoke({"sql": "SELECT * FROM people"})
    assert rows == [{"id": "p1", "name": "Rhea"}]
    assert db.exists()


def test_schema_is_not_reapplied_to_an_existing_database(tmp_path):
    db = tmp_path / "fresh.db"
    schema = _schema_file(tmp_path)

    first = DatabaseConnector({"path": str(db), "schema": schema, "read_only": False})
    _tool(first, "execute").invoke({"sql": "DELETE FROM people WHERE id = ?", "params": ["p1"]})
    first.close()

    # Re-opening must not silently resurrect the deleted row.
    second = DatabaseConnector({"path": str(db), "schema": schema})
    assert _tool(second, "query").invoke({"sql": "SELECT * FROM people"}) == []


def test_memory_databases_apply_the_schema_every_time(tmp_path):
    connector = DatabaseConnector({"path": ":memory:", "schema": _schema_file(tmp_path)})
    assert _tool(connector, "query").invoke({"sql": "SELECT * FROM people"})


def test_a_missing_schema_file_is_reported(tmp_path):
    with pytest.raises(DatabaseError, match="schema file not found"):
        DatabaseConnector({"path": str(tmp_path / "x.db"), "schema": str(tmp_path / "nope.sql")})


def test_a_broken_schema_names_the_file(tmp_path):
    bad = _schema_file(tmp_path, "CREATE TABLE (((;")
    with pytest.raises(DatabaseError, match="Could not apply schema"):
        DatabaseConnector({"path": str(tmp_path / "x.db"), "schema": bad})


def test_schema_is_refused_for_non_sqlite_drivers(tmp_path):
    import sys
    import types

    fake = types.ModuleType("pg_like")
    fake.connect = lambda *a, **k: sqlite3.connect(":memory:")
    sys.modules["pg_like"] = fake
    try:
        with pytest.raises(DatabaseError, match="only applies to the built-in sqlite"):
            DatabaseConnector(
                {"driver": "pg_like", "dsn": "x://y", "schema": _schema_file(tmp_path)}
            )
    finally:
        del sys.modules["pg_like"]


# --- registry wiring ---


def test_registry_builds_a_database_connector_by_type(tmp_path):
    from roscoe.workflow.registry import build_connectors

    built = build_connectors({"appdb": {"type": "database", "path": ":memory:"}})
    assert "appdb" in built


def test_sqlite_is_usable_as_a_type_name_directly():
    from roscoe.workflow.registry import build_connectors

    built = build_connectors({"sqlite": {"path": ":memory:"}})
    assert "sqlite" in built
