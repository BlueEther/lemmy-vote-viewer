# SQL query reference

Lemmy Vote Viewer reads Lemmy's PostgreSQL schema directly. This document
describes every application query. Reusable SQL constants, generated variants,
and controlled sort expressions are defined in `vote_viewer/queries.py`;
small route-specific statements remain inline where a shared constant would
not improve clarity.

[`db-grants.sql`](../db-grants.sql) is the authoritative list of database
columns available to the viewer. See the
[database compatibility guide](database-compatibility.md) for the verified
Lemmy/PostgreSQL baseline and schema preflight.

## Execution model

Every database connection requests:

- read-only transactions;
- a five-second statement timeout;
- a ten-second idle-in-transaction timeout; and
- dictionary-like result rows through Psycopg's `dict_row` factory.

The dedicated `vote_viewer` role applies equivalent read-only and timeout
settings for defense in depth. Instance and community overview routes, and the
item-voter community-activity query, replace the five-second statement timeout
for their current transaction with the configured
`INSTANCE_QUERY_TIMEOUT_SECONDS`, constrained to 5–12 seconds.

Query values use Psycopg `%s` parameters. The application interpolates SQL text
only in these controlled cases:

- fixed community-filter fragments generate filtered and unfiltered variants;
- sort expressions are selected from fixed application dictionaries; and
- the recent vote window is an integer validated and constrained to 1–365 days.

Raw request values are not interpolated into SQL text.

## Query index

| Query | Purpose | Primary tables |
| --- | --- | --- |
| `COMMUNITY_LOOKUP_SQL` | Resolve a local or federated community handle | `community`, `instance` |
| Remote-user lookup | Resolve `username@instance` | `person` |
| Local-user lookup | Resolve a local username | `person` |
| `USER_SUGGESTIONS_SQL` | Autocomplete user handles | `person`, `instance` |
| `USER_VOTES_SQL` / `USER_VOTES_OLDEST_SQL` | Paginate unfiltered votes cast by a user | Vote, content, community, and person tables |
| `USER_VOTES_BY_COMMUNITY_SQL` / `USER_VOTES_OLDEST_BY_COMMUNITY_SQL` | Paginate community-filtered votes cast by a user | Vote, content, community, and person tables |
| `USER_SUMMARY_SQL` | Count votes cast and filtered results | Vote, post, and community tables |
| `USER_RECEIVED_SUMMARY_SQL` | Summarize votes received by a user's content | Aggregate, content, and community tables |
| `USER_RECEIVED_ITEMS_SQL` | Paginate unfiltered content that received votes | Aggregate, content, and community tables |
| `USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL` | Paginate community-filtered content that received votes | Aggregate, content, and community tables |
| `USER_COMMUNITY_SUMMARY_SQL` | Group a user's cast and received activity by community | Vote, aggregate, content, and community tables |
| `COMMUNITY_OVERVIEW_SQL` | Summarize recent voters in one community | Vote, post, and person tables |
| `ITEM_BY_AP_ID_SQL` | Resolve an ActivityPub post or comment URL | Post, comment, and community tables |
| `INSTANCE_LOOKUP_SQL` | Resolve an instance domain | `instance` |
| Overview timeout statement | Set the transaction-local overview timeout | PostgreSQL configuration |
| `INSTANCE_OVERVIEW_SQL` | Summarize recent voters from one instance | Vote and person tables |
| `POST_ITEM_SQL` | Load post metadata for an item-voter page | `post`, `community` |
| `COMMENT_ITEM_SQL` | Load comment and parent-post metadata | `comment`, `post`, `community` |
| `POST_VOTERS_SQL` | Paginate voters on one post | `post_like`, `person` |
| `COMMENT_VOTERS_SQL` | Paginate voters on one comment | `comment_like`, `person` |
| `ITEM_VOTER_ACTIVITY_SQL` | Count recent community activity for one voter page | Vote, aggregate, post, and comment tables |
| `POST_VOTER_SUMMARY_SQL` | Count post voters by direction | `post_like`, `person` |
| `COMMENT_VOTER_SUMMARY_SQL` | Count comment voters by direction | `comment_like`, `person` |

## Community lookup

### `COMMUNITY_LOOKUP_SQL`

**Caller:** `resolve_community()`

**Purpose:** Resolve `community` or `community@instance` (with an optional
leading `!`) to a single public, active community row.

**Parameters:**

1. Community name
2. Optional instance domain, used to select local-only behavior when null
3. Optional instance domain for an exact remote-domain match

**Behavior:**

- Matches the community name case-insensitively.
- A handle without a domain matches only a local community.
- A handle with a domain joins `instance` and requires an exact domain.
- Excludes non-public, deleted, and removed communities.
- Prefers local rows, then the lowest community ID, and returns one result.

