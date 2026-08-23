# Lemmy Vote Viewer

Lemmy Vote Viewer provides a read-only view of the votes currently stored by a
Lemmy instance. Search local or known remote users, compare votes cast and
received, filter activity by community, inspect voters on individual posts and
comments, and review recent voting activity by instance or community.

## Screenshots

### Browse a user's vote history

![User vote history with type and vote filters](docs/images/vote-history.png)

### Inspect votes on a post or comment

![Voter list for an individual Lemmy comment](docs/images/item-voters.png)

### Review an instance's recent voters

![Instance vote overview with recent vote totals and sorting controls](docs/images/instance-overview.png)

### Review a community's recent voters

![Community vote overview with recent vote totals and sorting controls](docs/images/community-overview.png)

## Features

- Search vote histories for local and known remote users
- Find users through username and partial-instance suggestions
- View locally recorded votes cast and received
- Browse received votes for each post and comment
- Look up posts and comments by local path or ActivityPub URL
- Inspect the voters recorded for individual posts and comments
- Filter vote histories by content type and vote direction
- Filter cast and received histories by local or known remote community
- Compare votes cast and received across communities in a grouped summary
- Open a community overview showing recent voters and their vote totals
- Sort received-vote histories by date or score
- Review optional instance-level summaries and recent per-user vote totals
- Follow links to local profiles, content, vote histories, and remote originals
- Paginate large result sets with a configurable page size
- Restrict user histories and item voter lists to public, active communities
- Redact removed or deleted posts and comments
- Exclude deleted users from searches and voter lists
- Display timestamps in a configurable timezone

## Security and deployment

See the project's [security policy](SECURITY.md) for supported versions,
private vulnerability reporting, deployment boundaries, and operator guidance.

- Read-only PostgreSQL access
- Strict CSP, no-cache, and other security headers
- No inline JavaScript
- Hardened non-root Docker container with a read-only filesystem
- Application version and configured Lemmy instance shown in the footer
- Friendly error pages without internal details
- Optional authorization using the existing local Lemmy login

## Security note

This viewer exposes voting data publicly by default. It can reuse the local
Lemmy login to restrict ordinary searches, instance overviews, or both.
Operators should decide whether public access is appropriate for their
deployment and configure access controls if needed.

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

- Add oldest-first sorting to cast and received vote histories.
- Add database-backed integration tests for supported Lemmy releases.
- Improve deep-page performance by replacing large `OFFSET` queries with
  cursor-based pagination.

### Possible enhancements

- Add configurable date formatting.
- Add date-range filtering.
- Add a health-check endpoint.
- Add short-lived caching for expensive instance and community overviews.

### Documentation and operations

- Provide example Caddy access-control configurations.
- Add structured request and error logging with request duration, status,
  route, and timeout information.
- Add automated CI checks for tests and Python compilation.

## Deploying from Git

Maintainers preparing a new version should follow the
[release process](docs/releasing.md).

Clone the repository on the server:

```bash
git clone https://github.com/BlueEther/lemmy-vote-viewer.git
cd lemmy-vote-viewer
```

The default checkout follows `main`. For a reproducible production deployment,
select a release tag instead (replace `v0.8.2` with the version being deployed):

```bash
git fetch --tags
git switch --detach v0.8.2
```

To follow `main`:

```bash
git switch main
git pull --ff-only origin main
```

Create the deployment configuration and restrict access to it:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env`, then complete the [database setup](#build-environment),
[Docker deployment](#run-docker), and [Caddy configuration](#caddy) below.

### Updating an existing deployment

Read the release notes first. They identify configuration or database-access
changes that need operator attention.

For a deployment that follows `main`:

```bash
git switch main
git pull --ff-only origin main
```

For a tagged deployment:

```bash
git fetch --tags
git switch --detach v0.8.2
```

Reapply the read-only database grants after updating. This is safe to rerun and
ensures the viewer can access any newly required tables or columns:

```bash
docker exec -i lemmy-easy-deploy-postgres-1 \
  psql -U lemmy -d lemmy < db-grants.sql
