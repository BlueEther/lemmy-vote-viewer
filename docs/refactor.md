# Refactor `app.py` into modules

At 2,453 lines, splitting `app.py` into modules is now worthwhile. The issue is
not only its length: the file currently combines configuration, authentication,
database connections, 17 SQL blocks, URL parsing, result enrichment, and five
routes. The `index()` route alone contains several distinct search and view
paths.

This must be a behavior-preserving refactor. New features and query redesigns
should be handled separately so regressions can be attributed to a specific
change.

## Target structure

Use a small package with cohesive modules:

```text
app.py                       # Gunicorn compatibility entry point
vote_viewer/
    __init__.py              # Flask application factory
    config.py                # Environment parsing and validation
    auth.py                  # Lemmy authentication and authorization
    database.py              # PostgreSQL connections and timeouts
    links.py                 # Handles, paths, URLs, and parsing
    queries.py               # SQL constants and controlled sort expressions
    services.py              # Resolution and row-enrichment operations
    routes/
        __init__.py          # Blueprint registration
        search.py            # User, item, instance, and community search entry
        overviews.py         # Instance and community overview routes
        items.py             # Post and comment voter routes
```

Keep the root entry point compatible with the existing Gunicorn command:

```python
from vote_viewer import create_app

app = create_app()
```

Start with one `queries.py` containing the pure SQL constants. Split it into a
`queries/` package only if it remains difficult to navigate after the rest of
the application has been extracted. Avoid creating a separate module for every
small helper or query.

## Dependency direction

Keep imports flowing in one direction to prevent circular dependencies:

```text
config
  ↓
database, links, queries
  ↓
auth, services
  ↓
routes
  ↓
application factory
```

Pure modules such as `links.py` and `queries.py` must not import the Flask
application or route modules. Route modules may depend on services, but service
modules must not call route functions.

## Behavior-preservation requirements

The refactor is complete only when all of these remain true:

- Gunicorn continues to load the application through `app:app`.
- Existing public routes and `APP_PREFIX` behavior remain unchanged.
- Environment defaults, validation, bounds, and startup failures remain
  unchanged.
- SQL text, parameter order, sort expressions, pagination, and statement
  timeouts remain unchanged during extraction.
- Authentication and authorization are enforced before database access where
  they are today.
- Error status codes and generic browser error pages remain unchanged.
- Security headers remain present on successful and error responses.
- Template context, local and remote links, timestamps, and footer values remain
  unchanged.
- The complete unit suite passes after every extraction step.
- The Docker image starts and representative pages work against copied Lemmy
  data.

Do not use this refactor to change query plans, replace pagination, add
configuration, alter layout, or fix unrelated behavior. Record discovered bugs
separately unless they prevent the extraction from proceeding.

## Characterization tests before extraction

Add request-level tests around `index()` before moving production code. These
tests should record current behavior at stable boundaries such as HTTP status,
redirect location, selected query family, database parameters, and rendered
navigation state.

Cover at least:

- user cast, received, and community-summary views;
- content-type, score, and community filters;
- selection of filtered and cheaper unfiltered SQL variants;
- local post and comment path resolution;
- ActivityPub post and comment URL resolution;
- instance and community search feature flags;
- instance and community search authorization;
- preservation of user, view, filters, sort, and community across pagination;
- invalid and out-of-range page handling;
- received-view date, top, and bottom sorting; and
- database query cancellation returning HTTP 503.

Prefer tests of observable behavior over assertions about the current module
location of a function. Mock the database and Lemmy HTTP boundary for isolated
tests. Database-backed integration tests remain a separate requirement.

## Incremental implementation

### 1. Add the characterization tests

Create the request-level safety net and run the existing 26 unit tests. Commit
the tests before moving implementation code.

### 2. Create the package and compatibility entry point

Add `vote_viewer/` and its `__init__.py`. Keep `app.py` working throughout the
refactor; do not change the production Gunicorn command.

At this stage, the application factory may temporarily import existing pieces
until later steps move them. Avoid duplicating initialization in both locations.

### 3. Extract SQL and sort definitions

Move SQL constants, templates, and controlled sort-expression mappings to
`queries.py`. Do not edit the SQL while moving it. Verify exact parameters and
all query-selection tests.

### 4. Extract pure links and parsing helpers

