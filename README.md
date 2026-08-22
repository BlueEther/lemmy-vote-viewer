# Lemmy Vote Viewer

Lemmy Vote Viewer provides a read-only view of the votes currently stored by a
Lemmy instance. Search for local or known remote users, filter their post and
comment votes, and inspect the voters recorded for individual items.

## Screenshots

### Browse a user's vote history

![User vote history with type and vote filters](docs/images/vote-history.png)

### Inspect votes on a post or comment

![Voter list for an individual Lemmy comment](docs/images/item-voters.png)

## Features

- local users: `BlueEther`
- remote users: `Dave@lemmy.nz` and `@Dave@lemmy.nz`
- username and partial-instance suggestions after an unsuccessful search
- locally recorded upvote/downvote totals and per-item received-vote history
  for each searched user
- post and comment lookup by local path or ActivityPub URL
- optional instance-level summaries and sortable per-user totals for recently
  recorded local votes (30 days by default)
- public communities only in user histories and item voter lists
- removed/deleted post and comment text is redacted
- deleted users are excluded from voter lists/search
- title/comment links go to the local Lemmy copy
- globe links only appear for remote HTTP(S) originals
- voter names link to their vote history
- profile icons link to the local Lemmy profile
- server-side type/vote filters
- pagination (default 100, configurable 20–250)
- neutral-score handling
- strict CSP/no-cache/security headers
- no inline JavaScript
- read-only DB connection settings in the app
- non-root hardened Docker runtime
- application version and configured Lemmy instance shown in the footer
- configurable display timezone
- friendly error pages without internal error details

## Security note

This viewer exposes voting data publicly by default. Operators should decide
whether public access is appropriate for their deployment and configure access
controls if needed.