```

Rebuild and recreate the viewer, then check its logs:

```bash
docker compose up -d --build --force-recreate
docker logs --tail=100 lemmy-vote-viewer
```

To roll back, fetch the tags, switch to the previous release tag, reapply that
version's `db-grants.sql`, and rebuild the container using the same commands.

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
LEMMY_INTERNAL_URL=http://lemmy:8536
AUTH_PROVIDER=none
AUTH_SEARCH_REQUIRE=none
AUTH_INSTANCE_REQUIRE=none
AUTH_ALLOWED_USERS=Dave,BlueEther
AUTH_COOKIE_NAME=jwt
AUTH_CACHE_SECONDS=60
AUTH_TIMEOUT_SECONDS=3
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
  When disabled, the instance search and community-overview links are removed
  from the UI, and direct `/instance/<domain>` and `/community/<handle>`
  requests return 404.
- `INSTANCE_QUERY_TIMEOUT_SECONDS` controls the heavier instance-overview query.
  It defaults to 12 seconds and is constrained to 5–12 seconds so it remains
  below the Gunicorn worker timeout.
- `INSTANCE_VOTE_WINDOW_DAYS` controls how many days of locally recorded votes
  are included in instance-level totals. It defaults to 30 and is constrained
  to 1–365 days. Larger windows make instance searches more expensive.
- `APP_PREFIX=/votes` is the URL path from which the pages will be served.
- `LEMMY_BASE_URL` is the public URL of the Lemmy instance, without a path.
  It is used to identify and link to the instance in the viewer UI.
- `LEMMY_INTERNAL_URL` is the URL the viewer uses to validate Lemmy sessions.
  Prefer the internal Lemmy backend URL, such as `http://lemmy:8536`, rather
  than routing authentication requests back through the public proxy.
- `TIMEZONE` controls the timezone used for displayed vote timestamps. Use an
  IANA timezone name such as `Pacific/Auckland`; it defaults to `UTC`.

### Lemmy authentication

The viewer can reuse the `jwt` cookie created when a user logs into Lemmy on
the same hostname. It validates that token server-side with Lemmy's v3 API; it
does not handle passwords or share Lemmy's JWT signing secret.

To require a Lemmy login for ordinary searches and an administrator account
for instance overviews, use:

```text
AUTH_PROVIDER=lemmy
AUTH_SEARCH_REQUIRE=login
AUTH_INSTANCE_REQUIRE=admin
AUTH_ALLOWED_USERS=Dave,BlueEther
LEMMY_INTERNAL_URL=http://lemmy:8536
```

Both requirement settings accept:

- `none` — public access.
- `login` — any authenticated, active local Lemmy user.
- `allowlist` — a Lemmy administrator or a username listed in
  `AUTH_ALLOWED_USERS`.
- `admin` — Lemmy administrators only.

`AUTH_ALLOWED_USERS` is a comma-separated list of local usernames. Comparison
is case-insensitive and surrounding whitespace is ignored. The list is only
used when a requirement is set to `allowlist`; it does not override `admin`.

`AUTH_COOKIE_NAME` defaults to Lemmy's `jwt` cookie. Successful and failed
token validations are cached for `AUTH_CACHE_SECONDS`, constrained to 0–300
seconds and defaulting to 60. `AUTH_TIMEOUT_SECONDS` controls the Lemmy API
request and is constrained to 1–10 seconds, defaulting to 3. Authentication
requests do not follow redirects, preventing the bearer token from being
forwarded if the internal URL is misconfigured.

Authentication currently targets Lemmy 0.19's `GET /api/v3/site` endpoint.
The browser and viewer must be served from the same hostname for Lemmy's
host-scoped login cookie to be sent to the viewer. Caddy forwards cookies with
the existing configuration below. Tokens and cookie headers must never be
included in application or proxy logs.

When `ENABLE_DOMAIN_SEARCH=true`, the instance-search UI and community
overview links are shown only to a user who satisfies `AUTH_INSTANCE_REQUIRE`.
Direct instance and community-overview URLs enforce the same rule server-side.

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

Review the [database compatibility guide](docs/database-compatibility.md)
before connecting the viewer to a new Lemmy release. The guide lists the
verified versions, required schema, preflight query, and upgrade-testing
procedure.

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

The [upgrade procedure](#updating-an-existing-deployment) explains when and how
to reapply these grants for an existing deployment.


## Run Docker

No host port needs to be published because Caddy and this container share the Docker network:

```bash
docker compose up -d --build --force-recreate
```

or (if nothing has changed)

```bash
docker compose up -d
```

To stop and remove the viewer container:

```bash
docker compose down
```

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

For startup, routing, authentication, database, timeout, and local-test
problems, see the [troubleshooting guide](docs/troubleshooting.md).
The isolated application test suite and its Docker-based runner are documented
in the [unit-test guide](docs/unit-tests.md).

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

## Local database testing (preproduction testing)

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

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup, test and SQL
expectations, documentation requirements, and the pull request checklist.

## LLM declaration

ChatGPT 5.6 Sol was used for the initial framework and SQL, followed by manual
review and further work with LLM support. 
An LLM was used to create the HTML templates, which werethen manually edited 
to refine the layout and add elements. Codex was then used during further development for the templates etc

Codex was used to refactor the SQL for significant performance improvements and
to extend instance, community, and user search functionality.

ChatGPT was used to write documentation - with full review.

Security was then checked with Codex and GitHub Copilot.

## License

Copyright (C) 2026 BlueEther@no.lastname.nz
SPDX-License-Identifier: AGPL-3.0-or-later

This project is free software licensed under the GNU Affero General Public
License, version 3 or (at your option) any later version. See [LICENSE](LICENSE)
for the complete licence terms.
