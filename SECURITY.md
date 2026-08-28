# Security policy

Lemmy Vote Viewer exposes voting information from a Lemmy PostgreSQL database.
Its security depends on the viewer, Lemmy, PostgreSQL, the reverse proxy, and
the operator's configuration. Please report vulnerabilities privately and give
the maintainer a reasonable opportunity to investigate before public
disclosure.

## Supported versions

| Version | Security support |
| --- | --- |
| Latest published GitHub release | Supported |
| Earlier releases | Not normally supported; upgrade before reporting |
| Unreleased `main` branch | Development only; not recommended for production |

Security fixes are normally released only for the latest version. The current
release is listed on the project's [GitHub releases page][releases]. Operators
should deploy a release tag rather than an arbitrary development commit.

## Reporting a vulnerability

Do not open a public GitHub issue, discussion, or pull request for a suspected
vulnerability.

Email the maintainer at 
[blueether@no.lastname.nz](mailto:blueether@no.lastname.nz) with the subject:

```text
Lemmy Vote Viewer security report
```

GitHub private vulnerability reporting is not currently enabled for this
repository. If a **Report a vulnerability** button becomes available on the
repository's Security page, it may be used instead of email.

Include as much of the following as possible:

- affected viewer version, tag, or commit;
- Lemmy and PostgreSQL versions;
- deployment topology and relevant configuration, with secrets removed;
- vulnerability type and expected impact;
- attack prerequisites and whether authentication is required;
- minimal reproduction steps or a proof of concept;
- whether the issue has been exploited or disclosed elsewhere;
- suggested remediation, if known; and
- a safe way to contact you about the report.

Do not send JWTs, cookies, passwords, `.env` files, private keys, production
database dumps, or unredacted logs. If sensitive evidence is essential, first
describe it and arrange an appropriate transfer method with the maintainer.

There is no guaranteed response-time service level. Reports will be handled on
a best-effort basis. Please avoid public disclosure until the issue has been
confirmed and a fix or mitigation is available.

## Vulnerabilities of particular interest

Examples of issues that should be reported privately include:

- bypassing `AUTH_SEARCH_REQUIRE` or `AUTH_INSTANCE_REQUIRE`;
- accessing private, deleted, removed, or otherwise excluded content;
- SQL injection or obtaining database access beyond the restricted viewer role;
- exposing a Lemmy JWT, cookie, database password, or other secret;
- sending a bearer token to an unintended host;
- cross-site scripting or bypassing the content security policy;
- leaking internal errors, queries, credentials, or sensitive headers;
- escaping the non-root, read-only container restrictions;
- using an unauthenticated request to cause repeatable, material resource
  exhaustion; or
- a vulnerable dependency that is reachable through this application.

Report third-party vulnerabilities directly to the appropriate upstream
project unless Lemmy Vote Viewer introduces or materially worsens the exposure.

## Expected behavior and known limitations

The following are not vulnerabilities by themselves:

- Voting data is public when `AUTH_SEARCH_REQUIRE=none`, which is the default.
- The viewer intentionally displays locally recorded voting data to users who
  satisfy the configured access requirement.
- An authorized administrator can access enabled instance and community
  overviews.
- `ENABLE_DOMAIN_SEARCH=false` hides instance and community overviews, but does
  not authenticate ordinary user or item searches.
- Remote activity is limited to data that federated to and remains in the local
  database, so results can be incomplete or differ between instances.
- Vote tables represent current locally recorded state, not a permanent audit
  trail of every historical vote event.
- Aggregate received-vote counts can differ from visible per-voter histories.
- A database query that exceeds the configured timeout returns HTTP 503. An
  isolated timeout is an availability or performance issue, not necessarily a
  security vulnerability.
- Search terms, usernames, community handles, item IDs, filters, and page
  numbers appear in request URLs and may be recorded by proxy or access logs.

