# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import psycopg
from psycopg.rows import dict_row


CONNECTION_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=5000 "
    "-c idle_in_transaction_session_timeout=10000"
)


def connect_database(database_url):
    return psycopg.connect(
        database_url,
        row_factory=dict_row,
        connect_timeout=5,
        options=CONNECTION_OPTIONS,
    )
