# Unit tests

The unit test suite is in [`tests/test_app.py`](../tests/test_app.py),
[`tests/test_auth.py`](../tests/test_auth.py),
[`tests/test_config.py`](../tests/test_config.py), and
[`tests/test_database.py`](../tests/test_database.py). It uses Python's
standard-library `unittest` framework and currently contains 53 tests.

The suite covers authentication, authorization, disabled-feature handling,
community-handle parsing, URL construction, SQL-query selection, link
enrichment, and conditional template behavior. Configuration tests separately
cover environment defaults, normalization, bounds, and startup validation.

## Running the tests

Install the application dependencies, then run the suite from the repository
root:

```sh
python3 -m unittest discover -s tests -v
```

The suite can also be run using an existing local application image without
installing Python dependencies on the host:

```sh
docker run --rm \
  -e PYTHONPATH=/src \
  -v "$PWD:/src:ro" \
  -w /tmp \
  lemmy-vote-viewer-local \
  python -m unittest discover -s /src/tests -v
```

The Docker command assumes that the `lemmy-vote-viewer-local` image has already
been built.

## Test setup and isolation

Before importing the application, the test module supplies fixed environment
settings for the database URL, application prefix, Lemmy URLs, authentication
requirements, allowlist, authentication-cache duration, and domain-search
feature flag. Setting `APP_PREFIX=/` in the module keeps path assertions
independent of a developer's `.env` file and the application's default `/votes`
prefix.

The database URL deliberately points to an unused database because these tests
do not require a live PostgreSQL connection. Request-level query tests use
`ScriptedDatabase`, a small database double that supplies predetermined rows
and records SQL and parameters for later assertions. Each test clears the
in-memory authentication cache and creates a fresh Flask test client. Lemmy API
responses are represented by `FakeResponse` and the application's HTTP opener
is mocked, so the suite does not contact a Lemmy server or the network.

## Authentication and authorization

### `test_root_exports_only_app_compatibility_entrypoint`

Verifies that the root `app:app` compatibility entry point exposes the
configured Flask application without also exporting the package's
`create_app` helper.

### `test_pure_link_helpers_use_explicit_configuration`

Verifies that the extracted pure link helpers accept the application prefix,
Lemmy base URL, and page size explicitly. It checks prefixed URL generation,
oldest-first cast-history URLs, local item recognition, and pagination without
Flask request or application configuration imports.

## Configuration

### `test_defaults_are_preserved`

Loads configuration from only the required database setting and verifies the
existing prefix, page size, query timeout, vote window, timezone, feature flag,
authentication, cookie, cache, request-timeout, and Lemmy URL defaults.

### `test_values_are_normalized_bounded_and_fallback_on_bad_numbers`

Verifies prefix and URL normalization, case-insensitive booleans and allowed
users, numeric minimum and maximum bounds, invalid-number fallbacks, and Lemmy
public/internal URL derivation.

### `test_disabled_features_do_not_require_an_authentication_provider`

Verifies that `disabled` is accepted for both feature requirements without a
Lemmy authentication provider and is normalized case-insensitively.

### `test_invalid_settings_raise_the_existing_startup_errors`

Checks invalid booleans, timezones, authentication providers and requirements,
provider/requirement mismatches, missing Lemmy authentication URLs, malformed
ports, and Lemmy URLs containing paths or queries. Each case must retain its
specific startup error.

### `test_database_url_remains_required`

Verifies that loading configuration without `DATABASE_URL` still raises
`KeyError` during startup.

## Database connection

### `test_connection_preserves_read_only_timeouts_and_row_factory`

Mocks Psycopg's connection function and verifies that the extracted database
boundary passes through the configured DSN while preserving the five-second
connection timeout, dictionary row factory, read-only transactions, five-second
statement timeout, and ten-second idle transaction timeout.

### `test_anonymous_search_requires_login`

Requests the search page without a JWT cookie and verifies that it returns HTTP
401 with a prompt to log in to Lemmy.

### `test_anonymous_item_routes_require_login_before_database_access`

Requests both a post-voter route and a comment-voter route anonymously. It
verifies that both return HTTP 401 before attempting database access.

### `test_disabled_requirements_hide_routes_before_database_access`

Sets each feature requirement to `disabled` and verifies that representative
search and overview routes return HTTP 404 without opening a database
connection.

### `test_item_routes_select_queries_and_preserve_pagination`

Exercises successful post and comment voter pages. It verifies the selected
item, summary, and voter SQL constants, exact item ID, limit and offset
parameters, rendered item type, and second-page state.

### `test_instance_overview_selects_sort_timeout_and_page`

