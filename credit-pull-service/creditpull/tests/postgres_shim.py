"""
A thin shim that translates the Supabase Python client's chain API
(`sb.table(name).select().insert().update().eq().order().limit().single().execute()`)
into raw SQL queries against a local Postgres instance via psycopg2.

Just enough surface to make `creditpull.persistence`, `creditpull.scraper`,
and `creditpull.creditpull_api` work against `creditpull_test` for e2e
testing without needing a real Supabase / PostgREST setup.

Usage:
    import psycopg2
    from postgres_shim import PostgresShim
    conn = psycopg2.connect("dbname=creditpull_test")
    sb = PostgresShim(conn)
    # Now `sb` quacks like a supabase client for our calls.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor


class _Result:
    """Mimics supabase's APIResponse — has a `.data` attribute."""
    def __init__(self, data: Any):
        self.data = data


class _Query:
    """Builds and executes one query at a time. Mirrors supabase's chain API."""

    def __init__(self, conn, table_name: str):
        self.conn = conn
        self.table_name = table_name
        self._action: Optional[str] = None     # 'select' | 'insert' | 'update'
        self._select_cols: str = "*"
        self._payload: Any = None
        self._where: List[tuple] = []          # list of (col, op, value)
        self._order: List[tuple] = []          # list of (col, direction)
        self._limit: Optional[int] = None
        self._single: bool = False

    # ── Builder methods ────────────────────────────────────────────
    def select(self, cols: str = "*"):
        self._action = "select"
        self._select_cols = cols
        return self

    def insert(self, payload):
        self._action = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def eq(self, col: str, val: Any):
        self._where.append((col, "=", val))
        return self

    def is_(self, col: str, val):
        # Supabase: .is_("answered_at", "null")
        if val == "null" or val is None:
            self._where.append((col, "IS", None))
        else:
            self._where.append((col, "IS", val))
        return self

    def order(self, col: str, desc: bool = False):
        self._order.append((col, "DESC" if desc else "ASC"))
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    # ── Helpers ────────────────────────────────────────────────────
    def _build_where(self):
        if not self._where:
            return "", []
        clauses, vals = [], []
        for col, op, val in self._where:
            if op == "IS" and val is None:
                clauses.append(f'"{col}" IS NULL')
            else:
                clauses.append(f'"{col}" {op} %s')
                vals.append(val)
        return " WHERE " + " AND ".join(clauses), vals

    @staticmethod
    def _coerce(v):
        """Wrap dicts/lists as JSON for JSONB columns."""
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return v

    # ── Execute ────────────────────────────────────────────────────
    def execute(self) -> _Result:
        cur = self.conn.cursor(cursor_factory=RealDictCursor)
        try:
            if self._action == "insert":
                rows = self._payload if isinstance(self._payload, list) else [self._payload]
                if not rows:
                    return _Result([])
                cols = list(rows[0].keys())
                cols_sql = ", ".join(f'"{c}"' for c in cols)
                placeholders = ", ".join(["%s"] * len(cols))
                sql = f'INSERT INTO {self.table_name} ({cols_sql}) VALUES ({placeholders}) RETURNING *'
                results = []
                for row in rows:
                    values = [self._coerce(row.get(c)) for c in cols]
                    cur.execute(sql, values)
                    results.append(dict(cur.fetchone()))
                self.conn.commit()
                return _Result(results)

            if self._action == "update":
                set_clause = ", ".join(f'"{k}" = %s' for k in self._payload.keys())
                where_sql, where_vals = self._build_where()
                values = [self._coerce(v) for v in self._payload.values()] + where_vals
                sql = f'UPDATE {self.table_name} SET {set_clause}{where_sql} RETURNING *'
                cur.execute(sql, values)
                rows = [dict(r) for r in cur.fetchall()]
                self.conn.commit()
                return _Result(rows)

            # select (default)
            where_sql, where_vals = self._build_where()
            order_sql = ""
            if self._order:
                order_sql = " ORDER BY " + ", ".join(f'"{c}" {d}' for c, d in self._order)
            limit_sql = f" LIMIT {self._limit}" if self._limit else ""
            sql = f'SELECT {self._select_cols} FROM {self.table_name}{where_sql}{order_sql}{limit_sql}'
            cur.execute(sql, where_vals)
            rows = [dict(r) for r in cur.fetchall()]
            if self._single:
                if not rows:
                    raise RuntimeError(f"single() expected 1 row, got 0 (sql={sql!r})")
                return _Result(rows[0])
            return _Result(rows)
        finally:
            cur.close()


class PostgresShim:
    """Top-level client; supabase's `sb` quacks like this."""
    def __init__(self, conn):
        self.conn = conn

    def table(self, name: str) -> _Query:
        return _Query(self.conn, name)


def connect(dbname: str = "creditpull_test", host: str = "/tmp", **kw) -> PostgresShim:
    """Convenience factory — returns a ready-to-use shim."""
    conn = psycopg2.connect(dbname=dbname, host=host, **kw)
    return PostgresShim(conn)