See the README's [security note](README.md#security-note) and
[data semantics](README.md#data-semantics) for the intended exposure model.

## Authentication boundaries

Authentication is optional. A secure restricted deployment should set
`AUTH_PROVIDER=lemmy` and explicitly choose requirements for ordinary searches
and instance/community overviews. For example:

```text
AUTH_PROVIDER=lemmy
AUTH_SEARCH_REQUIRE=login
AUTH_INSTANCE_REQUIRE=admin
```

Each requirement can instead be `disabled` to turn its corresponding feature
off, or `none` to make it public. Disabled routes return HTTP 404 before
authentication and database access.

The viewer does not accept Lemmy passwords or possess Lemmy's JWT signing
secret. It reads the configured cookie and validates the bearer token against
the configured Lemmy API. The browser must send the Lemmy cookie to the viewer,
which normally requires the viewer to be served from the same hostname.

`LEMMY_INTERNAL_URL` is a security-sensitive trust setting. The viewer sends
the user's bearer token directly to this URL. Configure it only to a trusted
Lemmy backend controlled by the operator. Authentication requests do not follow
HTTP redirects, but a directly configured malicious or incorrect host would
still receive the token.

The authentication cache stores a SHA-256 digest as its key rather than the raw
token. Successful and failed validations may remain cached for up to
`AUTH_CACHE_SECONDS`. Reducing the cache interval shortens the time before
account, ban, deletion, or administrator changes are observed, at the cost of
more Lemmy API requests.

`AUTH_ALLOWED_USERS` has an effect only when a requirement is set to
`allowlist`. Lemmy administrators also satisfy the `allowlist` requirement.
Use `admin` when only Lemmy administrators should have access.

Authentication currently uses Lemmy's v3 `/api/v3/site` endpoint. Confirm API
compatibility before upgrading Lemmy; see the
[database compatibility guide](docs/database-compatibility.md).

## Database security

Never connect the viewer using Lemmy's administrative database account. Create
the dedicated `vote_viewer` role and apply [`db-grants.sql`](db-grants.sql).
The grant script:

- grants access only to the required tables and columns;
- sets transactions read-only by default;
- applies a statement timeout; and
- applies an idle-in-transaction timeout.

The application also requests read-only transactions and timeouts on each
database connection. These controls provide defense in depth but do not replace
network isolation and least-privilege grants.

Do not publish the PostgreSQL port to the internet. Restrict database access to
the required Docker network or private network. Use a strong, unique viewer
password and protect `.env` with restrictive filesystem permissions.

Reapply the grant script after an application update so newly required access
is explicit. Review every grant change before production deployment.

## Secret and data handling

Treat the following as secrets or sensitive operational data:

- `.env` and `.env.local` files;
- `DATABASE_URL` and database passwords;
- Lemmy JWTs and cookies;
- proxy and application logs containing user searches;
- PostgreSQL backups and copied production databases; and
- diagnostic output containing private hostnames or infrastructure details.

The repository ignores `.env*`, `*.dump`, and `*.backup` files except for the
provided example environment files. Ignore rules are not a security boundary:
always inspect `git status` and the staged diff before committing.

Copied production databases contain user and federation data. Store them on
encrypted, access-controlled systems, do not upload them to issue trackers or
public file-sharing services, and delete them securely when testing is
complete.

Never add cookies, authorization headers, tokens, or complete query strings to
structured application logs. Sanitize logs before sharing them.

## Reverse proxy and transport security

The application container serves plain HTTP and relies on the reverse proxy for
public TLS. Configure the proxy to:

- redirect public HTTP traffic to HTTPS;
- preserve the intended `/votes` path handling;
- avoid logging cookie or authorization headers;
- apply any additional operator-required authentication or network policy; and
- restrict direct access to the application container.

The application sends a restrictive content security policy, disables framing,
disables caching, prevents MIME sniffing, and sets restrictive referrer and
permissions policies. Do not weaken or overwrite these headers at the proxy
without understanding the effect.

## Container hardening

The supplied Compose configuration runs the viewer as a non-root user with:

- a read-only root filesystem;
- a small temporary filesystem mounted at `/tmp`;
- all Linux capabilities dropped;
- `no-new-privileges` enabled; and
- a process-count limit.

Keep these restrictions when adapting the deployment. Rebuild images regularly
to obtain patched base-image and Python dependencies, and review dependency
changes before release.

## Security update process

When a report is confirmed, the maintainer will determine the affected versions
and coordinate a fix on a private basis where practical. A security release
should include:

- a new final-component version unless the fix requires a larger compatibility
  change;
- an annotated release tag on the fixed `main` commit;
- upgrade or mitigation instructions;
- database-grant or configuration changes, if required; and
- appropriate credit for the reporter unless anonymity is requested.

After a fix is available, the issue may be disclosed through GitHub release
notes or a GitHub security advisory. Follow the project's
[release process](docs/releasing.md) when publishing the fix.

[releases]: https://github.com/BlueEther/lemmy-vote-viewer/releases
