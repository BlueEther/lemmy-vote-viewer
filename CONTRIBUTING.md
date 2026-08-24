# Contributing

Thank you for contributing to Lemmy Vote Viewer. The project is a small Flask
application that reads a live Lemmy PostgreSQL schema, so apparently simple
changes can affect privacy boundaries, database load, authentication, and
compatibility. Please keep changes focused and test them in proportion to their
risk.

## Before starting

Use a public [GitHub issue][issues] for ordinary bug reports, feature proposals,
and questions. Search existing issues first and describe the user or operator
problem rather than only a proposed implementation.

Do not open a public issue for a suspected vulnerability. Follow
[`SECURITY.md`](SECURITY.md) instead.

For a substantial feature, schema change, new dependency, or deployment change,
discuss the approach before investing in a large implementation. Small fixes
and documentation corrections can normally proceed directly to a pull request.

## Repository layout

| Path | Purpose |
| --- | --- |
| `app.py` | Gunicorn compatibility entry point |
| `vote_viewer/application.py` | Flask application, routes, enrichment, and view context during the refactor |
| `vote_viewer/auth.py` | Lemmy authentication, bounded caching, and authorization |
| `vote_viewer/config.py` | Environment loading, defaults, validation, and bounds |
| `vote_viewer/database.py` | PostgreSQL connection creation and safety options |
| `vote_viewer/queries.py` | SQL constants, query templates, and controlled sort expressions |
| `vote_viewer/links.py` | Pure handle, URL, parsing, and pagination helpers |
| `templates/` | Jinja HTML templates and shared footer |
| `static/style.css` | Responsive presentation |
| `tests/test_app.py` | Isolated `unittest` suite |
| `db-grants.sql` | Restricted PostgreSQL privileges and role safety settings |
| `compose.yml` | Production viewer service |
| `compose.local.yml` | Isolated local viewer and PostgreSQL stack |
| `.env.example` | Production configuration example |
| `.env.local.example` | Local test configuration example |
| `docs/` | Detailed operator, compatibility, testing, and release guides |
| `VERSION` | Single application version source |

## Development requirements

The production image uses Python 3.13. Prefer Python 3.13 (although 3.14 was used in fo development) for host-based
development so behavior matches the container. Runtime dependencies are pinned
in `requirements.txt`.

Docker is recommended for application and database testing. The unit tests can
run without PostgreSQL, but application pages and SQL changes require a Lemmy
database schema.

Useful tools include:

- Git;
- Python 3.13 and `venv`, or Docker;
- Docker Compose for the isolated database stack;
- PostgreSQL client tools for schema and query investigation; and
- GitHub CLI (`gh`) for contributors who prefer command-line pull requests.

No formatter, linter, or contributor CI workflow is currently configured. Do
not claim those checks passed unless they were actually run.

## Branch workflow

Start from a clean, current `main`:

```sh
git switch main
git pull --ff-only origin main
git status --short --branch
```

Create a descriptive branch for one coherent change:

```sh
git switch -c DescriptiveBranchName
```

Before editing or committing, confirm the active branch and working tree:

```sh
git branch --show-current
git status --short --branch
```

Do not mix unrelated refactoring, documentation, features, and fixes in one
pull request. Preserve existing work in a dirty tree and never discard another
contributor's changes to make a branch appear clean.

## Local Python setup

Create an ignored virtual environment and install the pinned dependencies:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The application requires `DATABASE_URL` when imported. Use the unit-test runner
for isolated changes; it supplies safe test configuration before importing the
application.

## Running unit tests

With dependencies installed:

```sh
python3 -m py_compile app.py vote_viewer/*.py
python3 -m unittest discover -s tests -v
```

To run through a Docker image without installing dependencies on the host:

```sh
docker build -t lemmy-vote-viewer-dev .

docker run --rm \
  -e PYTHONPATH=/src \
  -v "$PWD:/src:ro" \
  -w /tmp \
  lemmy-vote-viewer-dev \
  python -m unittest discover -s /src/tests -v
```

