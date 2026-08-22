#!/bin/sh
# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

set -eu

psql \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 \
    --set=viewer_password="$VOTE_VIEWER_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE vote_viewer WITH LOGIN PASSWORD %L',
    :'viewer_password'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = 'vote_viewer'
) \gexec
SQL