The resolver derives the canonical federated handle from `actor_id` after the
query returns.

## User resolution and suggestions

### Remote-user lookup

**Caller:** `resolve_user()` when the input contains `@`

**Purpose:** Find a non-deleted remote person with the requested username.

**Parameters:** Remote username without its domain

**Behavior:** Selects all non-local people whose name matches
case-insensitively. Python then extracts each `actor_id` domain and returns the
first exact match for the requested domain.

The domain comparison is deliberately performed outside SQL. Instances can
therefore contain more than one same-named remote person candidate before the
correct ActivityPub origin is selected.

### Local-user lookup

**Caller:** `resolve_user()` when the input has no domain

**Purpose:** Resolve a local, non-deleted username.

**Parameters:** Local username

**Behavior:** Matches the name case-insensitively, requires `local = true`, and
returns at most one row. The resolver then adds the canonical handle and local
profile path.

### `USER_SUGGESTIONS_SQL`

**Caller:** `find_user_suggestions()`

**Purpose:** Produce autocomplete candidates for a username prefix and optional
instance-domain prefix.

**Parameters:**

1. Escaped username prefix pattern
2. Optional raw domain-prefix marker
3. Escaped domain prefix pattern
4. Unescaped username prefix for exact-name ranking
5. Result limit, currently normally eight

**Behavior:**

- Excludes deleted people.
- Uses prefix `ILIKE` patterns with escaped backslash, percent, and underscore
  characters.
- Without a domain prefix, considers local and remote users.
- With a domain prefix, requires a remote user whose joined instance domain has
  that prefix.
- Ranks an exact username first, then local users, normalized names, and ID.

The deterministic ID tie-breaker prevents unstable ordering between equal
names.

## Votes cast by a user

### `USER_VOTES_SQL_TEMPLATE`

This template generates two executable queries. Both combine post and comment
votes with `UNION ALL`, materialize eligible vote identifiers, paginate them,
and only then join the page to content, community, and author details.

This paginate-before-enrichment shape avoids joining and enriching every vote
before applying the page limit.

Common filters and behavior:

- Restricts votes to the selected user.
- Supports `all`, `post`, or `comment` content types.
- Supports positive, negative, neutral, or all score states.
- Includes only public, active communities.
- Keeps votes on deleted or removed content but redacts titles, content, and
  author identity as required.
- Supports newest-first and oldest-first ordering with type, post ID, and
  comment ID tie-breakers.
- Uses `LIMIT` and `OFFSET` after eligibility filtering.

Returned rows contain vote time/type/score, item IDs, redacted post or comment
text, community identity, author identity where permitted, ActivityPub URL,
local/remote state, and hidden-content flags.

### `USER_VOTES_SQL`

**Caller:** Cast history without a community filter

**Parameters:**

1. Content type
2. Optional score
3. User ID for post votes
4. User ID for comment votes
5. Page size
6. Offset

This variant does not add `community_id` to the filter CTE. Keeping it separate
protects the cheaper unfiltered execution path.

### `USER_VOTES_BY_COMMUNITY_SQL`

**Caller:** Cast history with a resolved community filter

**Parameters:** Content type, optional score, community ID, two user IDs, page
size, and offset.

This variant adds the typed community ID to the filter CTE and applies it to
both post and comment branches.

### `USER_SUMMARY_SQL`

**Caller:** Every resolved user search

**Purpose:** Populate the top-level votes-cast summary and determine the
filtered cast-history row count used for pagination.

**Parameters:** Two copies of user ID followed by paired content-type, score,
and optional community-filter values.

**Behavior:**

- Combines post and comment votes in public, active communities.
- Returns total, up, down, neutral, post, and comment counts.
- Calculates `filtered_total` for the active content, score, and community
  filters.

The overall summary remains unfiltered while `filtered_total` drives the
current history page count.

## Votes received by a user

### `USER_RECEIVED_SUMMARY_SQL`

**Caller:** Every resolved user search

**Purpose:** Populate the top-level received-vote summary and item counts.

**Parameters:** Paired optional community IDs and user IDs for the post and
comment branches.

**Behavior:**

- Uses `post_aggregates` for posts created by the user.
- Uses `comment_aggregates` joined through comments and posts for comments
  created by the user.
- Includes only public, active communities.
- Sums current aggregate upvotes and downvotes.
- Counts content items only when their aggregate vote total is greater than
  zero.
- Returns overall and per-type item counts plus community-filtered item counts.

Received totals are intentionally global in the top summary. The optional
community filter affects item counts used by the history view, not the displayed
global upvote and downvote totals.

### `USER_RECEIVED_ITEMS_SQL_TEMPLATE`

This template generates filtered and unfiltered received-item history queries.
It combines the user's post and comment aggregate rows, materializes eligible
item identifiers and totals, paginates them, and then joins the selected page to
content and community details.

