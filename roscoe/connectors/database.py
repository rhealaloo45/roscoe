"""Database connector — SQL tools over SQLite (built in) or any DB-API 2.0 driver.

SQLite needs nothing installed: it is in the standard library, so a workflow can read
and write a local database with no dependency and no service to run. Point it at any
other database by naming a driver you already have.

```yaml
connectors:
  # SQLite — the default, no driver required
  appdb:
    type: database
    path: ./app.db
    read_only: false        # writes are off unless you say so

  # Any DB-API 2.0 driver you have installed
  warehouse:
    type: database
    driver: psycopg2
    dsn: postgresql://user:pass@host/warehouse
```

**Writes are disabled by default.** A tool-calling model that can run arbitrary SQL
can also drop a table, so allowing writes is an explicit decision (``read_only:
false``) rather than something you get by forgetting to think about it. Gate the
write tool behind ``human_approval`` as well when it touches anything that matters.

Every tool takes ``params`` and binds them, so values chosen by a model never reach
the database as SQL text. Statements are executed one at a time, which stops a second
statement being smuggled in behind a semicolon.

A live connection can be injected (``connection=...``) for tests.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from langchain_core.tools import StructuredTool

#: Max rows any tool returns, so a wide table can't flood the context window.
MAX_ROWS = 100

#: Statements the read tool accepts.
_READ_PREFIXES = ("select", "with", "pragma", "explain")

#: Identifiers can't be bound as parameters, so they are checked instead.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseError(ValueError):
    """Raised when a database connector is misconfigured or a statement is refused."""


class DatabaseConnector:
    """Tools: query, list_tables, describe_table, and (opt-in) execute."""

    def __init__(self, config: dict[str, Any], *, connection: Any | None = None) -> None:
        self.config = config
        self.read_only = bool(config.get("read_only", True))
        self._conn = connection if connection is not None else self._connect()

    # --- connection ---

    def _connect(self) -> Any:
        driver = self.config.get("driver", "sqlite")
        if driver in ("sqlite", "sqlite3"):
            return self._connect_sqlite()
        return self._connect_custom(driver)

    def _connect_sqlite(self) -> Any:
        path = self.config.get("path") or self.config.get("database")
        if not path:
            raise DatabaseError(
                "database connector (sqlite) needs 'path' — the .db file to open, "
                "or ':memory:' for a throwaway database."
            )
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_custom(self, driver: str) -> Any:
        """Open a connection through any DB-API 2.0 module the user has installed."""
        import importlib

        try:
            module = importlib.import_module(driver)
        except ImportError as exc:
            raise DatabaseError(
                f"database connector needs the '{driver}' driver, which is not "
                f"installed. Install it, or use the built-in sqlite driver."
            ) from exc

        connect = getattr(module, "connect", None)
        if connect is None:
            raise DatabaseError(
                f"'{driver}' does not look like a DB-API 2.0 module (no connect())."
            )

        dsn = self.config.get("dsn") or self.config.get("url")
        params = self.config.get("params") or {}
        if not isinstance(params, dict):
            raise DatabaseError("database connector: 'params' must be a mapping.")
        if dsn:
            return connect(dsn, **params)
        if not params:
            raise DatabaseError(
                f"database connector ({driver}) needs 'dsn' or 'params' to connect."
            )
        return connect(**params)

    # --- execution ---

    def _rows(self, cursor: Any) -> list[dict[str, Any]]:
        if cursor.description is None:
            return []
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchmany(MAX_ROWS)]

    def _run(self, sql: str, params: Any = None) -> Any:
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params or ()))
        return cursor

    def _check_single_statement(self, sql: str) -> str:
        """Reject anything with a second statement hiding behind a semicolon."""
        stripped = sql.strip().rstrip(";").strip()
        if ";" in stripped:
            raise DatabaseError(
                "Only one statement may be run at a time; remove the ';' and split "
                "the work into separate calls."
            )
        if not stripped:
            raise DatabaseError("Empty SQL statement.")
        return stripped

    def _identifier(self, name: str) -> str:
        """Validate a table name — identifiers cannot be passed as bound parameters."""
        if not _SAFE_IDENTIFIER.match(name or ""):
            raise DatabaseError(
                f"'{name}' is not a valid table name (letters, digits and underscores only)."
            )
        return name

    # --- tools ---

    @property
    def tools(self) -> list[StructuredTool]:
        def query(sql: str, params: list | None = None) -> list[dict]:
            """Run a read-only SQL query (SELECT) and return up to 100 rows.

            Put values in `params` and use placeholders in the SQL rather than
            formatting them into the string — e.g. sql="SELECT * FROM users WHERE
            id = ?", params=["E-1042"].
            """
            statement = self._check_single_statement(sql)
            if not statement.lower().startswith(_READ_PREFIXES):
                raise DatabaseError(
                    "query() only runs read statements. Use execute() for writes."
                )
            return self._rows(self._run(statement, params))

        def execute(sql: str, params: list | None = None) -> dict:
            """Run a single INSERT, UPDATE, or DELETE. Returns the rows affected.

            Put values in `params` and use placeholders in the SQL rather than
            formatting them into the string.
            """
            if self.read_only:
                raise DatabaseError(
                    "This database is read-only. Set 'read_only: false' on the "
                    "connector in agent_config.yaml to allow writes."
                )
            statement = self._check_single_statement(sql)
            cursor = self._run(statement, params)
            self._conn.commit()
            return {"rows_affected": cursor.rowcount}

        def list_tables() -> list[dict]:
            """List the tables in this database."""
            if isinstance(self._conn, sqlite3.Connection):
                return self._rows(
                    self._run(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                )
            return self._rows(
                self._run(
                    "SELECT table_name AS name FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
                )
            )

        def describe_table(table: str) -> list[dict]:
            """Show the column names and types of a table."""
            name = self._identifier(table)
            if isinstance(self._conn, sqlite3.Connection):
                return self._rows(self._run(f"PRAGMA table_info({name})"))
            return self._rows(
                self._run(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = ?",
                    [name],
                )
            )

        built = [
            StructuredTool.from_function(query, description=query.__doc__),
            StructuredTool.from_function(list_tables, description=list_tables.__doc__),
            StructuredTool.from_function(describe_table, description=describe_table.__doc__),
        ]
        # Only expose the write tool when writes are actually allowed — a model can't
        # reach for a capability it was never handed.
        if not self.read_only:
            built.append(StructuredTool.from_function(execute, description=execute.__doc__))
        return built

    def close(self) -> None:
        self._conn.close()
