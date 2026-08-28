# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import sqlite3
import time
from contextlib import contextmanager


class GraphCache:
    """Small cross-worker cache and lease for expensive graph fragments."""

    MAX_ENTRIES = 512

    def __init__(self, path, ttl_seconds, lease_seconds):
        self.path = str(path)
        self.ttl_seconds = ttl_seconds
        self.lease_seconds = lease_seconds
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=1)
        connection.execute("PRAGMA busy_timeout = 1000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        for attempt in range(5):
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS graph_cache (
                            cache_key TEXT PRIMARY KEY,
                            payload TEXT NOT NULL,
                            expires_at REAL NOT NULL,
                            created_at REAL NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS graph_cache_state (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            lease_key TEXT,
                            lease_until REAL NOT NULL DEFAULT 0
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO graph_cache_state (
                            id, lease_key, lease_until
                        ) VALUES (1, NULL, 0)
                        """
                    )
                return
            except sqlite3.OperationalError:
                if attempt == 4:
                    raise
                time.sleep(0.1)

    def claim(self, cache_key):
        """Return (status, payload); status is hit, claimed, or busy."""
        now = time.time()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cached = connection.execute(
                    """
                    SELECT payload
                    FROM graph_cache
                    WHERE cache_key = ? AND expires_at > ?
                    """,
                    (cache_key, now),
                ).fetchone()
                if cached:
                    return "hit", cached[0]

                lease = connection.execute(
                    """
                    SELECT lease_key, lease_until
                    FROM graph_cache_state
                    WHERE id = 1
                    """
                ).fetchone()
                if lease and lease[1] > now:
                    return "busy", None

                connection.execute(
                    """
                    UPDATE graph_cache_state
                    SET lease_key = ?, lease_until = ?
                    WHERE id = 1
                    """,
                    (cache_key, now + self.lease_seconds),
                )
                return "claimed", None
        except sqlite3.Error:
            # Cache failure must not make the graph endpoint unavailable.
            return "claimed", None

    def store(self, cache_key, payload):
        now = time.time()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if self.ttl_seconds > 0:
                    connection.execute(
                        """
                        INSERT INTO graph_cache (
                            cache_key, payload, expires_at, created_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            payload = excluded.payload,
                            expires_at = excluded.expires_at,
                            created_at = excluded.created_at
                        """,
                        (
                            cache_key,
                            payload,
                            now + self.ttl_seconds,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        DELETE FROM graph_cache
                        WHERE cache_key IN (
                            SELECT cache_key
                            FROM graph_cache
                            ORDER BY created_at DESC
                            LIMIT -1 OFFSET ?
                        )
                        """,
                        (self.MAX_ENTRIES,),
                    )
                self._release(connection, cache_key)
        except sqlite3.Error:
            pass

    def release(self, cache_key):
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._release(connection, cache_key)
        except sqlite3.Error:
            pass

    def _release(self, connection, cache_key):
        connection.execute(
            """
            UPDATE graph_cache_state
            SET lease_key = NULL, lease_until = 0
            WHERE id = 1 AND lease_key = ?
            """,
            (cache_key,),
        )

    def clear(self):
        with self._connect() as connection:
            connection.execute("DELETE FROM graph_cache")
            connection.execute(
                """
                UPDATE graph_cache_state
                SET lease_key = NULL, lease_until = 0
                WHERE id = 1
                """
            )