Supported sorts are:

- `date`: aggregate row `published` timestamp, newest first;
- `oldest`: aggregate row `published` timestamp, oldest first;
- `top`: `upvotes - downvotes`, highest first; and
- `bottom`: `downvotes - upvotes`, highest first.

All sorts use published time, type, post ID, and comment ID as deterministic
tie-breakers. Only items with at least one received upvote or downvote are
eligible. Deleted and removed content is retained but redacted.

### `USER_RECEIVED_ITEMS_SQL`

**Caller:** Received history without a community filter

**Parameters:** Content type, received sort, user ID for posts, user ID for
comments, page size, and offset.

As with cast history, this variant omits community filtering entirely to retain
the cheaper default path.

### `USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL`

**Caller:** Received history with a resolved community filter

**Parameters:** Content type, received sort, community ID, two user IDs, page
size, and offset.

The community ID is applied to post aggregates directly and through the parent
post for comment aggregates.

## User activity grouped by community

### `USER_COMMUNITY_SUMMARY_SQL`

**Caller:** The `communities` user-history view

**Purpose:** Produce one combined cast/received summary per community for a
user.

**Parameters:** User ID for post cast votes, comment cast votes, received posts,
received comments, authored posts, and authored comments, followed by page size
and offset.

**Behavior:**

1. Groups post and comment votes cast by community and direction.
2. Groups aggregate votes received on the user's posts and comments by
   community and direction.
3. Combines cast and received groups with a full outer join so activity present
   on only one side is retained.
4. Groups the user's lifetime authored post and comment totals by community and
   includes communities with authored content but no recorded vote activity.
5. Joins community display information.
6. Excludes non-public, deleted, and removed communities from output.
7. Uses `COUNT(*) OVER ()` to return the total community count for pagination.

The route inserts an order expression from `COMMUNITY_SUMMARY_SORTS`. Available
orders are combined total, cast total, received total, combined downvotes, and
community name. Every expression ends with normalized name and community ID
tie-breakers.

Unlike the cast and received history templates, this query aggregates all
candidate community activity before applying `LIMIT/OFFSET`.

## Community overview

### `COMMUNITY_OVERVIEW_SQL`

**Caller:** `/community/<handle>`

**Purpose:** Summarize recent locally recorded votes in one resolved community,
grouped by voter.

**Parameters:** Two copies of community ID, followed by the lower and upper row
positions for the requested page.

**Generated values:** Validated recent-window days and an order expression from
`COMMUNITY_OVERVIEW_SORTS`.

**Behavior:**

1. Combines recent post and comment likes whose parent post belongs to the
   community.
2. Groups totals, directions, neutral states, and latest vote by person.
3. Joins people and excludes deleted voters.
4. Calculates the page-independent summary.
5. Assigns deterministic row numbers using the requested sort.
6. Returns summary columns alongside the requested voter page.

Available sorts are total, downvotes, downvote percentage, upvotes, recent, and
username. Downvote-percentage ranking requires at least ten votes; lower-volume
users follow. The community is already resolved as public and active before
this query runs.

## Item lookup by ActivityPub URL

### `ITEM_BY_AP_ID_SQL`

**Caller:** `resolve_item_search()`

**Purpose:** Resolve a post or comment ActivityPub URL entered into item search.

**Parameters:** Two accepted URL forms for the post branch and the same two
forms for the comment branch.

**Behavior:**

- Combines post and comment candidates with `UNION ALL`.
- Joins each result to its community.
- Requires a public, active community.
- Returns the item kind and local database ID.
- Returns at most one result.

The query does not require the post or comment itself to remain active; later
item queries retain the row and redact deleted or removed content.

## Instance overview

### `INSTANCE_LOOKUP_SQL`

**Caller:** `/instance/<domain>`

**Purpose:** Resolve a previously normalized instance domain to its ID and
canonical stored domain.

**Parameters:** Exact domain

Returns at most one instance row.

### Overview timeout statement

**Callers:** Instance and community overview routes and item-voter activity
queries

```sql
SELECT set_config('statement_timeout', %s, true)
```

Sets the statement timeout only for the current transaction. The supplied value
is the validated `INSTANCE_QUERY_TIMEOUT_SECONDS` plus the `s` unit. It does not
alter the role or database globally.

### `INSTANCE_OVERVIEW_SQL`

**Caller:** `/instance/<domain>`

**Purpose:** Summarize recently recorded votes cast by users belonging to one
known instance.

**Parameters:** Instance ID, then the lower and upper row positions for the
requested page.

**Generated values:** Validated recent-window days and an expression from
`INSTANCE_SORTS`.

**Behavior:**

1. Selects the target instance once in a materialized CTE.
2. Combines recent post and comment likes by non-deleted people whose
   `instance_id` matches the target.
