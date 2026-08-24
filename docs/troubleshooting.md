# Troubleshooting

This guide covers common production and local-development failures for Lemmy
Vote Viewer. Commands assume the production container is named
`lemmy-vote-viewer`; adjust names for other deployments.

Do not paste `.env` files, database URLs, cookies, authorization headers,
production dumps, or unredacted logs into public issues. Follow the
[security policy](../SECURITY.md) when a failure may expose sensitive data.

## Start with these checks

Confirm the Git branch, working tree, application version, and container state:

```sh
git branch --show-current
git status --short --branch
cat VERSION
docker compose ps
docker logs --tail=200 lemmy-vote-viewer
```

Confirm the version actually copied into the running container:

```sh
docker exec lemmy-vote-viewer cat /app/VERSION
```

If the repository and container versions differ, follow
[The UI shows an old version](#the-ui-shows-an-old-version).

Check which containers and networks exist without printing their environment:

```sh
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker network ls
docker inspect lemmy-vote-viewer \
  --format '{{json .NetworkSettings.Networks}}'
```

Avoid commands that dump the container environment because `DATABASE_URL`
contains the database password.

## HTTP status guide

| Status | Usual meaning |
| --- | --- |
| 400 | An input exceeded its limit or could not be parsed. |
| 401 | Lemmy authentication is required, but no valid active account was found. |
| 403 | The account is authenticated but does not satisfy the configured requirement. |
| 404 | The route, item, user-facing feature, instance, or community is unavailable. |
| 500 | An unexpected application, schema, grant, or database error occurred. |
| 503 | A database query timed out or the Lemmy authentication service was unavailable. |

The browser receives a generic error page. The application log contains the
useful distinction, but should still be checked for sensitive values before it
is shared.

## The application does not start

### `KeyError: 'DATABASE_URL'`

`DATABASE_URL` is required when `app.py` is imported. This error usually means
the application was started directly without its environment, or the production
`.env` file is missing.

For Docker Compose:

```sh
test -f .env && echo '.env exists'
docker compose config --services
docker compose up -d --build
```

Do not publish the output of `docker compose config`; it can contain the
expanded database URL and password.

For direct Python execution, supply a valid database URL in the process
environment. A placeholder is sufficient only for isolated unit tests that do
not access the database; use the documented test command instead of starting
the application manually.

### Compose says a required variable is unset

The local stack requires `.env.local`, including `POSTGRES_IMAGE`,
`POSTGRES_PASSWORD`, `VOTE_VIEWER_PASSWORD`, and `DATABASE_URL`:

```sh
cp .env.local.example .env.local
chmod 600 .env.local
```

Keep the password inside `DATABASE_URL` synchronized with
`VOTE_VIEWER_PASSWORD`. Percent-encode URL-special characters only in the URL,
as described in the README.

### The application rejects an environment value

Startup intentionally fails for some invalid values. Common examples include:

- `ENABLE_DOMAIN_SEARCH` must be exactly `true` or `false`;
- `AUTH_PROVIDER` must be `none` or `lemmy`;
- authentication requirements must be `none`, `login`, `allowlist`, or
  `admin`;
- protected routes require `AUTH_PROVIDER=lemmy`;
- Lemmy public and internal URLs must be HTTP(S) origins with a valid numeric
  port and no credentials, path, query, or fragment; and
- `TIMEZONE` must be a valid IANA timezone such as `Pacific/Auckland`.

Correct `.env`, then recreate the container. A restart does not reload changed
Compose environment values:

```sh
docker compose up -d --force-recreate
docker logs --tail=100 lemmy-vote-viewer
```

## The UI shows an old version

The footer version comes from `VERSION` at image-build time. It is not read from
the host after the container starts.

Compare the checkout with the running container:

```sh
git branch --show-current
git log -1 --oneline --decorate
cat VERSION
docker exec lemmy-vote-viewer cat /app/VERSION
```

The most common cause is building from the wrong branch or an old checkout.
Switch to the intended branch or release tag before rebuilding. For a
production deployment following `main`:

```sh
git switch main
git pull --ff-only origin main
docker compose up -d --build --force-recreate
```

If Docker still reuses an inappropriate cached layer, force one clean rebuild:

```sh
docker compose build --no-cache vote-viewer
docker compose up -d --force-recreate vote-viewer
```

Verify `/app/VERSION` again. Browser caching is unlikely because the application
sends no-cache headers, but a separate proxy cache must also be considered if
the container reports the correct version and the page does not.

## Routing and `APP_PREFIX`

### `/votes` or generated links return 404

For the documented path-based Caddy deployment, these settings must agree:

```text
APP_PREFIX=/votes
```

```caddy
redir /votes /votes/ 308

handle_path /votes/* {
    reverse_proxy lemmy-vote-viewer:8080
}
```

`handle_path` removes `/votes` before proxying the request. The application uses
`APP_PREFIX` when generating outward-facing links, while its internal Flask
routes remain rooted at `/`.

For direct local access at `http://127.0.0.1:8080/`, use `APP_PREFIX=/`. Recreate
the container after changing it.

### Post or comment paths do not open directly

The item search box accepts Lemmy paths such as `/post/123` and `/comment/456`
and ActivityPub URLs. After resolving an item, the viewer redirects to its own
route:

```text
/item/post/123
/item/comment/456
```

Navigating directly to `/post/123` on the viewer is not the same as submitting
that value through item search. With the production prefix, the viewer result
path is `/votes/item/post/123`.

### Static CSS is missing

Inspect the page source or browser network panel and confirm the stylesheet URL
contains the same prefix used by the page. A missing prefix usually indicates
an `APP_PREFIX` and proxy mismatch. A doubled prefix usually means the proxy did
not strip the path as expected.

## Instance or community overview controls are missing

Both kinds of overview use the same feature flag and authorization requirement.
Check:

```text
ENABLE_DOMAIN_SEARCH=true
AUTH_INSTANCE_REQUIRE=admin
```

The controls are shown only when:

1. `ENABLE_DOMAIN_SEARCH` is `true`; and
2. the current account satisfies `AUTH_INSTANCE_REQUIRE`.

A direct `/instance/...` or `/community/...` request returns 404 when the
feature is disabled and 401 or 403 when authentication or authorization fails.

After editing `.env`, rebuild or recreate the container. Confirm that you
changed the environment file used by the active Compose project, rather than
`.env.local` for production or `.env` for the isolated local stack.

## Authentication problems

### HTTP 401

HTTP 401 means no valid active Lemmy account was found. Check that:

- the user is logged into Lemmy in the same browser;
- the viewer is served from the same hostname as Lemmy;
- `AUTH_COOKIE_NAME` matches Lemmy's cookie, normally `jwt`;
- the browser sends the cookie on the viewer request; and
- the Lemmy account is not banned or deleted.

Do not copy the cookie value into logs, commands, or issue reports.

### HTTP 403

HTTP 403 means authentication succeeded but the account does not meet the
configured requirement:

- `login` accepts any authenticated active local user;
- `allowlist` accepts a Lemmy administrator or a username in
  `AUTH_ALLOWED_USERS`; and
- `admin` accepts only Lemmy administrators.

Allowlist matching is case-insensitive. `AUTH_ALLOWED_USERS` does not override
an `admin` requirement.

### HTTP 503 with authentication unavailable

Look for this log message:

```text
Lemmy authentication service unavailable for GET ...
```

Check that `LEMMY_INTERNAL_URL` points directly to the trusted Lemmy backend,
for example `http://lemmy:8536`, and that both containers share a network. Do
not point it at an untrusted host: the viewer sends the user's bearer token to
this URL.

From the proxy or another container on the same network, verify that the Lemmy
backend name and port are reachable. Do not include a real token in a diagnostic
request.

Authentication currently calls `/api/v3/site`. A Lemmy upgrade that removes
the v3 endpoint requires an application compatibility update.

### A changed role or ban is not recognized immediately

Authentication results are cached for `AUTH_CACHE_SECONDS`, which defaults to
60 seconds and is constrained to 0–300 seconds. Wait for the cache to expire or
recreate the viewer. Set the value to `0` to disable caching during diagnosis,
then restore an appropriate production value.

## Database connection failures

Common log messages include connection refused, name resolution failure,
password authentication failure, and database unavailable.

Check container and network state:

```sh
docker compose ps
docker network ls
docker inspect lemmy-vote-viewer \
  --format '{{json .NetworkSettings.Networks}}'
```

Confirm that the hostname in `DATABASE_URL` is the PostgreSQL service name
reachable on the shared Docker network, not `localhost`. Inside the viewer
container, `localhost` refers to the viewer container itself.

Check PostgreSQL readiness using the actual database container name:

```sh
docker exec lemmy-easy-deploy-postgres-1 \
  pg_isready -U lemmy -d lemmy
```

If a password contains `@`, `:`, `/`, `#`, or `%`, percent-encode it in
`DATABASE_URL` only. Use the original password when creating the PostgreSQL
role.

## Database permission or schema errors

Errors such as `permission denied for table`, `permission denied for column`,
or `column does not exist` normally mean the grants are stale or the Lemmy
schema is incompatible.

Reapply the grants after every viewer update:

```sh
docker exec -i lemmy-easy-deploy-postgres-1 \
  psql -U lemmy -d lemmy < db-grants.sql
```

The `vote_viewer` role must already exist. Run the read-only preflight in the
[database compatibility guide](database-compatibility.md) when a required table
or column is missing.

Do not solve a permission error by giving the viewer Lemmy's administrative
database credentials or broad table access.

## Database query timeouts and HTTP 503

Look for this application log message:

```text
Database query timed out for GET ...
```

The standard database connection uses a five-second statement timeout. Instance
and community overviews temporarily use `INSTANCE_QUERY_TIMEOUT_SECONDS`, which
is constrained to 5–12 seconds so it remains below the Gunicorn worker timeout.

For slow instance or community overviews:

- reduce `INSTANCE_VOTE_WINDOW_DAYS`;
- test a smaller instance or community to confirm the route works;
- run `ANALYZE` after restoring a copied database;
- inspect the query plan with `EXPLAIN (ANALYZE, BUFFERS, SETTINGS)` on a staging
  database; and
- compare cold, semi-cold, and warm timings before changing SQL or indexes.

Do not repeatedly run expensive `EXPLAIN ANALYZE` queries against a busy
production database. A copied production database is the preferred test target.

A single PostgreSQL query may use only one CPU core when the selected plan is
not parallel. Giving Docker more cores does not guarantee that one query will
use them. Diagnose the plan and rows processed rather than treating low total
CPU utilization as proof that Docker is limiting the container.

For slow user history or item-voter pages, reducing the overview vote window
will not help because that window applies only to instance and community
overview totals. Identify the exact route and SQL before changing settings.

## Generic HTTP 500 errors

The browser intentionally receives a generic message. Inspect the corresponding
application traceback:

```sh
docker logs --since=10m lemmy-vote-viewer
```

Common causes include:

- missing or stale database grants;
- a Lemmy schema change;
- an unexpected data value or type;
- a code/template mismatch after an incomplete update; or
- building one Git branch while expecting another.

Confirm the container version, reapply grants, and compare the deployed commit
with the intended release before changing code. Redact database values,
usernames, URLs, cookies, and infrastructure details before sharing a traceback.

## Empty, incomplete, or unexpected results

The viewer can show only data known to its local Lemmy database:

- a remote user must already be known locally;
- a remote post, comment, community, or vote must have federated locally;
- removed, deleted, and non-public communities are excluded from public
  histories;
- deleted users are excluded from searches and voter lists; and
- received totals use aggregate data and can differ from visible voter rows.

An instance does not receive every vote made anywhere in the federation. It
generally sees remote activity only when relevant content or communities are
federated to it. Results from two Lemmy instances can therefore differ without
either viewer being incorrect.

Instance and community overview totals cover only the configured recent window.
Ordinary user histories are not limited by that setting.

If an ActivityPub URL is not found, confirm that the exact post or comment is
known to the local Lemmy instance and belongs to a public, active community.

## Incorrect timestamps

`TIMEZONE` must be an IANA timezone name:

```text
TIMEZONE=Pacific/Auckland
```

An invalid name prevents startup. If the application starts but displays the
wrong zone, confirm that the active container was recreated after `.env`
changed. The date format itself is currently fixed; only the timezone is
configurable.

## Local test environment problems

### Port 8080 is already allocated

Change `VIEWER_PORT` in `.env.local`, then recreate the local viewer:

```text
VIEWER_PORT=8081
```

```sh
docker compose --env-file .env.local -f compose.local.yml \
  up -d --force-recreate vote-viewer
```

### A changed local database password does not work

PostgreSQL initialization variables apply only when the data volume is first
created. Editing `POSTGRES_PASSWORD` or `VOTE_VIEWER_PASSWORD` does not update
roles inside an existing database volume. Change the database role password
explicitly and keep `DATABASE_URL` synchronized, or intentionally recreate the
local database from a dump.

### Restore fails or the schema behaves unexpectedly

Match `POSTGRES_IMAGE` to the production PostgreSQL major version. Check the
dump format, available disk space, and `pg_restore` error before starting the
viewer. After a successful restore, apply `db-grants.sql` and run `ANALYZE` as
documented in the README.

To discard the isolated local database and start again, the following command
permanently deletes its named volume:

```sh
docker compose --env-file .env.local -f compose.local.yml down --volumes
```

Use it only for the disposable local stack after confirming the Compose project
name and backup path. Never adapt that command to a production Compose project.

## Collecting a safe diagnostic report

Record:

- viewer version and Git commit;
- Lemmy and PostgreSQL versions;
- the failing route without sensitive query values;
- HTTP status;
- whether the failure affects every user or only one account/item;
- whether it occurs cold, warm, or consistently;
- the relevant sanitized traceback or warning; and
- the checks already attempted.

Before sharing, remove:

- cookies, JWTs, and authorization headers;
- database URLs and passwords;
- `.env` contents;
- private hostnames and IP addresses;
- production data and database dumps; and
- usernames or search terms that are not necessary to reproduce the issue.

Use a private security report rather than ordinary support channels if the
failure may expose secrets, bypass authorization, or reveal excluded data.