Move handle construction, URL validation, local and remote link generation,
item parsing, instance normalization, community parsing, and pagination helpers
to `links.py`.

Pass required configuration into pure helpers rather than importing the Flask
application. Preserve URL encoding and `APP_PREFIX` behavior exactly.

### 5. Extract configuration

Move environment loading, validation, defaults, and bounds to `config.py`.
Initially preserve existing values and failure behavior. Then expose them to the
application factory in one consistent configuration object.

Add tests for important defaults, invalid values, and constrained numeric
ranges before changing how configuration is represented.

### 6. Extract database access

Move connection creation and read-only timeout options to `database.py`.
Preserve the five-second default statement timeout and the explicit overview
timeout behavior.

Do not introduce pooling, retries, or transaction changes as part of this step.

### 7. Extract authentication

Move Lemmy token validation, the bounded authentication cache, access checks,
and decorators to `auth.py`.

Preserve bearer-token handling, redirect refusal, response-size limits, cache
duration, banned/deleted-user behavior, and 401/403/503 distinctions. Avoid
module-level imports that point back to route modules or the root `app.py`.

### 8. Extract services and enrichment

Move user, community, item, and instance resolution plus row-enrichment
operations to `services.py`. Services may use database cursors, queries, and
link helpers but must not render templates or return Flask responses.

### 9. Introduce Blueprints and divide routes

Move the search route to `routes/search.py`, instance and community overviews to
`routes/overviews.py`, and post/comment voter pages to `routes/items.py`.

Register the Blueprints in the application factory. Preserve route paths,
endpoint behavior, decorators, templates, query parameters, and redirects.

### 10. Remove temporary compatibility imports

Update tests to import pure helpers from their owning modules. The root `app.py`
should export only the Flask application unless another compatibility export is
deliberately retained and documented.

Check for circular imports and remove temporary re-exports created during the
transition.

## Verification after every step

Run at minimum:

```sh
git diff --check
python3 -m py_compile app.py vote_viewer/*.py vote_viewer/routes/*.py
python3 -m unittest discover -s tests -v
```

When local Python dependencies are unavailable, use the Docker-based runner in
[`unit-tests.md`](unit-tests.md).

After route, configuration, database, or authentication steps, also rebuild the
container and test against the isolated local database:

```sh
docker compose --env-file .env.local -f compose.local.yml \
  up -d --build --force-recreate vote-viewer

docker compose --env-file .env.local -f compose.local.yml \
  logs --tail=100 vote-viewer
```

Manually verify:

- the home page with public access;
- authenticated normal-user and administrator behavior;
- cast, received, and community-summary user views;
- content, direction, and community filters;
- local-path and ActivityPub item lookup;
- post and comment voter pages;
- instance and community overviews;
- date/top/bottom and overview sort controls;
- previous and next pagination links;
- local and remote entity links;
- `/` in local development and `/votes` behind Caddy; and
- representative desktop and narrow/mobile layouts.

## Commit strategy

Use one behavior-preserving commit for each completed extraction step. Each
commit must compile and pass the unit suite so it can be reviewed or reverted
independently.

Suggested commit progression:

1. Add `index()` characterization tests.
2. Add the package and compatibility entry point.
3. Extract queries and sort definitions.
4. Extract link and parsing helpers.
5. Extract configuration.
6. Extract database access.
7. Extract authentication.
8. Extract services and enrichment.
9. Extract and register route Blueprints.
10. Remove temporary imports and update documentation.

Because this refactor should not change user-visible behavior, it does not need
a feature-version bump by itself. If maintainers release it independently, use
the versioning guidance in [`releasing.md`](releasing.md).

## Completion criteria

- [x] Characterization tests cover the major `index()` branches.
- [ ] Production still starts through `app:app`.
- [ ] Imports follow the documented dependency direction.
- [ ] No circular imports or unnecessary compatibility re-exports remain.
- [ ] All unit tests pass.
- [ ] Local copied-database smoke tests pass.
- [ ] Authentication behavior is verified for anonymous, normal, allowlisted,
  and administrator accounts where configured.
- [ ] Root and prefixed deployments generate correct links.
- [ ] Representative desktop and mobile views are unchanged.
- [ ] SQL text, parameters, query plans, and timings show no unintended change.
- [ ] Contributor, unit-test, architecture, and release documentation references
  are updated where required.