3. Groups totals, directions, neutral states, and latest vote by person.
4. Counts every known non-deleted user from the instance and the subset with
   recent votes.
5. Calculates page-independent vote totals.
6. Ranks users deterministically and returns the requested row-number range.

Available sorts are total, downvotes, downvote percentage, upvotes, recent, and
username. Percentage sorting applies only after ten votes.

This query intentionally counts direct locally stored likes without joining to
community visibility. Instance totals can therefore include communities not
otherwise displayed by the viewer. Linked per-user histories continue to apply
the public-community filters and may show lower counts.

## Post and comment item pages

### `POST_ITEM_SQL`

**Caller:** `item_votes('post', item_id)`

**Purpose:** Load the post and community metadata needed by a post-voter page.

**Parameters:** Post ID

**Behavior:** Requires a public, active community, returns at most one row, and
redacts a deleted or removed post title while retaining hidden-state flags and
local/ActivityPub link information.

### `COMMENT_ITEM_SQL`

**Caller:** `item_votes('comment', item_id)`

**Purpose:** Load comment, parent-post, and community metadata for a
comment-voter page.

**Parameters:** Comment ID

**Behavior:** Requires a public, active community and returns at most one row.
It independently redacts unavailable comments and parent posts and returns the
local/ActivityPub state needed for both item and post links.

### `POST_VOTERS_SQL`

**Caller:** Post item-voter page

**Purpose:** Return one page of non-deleted people whose current post vote is
stored locally.

**Parameters:** Post ID, page size, and offset

Rows contain vote time, score, voter identity, local/remote state, and actor
URL. Ordering is upvotes before neutral/downvotes according to numeric score,
then normalized username and voter ID.

### `COMMENT_VOTERS_SQL`

**Caller:** Comment item-voter page

**Purpose:** Return one page of non-deleted people whose current comment vote is
stored locally.

**Parameters:** Comment ID, page size, and offset

It returns and orders the same voter fields as the post variant.

### `ITEM_VOTER_ACTIVITY_SQL`

**Caller:** Post and comment item-voter pages after voter pagination

**Purpose:** Add configured-window posts, comments, upvotes, and downvotes for
each displayed voter, scoped to the item's community.

The route skips this query entirely when `ENABLE_COMMUNITY_CONTENT_COUNTS` is
false.

**Parameters:** Concrete page voter IDs, community ID, and activity-window
days.

Passing concrete voter IDs separately from voter pagination lets PostgreSQL
combine its existing indexes and keeps the expensive aggregates limited to
the displayed page. The route disables PostgreSQL JIT for the activity query
because the bounded aggregate is cheaper to execute directly than to compile,
and disables parallel workers to avoid shared-memory overhead for the small
page-scoped result. The activity query uses the configured overview timeout
instead of the connection's shorter default timeout.

### `POST_VOTER_SUMMARY_SQL`

**Caller:** Post item-voter page before pagination

**Purpose:** Count all non-deleted current post voters by positive, negative,
and neutral score.

**Parameters:** Post ID

The total drives item-page pagination.

### `COMMENT_VOTER_SUMMARY_SQL`

**Caller:** Comment item-voter page before pagination

**Purpose:** Count all non-deleted current comment voters by positive, negative,
and neutral score.

**Parameters:** Comment ID

The total drives item-page pagination.

## Shared data and visibility rules

Most user-facing history and item-resolution queries require communities to be:

```sql
visibility = 'Public'
AND deleted = false
AND removed = false
```

Deleted voters are excluded from item-voter and overview rows. Votes on removed
or deleted posts/comments can remain in cast or received histories, but content
and author details are redacted.

Instance overview is the deliberate exception to community visibility filtering
because it measures recent direct votes from users assigned to an instance.
This difference is disclosed on the instance page and in the README.

## Pagination characteristics

User cast and received-item history queries materialize and paginate eligible
identifiers before enrichment. Overview queries assign row numbers before
selecting the requested range. Item-voter and community-summary queries use
`LIMIT/OFFSET` directly.

All current list queries use offset-based pagination. Deterministic tie-breakers
prevent duplicate or reordered rows when sort values are equal, but large
offsets can still become expensive. Cursor-based pagination remains a future
performance improvement.

## Maintaining this reference

Update this document whenever a query is added, removed, or materially changed.
In particular, record changes to:

- tables and columns;
- parameter order;
- filters and visibility semantics;
- sort expressions and tie-breakers;
- summary versus filtered counts;
- timeout behavior;
- pagination shape; and
- performance assumptions.

Schema access changes must also update `db-grants.sql` and
`database-compatibility.md`. Query refactoring should preserve the behavior
described here unless a separate, reviewed behavior change intentionally updates
the contract.
