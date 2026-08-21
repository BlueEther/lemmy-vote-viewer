-- Copyright (C) 2026 BlueEther@no.lastname.nz
-- SPDX-License-Identifier: AGPL-3.0-or-later

\set ON_ERROR_STOP on

-- Run as an administrative PostgreSQL role after vote_viewer exists.


\echo ''
\echo '------------------------------------'
\echo 'Lemmy Vote Viewer - database grants'
\echo '------------------------------------'
\echo ''
\echo 'Checking for vote_viewer role'
\echo ''

-- Check that the role exists before continuing.
SELECT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = 'vote_viewer'
) AS vote_viewer_role_exists \gset

\if :vote_viewer_role_exists

\echo ''
\echo 'vote_viewer role exists, continueing'
\echo ''

\else

\echo 'ERROR: PostgreSQL role "vote_viewer" does not exist.'
\echo ''
\echo '"vote_viewer" role found.'
\echo ''
\echo 'This script assumes the PostgreSQL role "vote_viewer" already exists.'
\echo ''
\echo 'If it does not exist, create it first with:'
\echo ''
\echo '  CREATE ROLE vote_viewer WITH LOGIN PASSWORD ''YOUR_PASSWORD'';'
\echo ''
\echo 'Then run this script again.'
\echo ''

\quit 3

\endif



BEGIN;

-- ---------------------------------------------------------------------------
-- Basic database/schema access
-- ---------------------------------------------------------------------------

\echo ''
\echo 'Granting access to the Lemmy database...'

GRANT CONNECT ON DATABASE lemmy TO vote_viewer;
GRANT USAGE ON SCHEMA public TO vote_viewer;


-- ---------------------------------------------------------------------------
-- Reset old privileges
-- ---------------------------------------------------------------------------

\echo ''
\echo 'Removing previous table-level privileges...'

REVOKE ALL PRIVILEGES ON TABLE
    public.person,
    public.post,
    public.comment,
    public.community,
    public.post_like,
    public.comment_like
FROM vote_viewer;


\echo ''
\echo 'Removing previous column-level privileges...'

DO $$
DECLARE
    table_name text;
    column_list text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'person',
        'post',
        'comment',
        'community',
        'post_like',
        'comment_like'
    ]
    LOOP

        SELECT string_agg(
            quote_ident(a.attname),
            ', '
            ORDER BY a.attnum
        )
        INTO column_list
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c
            ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n
            ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = table_name
          AND a.attnum > 0
          AND NOT a.attisdropped;

        IF column_list IS NOT NULL THEN

            EXECUTE format(
                'REVOKE SELECT (%s) ON TABLE public.%I FROM vote_viewer',
                column_list,
                table_name
            );

            EXECUTE format(
                'REVOKE INSERT (%s) ON TABLE public.%I FROM vote_viewer',
                column_list,
                table_name
            );

            EXECUTE format(
                'REVOKE UPDATE (%s) ON TABLE public.%I FROM vote_viewer',
                column_list,
                table_name
            );

            EXECUTE format(
                'REVOKE REFERENCES (%s) ON TABLE public.%I FROM vote_viewer',
                column_list,
                table_name
            );

        END IF;
    END LOOP;
END
$$;


-- ---------------------------------------------------------------------------
-- Required read-only permissions
-- ---------------------------------------------------------------------------

\echo ''
\echo 'Granting SELECT access to required columns on [person]...'

GRANT SELECT (
    id,
    name,
    display_name,
    local,
    actor_id,
    instance_id,
    deleted
)
ON public.person
TO vote_viewer;


\echo ''
\echo 'Granting SELECT access to required columns on [post]...'

GRANT SELECT (
    id,
    name,
    creator_id,
    community_id,
    ap_id,
    local,
    deleted,
    removed
)
ON public.post
TO vote_viewer;


\echo ''
\echo 'Granting SELECT access to required columns on [comment]...'

GRANT SELECT (
    id,
    creator_id,
    post_id,
    content,
    ap_id,
    local,
    deleted,
    removed
)
ON public.comment
TO vote_viewer;


\echo ''
\echo 'Granting SELECT access to required columns on [community]...'

GRANT SELECT (
    id,
    name,
    title,
    local,
    actor_id,
    visibility,
    deleted,
    removed
)
ON public.community
TO vote_viewer;


\echo ''
\echo 'Granting SELECT access to required columns on [post_like]...'

GRANT SELECT (
    post_id,
    person_id,
    score,
    published
)
ON public.post_like
TO vote_viewer;


\echo ''
\echo 'Granting SELECT access to required columns on [comment_like]...'

GRANT SELECT (
    person_id,
    comment_id,
    post_id,
    score,
    published
)
ON public.comment_like
TO vote_viewer;


-- ---------------------------------------------------------------------------
-- Role safety settings
-- ---------------------------------------------------------------------------

\echo ''
\echo 'Applying read-only and timeout settings...'

ALTER ROLE vote_viewer
    SET default_transaction_read_only = on;

ALTER ROLE vote_viewer
    SET statement_timeout = '5s';

ALTER ROLE vote_viewer
    SET idle_in_transaction_session_timeout = '10s';


COMMIT;


\echo ''
\echo 'Database permissions configured successfully.'
\echo ''