See [`docs/unit-tests.md`](docs/unit-tests.md) for every currently documented
test. Add or update tests whenever behavior changes. Keep the documented count
and per-test descriptions synchronized with `tests/test_app.py`.

Tests should be deterministic and isolated. Mock Lemmy HTTP responses rather
than requiring network access. A unit test that does not need PostgreSQL should
not accidentally connect to it.

## Local database testing

Do not develop SQL against the production database when a restored copy can be
used. The README's [local database testing procedure][local-testing] documents
how to:

1. match the production PostgreSQL major version;
2. create `.env.local`;
3. restore a custom-format dump into the isolated Compose stack;
4. apply the restricted grants;
5. regenerate planner statistics; and
6. run the viewer on the loopback interface.

Production dumps contain user and federation data. Store and transfer them
securely, never commit them, and remove them when they are no longer required.
The `.gitignore` patterns are a convenience, not a security boundary.

Before testing a new Lemmy release or schema, follow
[`docs/database-compatibility.md`](docs/database-compatibility.md).

## Python expectations

- Follow the existing four-space style and favor readable, direct code.
- Keep configuration validation close to configuration loading.
- Validate and bound all request and environment inputs.
- Preserve generic browser error pages; send diagnostic detail only to logs.
- Enforce authentication and authorization before expensive work or database
  access when possible.
- Keep external URL handling restricted to validated HTTP or HTTPS URLs.
- Do not log cookies, bearer tokens, authorization headers, passwords, database
  URLs, or unredacted sensitive queries.
- Preserve the existing copyright and SPDX header style in source and
  configuration files where applicable.
- Avoid new dependencies unless they provide clear value that cannot reasonably
  be implemented with the standard library or existing dependencies.
- Pin intentional runtime dependencies in `requirements.txt` and explain their
  security and image-size impact in the pull request.

When changing authentication, add tests for anonymous users, normal users,
allowlisted users where relevant, administrators, disabled features, and direct
route access. Authentication failures must not fall through to database work.

## SQL expectations

The viewer runs against an active Lemmy database with strict statement
timeouts. Query correctness and cost are both review requirements.

- Parameterize data values with Psycopg placeholders.
- Interpolate SQL fragments only from fixed application-controlled allowlists,
  such as predefined sort expressions.
- Do not concatenate raw request or environment values into SQL.
- Avoid casting indexed columns in filters or joins when a correctly typed
  parameter can be used instead.
- Do not force planner behavior with `enable_*` settings as a substitute for a
  sound query shape.
- Filter and paginate before expensive enrichment joins where possible.
- Use deterministic ordering with a stable unique tie-breaker, normally an ID.
- Preserve the cheaper unfiltered query path when adding optional filters.
- Consider deep-page cost before adding or expanding `OFFSET` pagination.
- Keep the viewer read-only. Do not add application writes to the Lemmy schema.

For material query changes, capture
`EXPLAIN (ANALYZE, BUFFERS, SETTINGS)` results on a copied database. Test
representative small, medium, and large result sets and distinguish cold,
semi-cold, and warm timings. Do not repeatedly benchmark expensive queries on
a busy production database.

If a query needs a new table or column:

1. update `db-grants.sql` with the minimum required `SELECT` access;
2. update the required schema in `docs/database-compatibility.md`;
3. run the compatibility preflight;
4. add database-backed testing where available; and
5. document any supported-version change.

Do not add indexes or modify Lemmy's schema as an incidental viewer deployment
step. Propose and evaluate such changes separately, including their write and
maintenance cost to Lemmy.

## HTML and CSS expectations

- Preserve Jinja autoescaping; do not mark untrusted values safe.
- Keep the restrictive content security policy in mind. The current UI uses no
  inline or external JavaScript.
