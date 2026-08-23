# Documentation todo

## Priority

### Release process

- [x] Create `docs/releasing.md` covering:
  - the project's patch, minor, and major versioning policy;
  - updating `VERSION`;
  - the branch, commit, push, pull request, and merge workflow;
  - when release tags should be created;
  - creating GitHub releases with correctly formatted release notes; and
  - synchronizing a local checkout after a pull request is merged.

### Database compatibility

- [x] Create `docs/database-compatibility.md` covering:
  - tested Lemmy and PostgreSQL versions;
  - the database tables and columns required by the viewer;
  - a compatibility-check procedure;
  - the likely effects of Lemmy schema changes; and
  - how to test a new Lemmy release safely.

### Security policy

- [x] Create `SECURITY.md` covering:
  - private vulnerability reporting;
  - supported versions;
  - the voting-data exposure warning;
  - authentication limitations; and
  - handling logs, database credentials, and production database dumps.

## Operations

### Troubleshooting

- [x] Create `docs/troubleshooting.md` covering:
  - the UI displaying an old version after rebuilding;
  - PostgreSQL statement timeouts;
  - authentication-related HTTP 401, 403, and 503 responses;
  - an incorrect `APP_PREFIX`;
  - Lemmy cookie and internal URL problems;
  - Docker network failures; and
  - database role or permission failures.

### Caddy access control

- [ ] Create `docs/caddy-access-control.md` with examples for:
  - basic authentication;
  - IP allowlists;
  - protecting only instance and community overviews;
  - rate limiting; and
  - appropriate forwarded headers.

## Contributing

- [x] Create `CONTRIBUTING.md` covering:
  - development setup;
  - branch naming;
  - running tests;
  - Python and SQL expectations;
  - pull request requirements; and
  - avoiding commits containing `.env` files or database dumps.

## Documentation links

- [x] Link [`docs/unit-tests.md`](docs/unit-tests.md) from `README.md`.