Exercises a successful administrator-only instance overview. It verifies
domain normalization, controlled sort interpolation, the per-query statement
timeout override, exact page bounds, rendered pagination state, and
configured-window post and comment totals for each paginated user.

### `test_community_overview_selects_sort_timeout_and_page`

Exercises a successful administrator-only community overview. It verifies
controlled sort interpolation, the statement timeout override, exact community
and page parameters, local and remote community links, rendered pagination
state, and community-scoped post and comment counts limited to the configured
vote window in each user row.

### `test_user_summary_shows_post_and_comment_counts`

Renders the main user-search summary and verifies that the user's total known
post and comment counts appear between the display name and handle.

### `test_logged_in_user_can_search_but_cannot_see_instance_search`

Mocks a valid, non-admin Lemmy account and verifies that the search page loads,
shows the signed-in username, and hides both instance and community overview
search controls.

### `test_logged_in_non_admin_cannot_submit_instance_search`

Submits an instance overview search as a logged-in non-admin and verifies that
the request is rejected with HTTP 403.

### `test_logged_in_non_admin_cannot_submit_community_search`

Submits a community overview search as a logged-in non-admin and verifies that
the request is rejected with HTTP 403.

### `test_logged_in_non_admin_cannot_open_instance_route`

Requests a direct instance overview URL as a logged-in non-admin and verifies
that the route returns HTTP 403.

### `test_logged_in_non_admin_cannot_open_community_overview`

Requests a direct community overview URL as a logged-in non-admin and verifies
that the route returns HTTP 403.

### `test_disabled_instance_search_returns_404_before_authentication`

Temporarily disables domain search and requests an instance overview without
authentication. It verifies that the feature is hidden with HTTP 404 rather
than exposing its existence through an authentication response.

### `test_disabled_instance_search_hides_community_overview`

Temporarily disables domain search and verifies that a direct community
overview URL also returns HTTP 404.

### `test_admin_sees_instance_search`

Mocks an authenticated Lemmy administrator and verifies that the page identifies
the account as an admin and displays both instance and community overview
search controls.

### `test_admin_community_search_normalizes_and_redirects`

Submits a community search as an administrator using uppercase instance text
and a trailing dot. It verifies that the response redirects to a normalized,
lowercase community overview path.

### `test_allowlist_is_case_insensitive_and_also_allows_admins`

Checks the `allowlist` access requirement directly. It verifies that username
matching is case-insensitive, administrators are accepted even when absent from
the allowlist, and an unlisted non-admin is rejected.

### `test_banned_user_is_not_authenticated`

Mocks a Lemmy response for a banned account and verifies that the viewer treats
the account as unauthenticated and returns HTTP 401.

### `test_authentication_failure_returns_503`

Makes the mocked Lemmy authentication request raise `URLError`. It verifies
that the viewer returns HTTP 503 with an authentication-service-unavailable
message.

### `test_authentication_result_is_cached_without_storing_token`

Makes two requests with the same JWT and verifies that Lemmy authentication is
performed only once. It also checks that the request targets the configured
internal Lemmy `/api/v3/site` endpoint, sends the JWT as a bearer token, and
stores only hashed byte keys in the authentication cache rather than the raw
token.

### `test_disabled_requirement_returns_404_without_authentication`

Verifies directly that `disabled` returns HTTP 404 without attempting Lemmy
authentication and remains unavailable even to an administrator.

### `test_authentication_redirects_remain_disabled`

Verifies that the authentication HTTP redirect handler refuses redirects rather
than allowing a configured internal Lemmy request to leave its expected origin.

### `test_authentication_cache_remains_bounded`

Adds one more entry than the authentication cache limit and verifies that the
cache remains at 1,024 entries and evicts the oldest entry.

### `test_oversized_authentication_response_is_rejected`

Supplies an authentication response one byte beyond the one-megabyte limit and
verifies that token validation reports the authentication service as
unavailable.

## Search-route characterization

These request-level tests lock in the observable behavior of the large
`index()` route before it is split into smaller modules.

### `test_index_cast_view_selects_unfiltered_query_and_preserves_filters`

Requests the second page of a user's comment downvotes. It verifies selection
of the cheaper unfiltered cast-history query, its exact parameters, the
rendered filter state, and preservation of the user, type, score, and page in
the next-page URL.

### `test_index_cast_view_selects_community_filtered_query`

Requests cast history within a resolved community and verifies selection of
the oldest-first community-filtered query, including the community ID in its
parameters, the canonical community handle in the rendered state, and sort
preservation in the clear-filter URL.

### `test_index_received_view_selects_sort_and_ignores_score`