Similar voting data is already publicly available through services such as
[Lemvotes](https://lemvotes.org).

Received-vote totals come from the local Lemmy database's post and comment
aggregates. They do not include votes that were never federated to the local
instance.

Instance-level search can reveal aggregate behaviour that may not otherwise be
easy to discover. It is disabled by default. Enabling it should be an informed
deployment decision and does not replace authentication or proxy access
controls.

Instance-level totals aggregate recent, locally stored `post_like` and
`comment_like` records. The window defaults to 30 days and is configurable.
These totals may include votes associated with communities that are not
otherwise visible in this viewer. The linked per-user histories remain
restricted to public, active communities, so their counts may differ from the
instance overview.

## Roadmap

### High priority

- Add community filtering by `!community` or `!community@instance`.
- Preserve community, sorting, and other filters in shareable URLs.
- Allow sorting votes by newest or oldest.
- Add a configurable `LEMMY_BASE_URL`.
- Add automated tests.
- Document supported Lemmy versions and database compatibility.

### Possible enhancements

- Add optional authentication.
- Add configurable date formatting.
- Add date-range filtering.
- Add a health-check endpoint.

### Documentation and operations

- Document upgrading, rebuilding, and rerunning `db-grants.sql`.
- Provide example Caddy access-control and optional-authentication configurations.
- Add structured request and error logging.

## Environment

A full example is in `.env.example`.

```
DATABASE_URL=postgresql://vote_viewer:STRONG_PASSWORD@postgres:5432/lemmy
APP_PREFIX=/votes
PAGE_SIZE=100
ENABLE_DOMAIN_SEARCH=false
INSTANCE_QUERY_TIMEOUT_SECONDS=12
INSTANCE_VOTE_WINDOW_DAYS=30
LEMMY_NETWORK=lemmy-easy-deploy_default
LEMMY_BASE_URL=https://example.com
TIMEZONE=Pacific/Auckland
```

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

- Replace `CHANGE_ME` with the password used to create the `vote_viewer`
  PostgreSQL role in the database steps below.
- `LEMMY_NETWORK` is the existing Docker network shared by the proxy, PostgreSQL,
  and this application (*docker network ls* to find the network name).
- `PAGE_SIZE` can be set from 20 to 250 and defaults to 100.
- `ENABLE_DOMAIN_SEARCH` accepts `true` or `false`. It defaults to `false`.
  When disabled, the instance search is removed from the UI and direct
  `/instance/<domain>` requests return 404.
- `INSTANCE_QUERY_TIMEOUT_SECONDS` controls the heavier instance-overview query.
  It defaults to 12 seconds and is constrained to 5–12 seconds so it remains
  below the Gunicorn worker timeout.
- `INSTANCE_VOTE_WINDOW_DAYS` controls how many days of locally recorded votes
  are included in instance-level totals. It defaults to 30 and is constrained
  to 1–365 days. Larger windows make instance searches more expensive.
- `APP_PREFIX=/votes` is the URL path from which the pages will be served.
- `LEMMY_BASE_URL` is the public URL of the Lemmy instance, without a path.
  It is used to identify and link to the instance in the viewer UI.
- `TIMEZONE` controls the timezone used for displayed vote timestamps. Use an
  IANA timezone name such as `Pacific/Auckland`; it defaults to `UTC`.

### Passwords with URL-special characters

If the password contains URL-special characters, percent-encode them only in
`DATABASE_URL`. For example, encode `@` as `%40`, `:` as `%3A`, `/` as `%2F`,
`#` as `%23`, and `%` as `%25`.

Use the original, unencoded password in the PostgreSQL `CREATE ROLE` command.


Protect the real env file:

```bash
chmod 600 .env
```

## Build environment

Set up the database user:

```bash
docker exec -i lemmy-easy-deploy-postgres-1   psql -U lemmy -d lemmy
```

```SQL
CREATE ROLE vote_viewer
WITH LOGIN
PASSWORD 'STRONG_PASSWORD';
```
Exit `psql`, then run the grant SQL file:

```bash
docker exec -i lemmy-easy-deploy-postgres-1   psql -U lemmy -d lemmy < db-grants.sql
```

Re-run `db-grants.sql` after upgrading the viewer. New releases may require
read-only access to additional Lemmy columns or aggregate tables.


## Run Docker

No host port needs to be published because Caddy and this container share the Docker network:

```bash
docker rm -f lemmy-vote-viewer

docker compose up -d --build
```

or (if nothing has changed)

```bash
docker compose up -d
```

## Local database testing

The production Compose file expects an existing Lemmy Docker network and
database. For isolated development, `compose.local.yml` runs the viewer with a
local PostgreSQL container and publishes the viewer only on the loopback
interface.

First check the PostgreSQL major version on the production database:

```bash
docker exec lemmy-easy-deploy-postgres-1 \
  psql -U lemmy -d lemmy -Atc 'SHOW server_version;'
```

Create the local environment file and set `POSTGRES_IMAGE` to the matching
major version. Replace both example passwords with local-only passwords, and
keep `DATABASE_URL` in sync with `VOTE_VIEWER_PASSWORD`:

```bash
cp .env.local.example .env.local
chmod 600 .env.local
```

If the viewer password contains URL-special characters, percent-encode it only
in `DATABASE_URL`, as described in the main environment section above.

Create a custom-format logical dump on the production host. Treat this file as
production-sensitive data and transfer it to the development machine using a
secure method:

```bash
docker exec lemmy-easy-deploy-postgres-1 \
  pg_dump -U lemmy -d lemmy -Fc --no-owner --no-acl \
  > lemmy-local-test.dump
```

Start only the local database:

```bash
docker compose --env-file .env.local -f compose.local.yml up -d postgres
```

Restore the dump into the empty local database:

```bash
docker compose --env-file .env.local -f compose.local.yml exec -T postgres \
  pg_restore -U lemmy -d lemmy --no-owner --no-acl --exit-on-error \
  < /path/to/lemmy-local-test.dump
```

Apply the viewer's restricted grants and regenerate query-planner statistics:

```bash
docker compose --env-file .env.local -f compose.local.yml exec -T postgres \
  psql -U lemmy -d lemmy < db-grants.sql

docker compose --env-file .env.local -f compose.local.yml exec -T postgres \
  psql -U lemmy -d lemmy -c 'ANALYZE;'
```

Build and start the viewer:

```bash
docker compose --env-file .env.local -f compose.local.yml up -d --build
```

Open <http://127.0.0.1:8080/>. The local PostgreSQL port is not published to
the host or local network.

To stop the local stack while retaining the restored database:

```bash
docker compose --env-file .env.local -f compose.local.yml down
```

The database remains in a named Docker volume. Adding `--volumes` to the `down`
command permanently deletes that local database and requires a fresh restore.
The local services do not restart automatically when Docker Desktop starts.

## Caddy

For the existing path-based deployment, place the following in the Lemmy site
block after any bot or ASN blocking rules you may have configured. Place it
immediately before `reverse_proxy http://lemmy-ui:1234`:

```caddy

	##########  Bot block ends  ##########
	######################################
	
	redir /votes /votes/ 308
	
	handle_path /votes/* {
		reverse_proxy lemmy-vote-viewer:8080
	}
	
	reverse_proxy http://lemmy-ui:1234
```

With that configuration, keep the application prefix in `.env`:

```text
APP_PREFIX=/votes
```

## Checks

```bash
docker logs lemmy-vote-viewer
```

Verify it is non-root:

```bash
docker exec lemmy-vote-viewer id
```

Expected:

```text
uid=10001(voteviewer) gid=10001(voteviewer)
```

Verify the root filesystem is read-only:

```bash
docker exec lemmy-vote-viewer touch /app/test
```

That should fail with `Read-only file system`.

Verify Caddy can reach it:

```bash
docker exec lemmy-easy-deploy-proxy-1 \
  wget -qO- http://lemmy-vote-viewer:8080/ | head
```

## Data semantics

This viewer shows the current vote state stored by this Lemmy instance. 
It is not a permanent audit log of removed or changed votes. 
Remote-user data is limited to what has already federated to and is stored by this instance.
Instance-level totals are limited to the configured recent-vote window, which
defaults to 30 days.


## LLM declaration

ChatGPT 5.6 Sol was used for the framework and SQL, followed by manual work
with LLM support. An LLM was used to create the HTML templates, which were then
manually edited to tidy them and add elements.

Security was then checked with Codex and GitHub Copilot.

## License

Copyright (C) 2026 BlueEther@no.lastname.nz
-- SPDX-License-Identifier: AGPL-3.0-or-later

This project is free software licensed under the GNU Affero General Public
License, version 3 or (at your option) any later version. See [LICENSE](LICENSE)
for the complete licence terms.