- Maintain the established link behavior: text stays within Vote Viewer where
  an internal view exists, the person or house icon opens the local Lemmy page,
  and the globe icon opens the original remote page.
- Use the shared footer rather than duplicating version, copyright, or instance
  markup.
- Check desktop and narrow/mobile layouts for every material template or CSS
  change.
- Keep controls keyboard-usable and give icon-only links meaningful titles or
  accessible labels.

Screenshots are useful for user-visible pull requests. Do not include real
private data or information that the configured viewer would normally hide.

## Configuration changes

When adding or changing an environment setting, update every applicable source:

- `.env.example`;
- `.env.local.example`;
- `compose.local.yml` defaults or required interpolation;
- the README environment section;
- authentication, security, compatibility, or troubleshooting documentation;
  and
- unit tests for parsing, bounds, defaults, and access behavior.

Choose conservative defaults for features that increase data exposure or query
cost. Invalid security-sensitive settings should fail clearly at startup rather
than silently weakening protection.

## Documentation changes

Keep the README focused on features, deployment, configuration, and essential
operations. Put detailed procedures and reference material in `docs/`, then
link them from the relevant README section.

When behavior changes, review:

- the Features and Security sections;
- environment examples;
- data-semantics wording;
- roadmap and `docs_todo.md` entries;
- screenshots; and
- unit-test documentation.

Commands in documentation must be safe to paste, identify destructive effects,
and use placeholders for deployment-specific names. Never include real
credentials or private infrastructure values.

## Versioning and releases

Do not bump `VERSION` for ordinary documentation-only contributions unless a
maintainer is preparing a release. Feature branches that are intended to become
a release should include the agreed version bump before merging.

Maintainers should follow [`docs/releasing.md`](docs/releasing.md). In
particular, release tags are annotated, use the `v` prefix, and are created only
after the pull request has merged into `main`.

## Commit and pull request expectations

Before committing:

```sh
git status --short --branch
git diff
git diff --check
python3 -m py_compile app.py vote_viewer/*.py
python3 -m unittest discover -s tests -v
```

Stage only intended files. Use a concise commit subject that describes the
outcome rather than the editing process.

A pull request should explain:

- the problem and resulting behavior;
- important implementation decisions;
- security, privacy, database, or compatibility implications;
- new configuration and operator actions;
- tests and manual checks performed;
- relevant query timings for SQL changes; and
- screenshots for material UI changes.

Call out checks that were not run and explain why. Do not describe a mocked unit
test as database integration testing or a warm query as a cold benchmark.

Use a Markdown body file with GitHub CLI to preserve formatting:

```sh
git push -u origin DescriptiveBranchName

gh pr create \
  --base main \
  --head DescriptiveBranchName \
  --title "Describe the outcome" \
  --body-file /path/to/pull-request.md
```

## Before requesting review

- [ ] The branch contains one coherent change.
- [ ] No `.env`, dump, backup, credential, token, or sensitive log is staged.
- [ ] `git diff --check` passes.
- [ ] `app.py` and the `vote_viewer` package compile.
- [ ] All unit tests pass, or skipped checks are disclosed.
- [ ] New behavior and regressions have tests.
- [ ] SQL changes were measured against realistic copied data.
- [ ] Authentication and direct-route authorization were considered.
- [ ] Desktop and mobile layouts were checked for UI changes.
- [ ] Environment examples and detailed documentation are synchronized.
- [ ] Schema access changes are reflected in `db-grants.sql` and compatibility
  documentation.
- [ ] The pull request explains operator actions and compatibility effects.

## Licence

Contributions are accepted under the repository's GNU Affero General Public
License, version 3 or later (`AGPL-3.0-or-later`). By submitting a contribution,
you agree that it may be distributed under that licence.

[issues]: https://github.com/BlueEther/lemmy-vote-viewer/issues
[local-testing]: README.md#local-database-testing-preproduction-testing