Exercises newest, oldest, top, and bottom sorting for received votes. It
verifies the selected unfiltered received-items query and parameters and
confirms that a cast-vote score filter is discarded in received mode.

### `test_index_received_view_selects_community_filtered_query`

Requests bottom-sorted received votes within a community. It verifies selection
of the community-filtered received-items query, its parameters, and preservation
of received mode in pagination.

### `test_index_community_view_selects_sort_and_preserves_pagination`

Requests the second page of downvote-sorted community summaries. It verifies
the controlled SQL sort expression, exact pagination parameters, forced `all`
content type, and preservation of the community-summary view and sort order in
the next-page URL.

### `test_index_empty_deep_community_page_redirects_to_first_page`

Requests an empty community-summary page beyond the available results and
verifies the redirect to the first page while retaining the username, view,
and sort order.

### `test_index_cast_page_is_clamped_to_available_results`

Requests an extremely large cast-history page number and verifies that the
rendered page and SQL offset are clamped to the final available page.

### `test_index_local_item_paths_redirect_without_database_lookup`

Exercises both `/post/123` and `/comment/456` search input. It verifies direct
redirects to the corresponding viewer pages without opening the database.

### `test_index_activitypub_item_url_uses_lookup_and_redirects`

Exercises remote post and comment ActivityPub URLs. It verifies use of the
public-item lookup query with both trailing-slash variants and redirects to the
resolved viewer item pages.

### `test_index_query_timeout_returns_503`

Makes the database boundary raise PostgreSQL's query-cancellation exception
during a user search and verifies that the request returns HTTP 503 with the
specific timeout message.

## Community parsing and URL state

### `test_community_handle_parser_accepts_local_and_remote_handles`

Verifies that the parser accepts both `!newzealand` and
`!technology@lemmy.world`, trims surrounding whitespace, lowercases the
instance, and removes a trailing dot from the instance name.

### `test_community_handle_parser_rejects_invalid_values`

Verifies that the parser rejects a missing `!`, an empty name, a missing
instance after `@`, a path component, and whitespace inside a community name.
Each invalid value is reported as a separate `subTest` case.

### `test_index_url_preserves_community_filter`

Builds a paginated comment/downvote history URL and verifies that the user,
content type, score, community filter, and page are all retained in its query
string.

### `test_community_summary_url_preserves_sort_and_page`

Builds a user's community-summary URL and verifies that the communities view,
selected sort order, and page number are retained along with the username.

### `test_community_overview_url_preserves_sort_and_page`

Builds a community overview URL and verifies both its community path and the
preserved sort and page query parameters.

## Query selection

### `test_unfiltered_history_queries_keep_community_out_of_filter_cte`

Checks the SQL constants used for user history. It verifies that unfiltered
cast and received queries do not reference `f.community_id`, while their
community-filtered variants do. This guards the less-expensive unfiltered query
paths from accidentally acquiring the community join and filtering work.

## Link enrichment and templates

### `test_community_user_links_to_profile_and_filtered_history`

Enriches a remote user shown on a community overview. It verifies the local
profile proxy path, original remote profile URL, and vote-history URL containing
both the federated username and selected community filter.

### `test_user_link_enrichment_separates_viewer_local_and_remote_urls`

Verifies that a remote user's row has separate vote-history, local-profile, and
original remote-profile targets. It also verifies that a local user's profile
uses the shorter local path and has no redundant remote link.

### `test_received_item_text_and_local_links_have_separate_targets`

Enriches a received-vote comment and verifies the distinct destinations for
the viewer's item-voter page, local Lemmy comment, original remote comment,
community overview, local community page, and original remote post. It also
checks that a local community does not receive a redundant remote URL.

### `test_item_community_text_links_only_for_instance_authorized_users`

Renders an item page once for a regular authenticated user and once for an
administrator. It verifies that both can use the local Lemmy community link,
while only the administrator receives a clickable vote-viewer community
overview link on the community name.

### `test_community_summary_links_to_cast_and_received_filters`

Enriches remote and local community-summary rows. For a remote community, it
verifies the federated display name, local Lemmy path, original remote URL,
vote-viewer overview path, and cast/received history filters. For a local
community, it verifies the short display and local path and confirms that no
redundant remote URL is produced.

## Current boundaries

These are isolated unit tests. They do not currently:

- connect to PostgreSQL or execute the SQL queries against a Lemmy schema;
- validate compatibility across Lemmy or PostgreSQL versions;
- run the application through Gunicorn or Docker Compose;
- exercise pages in a browser or verify responsive layout; or
- test query performance, indexes, timeouts from a real database, or deep
  pagination.

Those areas require database-backed integration tests, deployment tests, or
browser-level tests rather than additions to this isolated unit suite.
