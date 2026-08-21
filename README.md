# Lemmy Vote Viewer v0.2

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
- public communities only
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

## Security note

This viewer exposes voting data publicly by default. Operators should decide
whether public access is appropriate for their deployment and configure access
controls if needed.

Similar voting data is already publicly available through services such as
[Lemvotes](https://lemvotes.org).

## Roadmap

### High priority

- Search for posts and comments by local path or ActivityPub URL
  (`/post/123`, `/comment/456`, a complete local URL, or a federated URL).
- Add autocomplete or suggestions for `username@instance` searches.
- Add community filtering by `!community` or `!community@instance`.
- Preserve community, sorting, and other filters in shareable URLs.
- Allow sorting votes by newest or oldest.
- Add a configurable `LEMMY_BASE_URL`.
- Add automated tests.
- Document supported Lemmy versions and database compatibility.

### Possible enhancements

- Add optional authentication.
- Add configurable timezone and date formatting.
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
LEMMY_NETWORK=lemmy-easy-deploy_default
LEMMY_BASE_URL=https://example.com
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
- `APP_PREFIX=/votes` is the URL path from which the pages will be served.
- `LEMMY_BASE_URL` is the public URL of the Lemmy instance, without a path.
  It is used to identify and link to the instance in the viewer UI.

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
