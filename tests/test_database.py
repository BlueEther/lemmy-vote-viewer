# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest
from unittest.mock import patch

from psycopg.rows import dict_row

from vote_viewer import database


class DatabaseTests(unittest.TestCase):
    def test_connection_preserves_read_only_timeouts_and_row_factory(self):
        connection = object()
        with patch.object(
            database.psycopg,
            "connect",
            return_value=connection,
        ) as mocked_connect:
            result = database.connect_database(
                "postgresql://viewer@example/lemmy"
            )

        self.assertIs(result, connection)
        mocked_connect.assert_called_once_with(
            "postgresql://viewer@example/lemmy",
            row_factory=dict_row,
            connect_timeout=5,
            options=(
                "-c default_transaction_read_only=on "
                "-c statement_timeout=5000 "
                "-c idle_in_transaction_session_timeout=10000"
            ),
        )


if __name__ == "__main__":
    unittest.main()
