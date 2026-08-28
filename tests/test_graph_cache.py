# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import tempfile
import unittest
from pathlib import Path

from vote_viewer.graph_cache import GraphCache


class GraphCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cache = GraphCache(
            Path(self.temporary_directory.name) / "graphs.sqlite3",
            ttl_seconds=300,
            lease_seconds=15,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_cache_coalesces_misses_and_returns_stored_payload(self):
        self.assertEqual(self.cache.claim("first"), ("claimed", None))
        self.assertEqual(self.cache.claim("first"), ("busy", None))
        self.assertEqual(self.cache.claim("second"), ("busy", None))

        self.cache.store("first", "<section>graph</section>")

        self.assertEqual(
            self.cache.claim("first"),
            ("hit", "<section>graph</section>"),
        )
        self.assertEqual(self.cache.claim("second"), ("claimed", None))

    def test_release_allows_the_next_miss_to_run(self):
        self.assertEqual(self.cache.claim("first"), ("claimed", None))
        self.cache.release("first")
        self.assertEqual(self.cache.claim("second"), ("claimed", None))
