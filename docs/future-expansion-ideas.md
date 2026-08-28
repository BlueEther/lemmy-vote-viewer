# Future expansion ideas

This document records related ideas for expanding the existing instance and
community overview features:

1. a paginated list of known instances with recent vote totals;
2. a paginated list of known communities with recent vote totals;
3. a time-based voting graph on an instance overview; and
4. a time-based voting graph on a community overview.

These are design notes, not committed roadmap items. Query prototypes should be
benchmarked against copied production data before any view is implemented.

## Shared purpose

The existing search controls require an operator to know an instance domain or
community handle. The proposed list views would help an authorized operator
discover where locally recorded voting activity is coming from and where it is
occurring.

Both views would use the configured `VOTE_WINDOW_DAYS`, which defaults
to 30 days. Counts would describe votes stored by the local Lemmy instance, not
complete activity from the wider federation.

Both views should use the existing controls:

- `ENABLE_DOMAIN_SEARCH` must be enabled;
- the account must satisfy `AUTH_INSTANCE_REQUIRE`;
- direct routes must enforce the same rules as the UI; and
- disabled routes should return HTTP 404, matching existing overview behavior.

Neither view should be public merely because ordinary user search is public.

## Known-instance activity view

### Proposed entry point

Add a **Browse known instances** button beside the existing instance search.
The likely route is:

```text
/instances
```

Selecting an instance would open the existing:

```text
/instance/<domain>
```

### Suggested columns

- Instance domain
- Recent voters
- Total votes
- Upvotes
- Downvotes
- Downvote percentage
- Latest recorded vote
- Link to the existing instance overview

### Suggested sorting

- Most votes
- Most downvotes
- Downvote percentage
- Most upvotes
- Most recent activity
- Domain

Sorting must use deterministic domain or ID tie-breakers, and pagination must
preserve the selected sort.

### Data meaning

The view would report:

> Votes cast by known users from each instance and recorded locally during the
> configured recent-vote window.

It would not report every vote made on each remote instance. A local Lemmy
instance sees only activity that federates to it and remains in its database.

### Query shape

The query would:

1. select recent `post_like` and `comment_like` records;
2. join each vote to `person`;
3. group activity by `person.instance_id`;
4. join the result to `instance`; and
5. sort and paginate the grouped rows.

Counting recent voters is relatively natural for this view because each person
belongs to one instance. The query can group by `(instance_id, person_id)` and
then summarize each instance.

### Scope options

The recommended default is to show only instances with at least one recent
vote. This produces a useful operational list and avoids pages of inactive
domains.

An optional **Include inactive instances** control could left-join totals to the
complete `instance` table and include zero-vote rows. This is technically
straightforward but produces a noisier result.

### Estimated implementation effort

- Basic query, route, template, and entry button: 3–5 hours
- Sorting, pagination, authorization, and tests: another 3–5 hours
- Production-size query analysis and optimization: up to another day
- Caching, if required after benchmarking: additional work

A production-ready implementation is therefore likely to require one to two
days.

## Known-community activity view

### Proposed entry point

Add a **Browse known communities** button beside the existing community
overview search. The likely route is:

```text
/communities
```

Selecting a community would open the existing:

```text
/community/<handle>
```

### Suggested columns

The recommended first version would show:

- Community name and federated handle
- Total votes
- Upvotes
- Downvotes
- Downvote percentage
- Latest recorded vote
- Local and original remote community links
- Link to the existing community overview

Distinct recent voters should be treated as optional until its cost is
measured.

### Suggested sorting

- Most votes
- Most downvotes
- Downvote percentage
- Most upvotes
- Most recent activity
- Community name

Public, active communities with recent activity should appear by default.
Deleted, removed, or non-public communities should remain excluded.

### Data meaning

The view would report:

> Votes recorded locally on posts and comments in each public community during
> the configured recent-vote window.

These are direct locally stored vote records. They are not necessarily complete
for remote communities and do not represent all activity visible on the
community's home instance.

### Query shape

The query would:

1. select recent `post_like` and `comment_like` records;
2. use each vote's post ID to join to `post`;
3. group activity by `post.community_id`;
4. join the result to `community` and `instance` for display and links; and
5. filter, sort, and paginate the grouped rows.

`comment_like` already stores `post_id`, so comment votes do not need an
additional join through `comment` merely to determine the community.

### Distinct-voter cost

Counting distinct recent voters per community can be more expensive than the
equivalent instance count:

- a person belongs to exactly one instance; but
- one person can vote in many communities.

The intermediate `(community_id, person_id)` grouping can therefore be much
larger than `(instance_id, person_id)`. The first implementation should omit
recent-voter counts unless benchmarking shows they remain comfortably within
the query timeout.

### Scope options

The recommended default is to list public, active communities with recent
votes. Including every known community with a zero count is easy after totals
are calculated, but is less useful and should be optional if added.

### Estimated implementation effort

Implemented independently, the community view would also take approximately
one to two days. If it follows the instance list, shared sorting, pagination,
authorization, styles, and tests should reduce the additional work to roughly
three to six hours, excluding unexpected query optimization.

## Instance and community voting graphs

The initial asynchronous, cached implementation is now available behind
disabled-by-default instance and community feature flags. The application-owned
daily rollup described below remains a possible future optimization.

### Implemented behavior

Daily upvote, downvote, and neutral-vote graphs appear on the existing instance
and community overview pages. The graphs use `VOTE_WINDOW_DAYS` and the
configured timezone, matching the user Cast and Received graphs.

The data meaning should match each existing overview:

- an instance graph shows votes cast by known users whose `person.instance_id`
  is the selected instance; and
- a community graph shows votes recorded locally on posts and comments in the
  selected public community.

These graphs would describe only votes stored by the local Lemmy database. They
would not represent complete activity on a remote instance or community.

### Loading and caching

Both graphs should load asynchronously after the overview page renders. A slow
or failed graph query must not prevent the existing overview table from being
used.

Use the same cache-miss coalescing pattern as the user graphs so concurrent
requests do not start duplicate expensive queries. Instance and community
graphs are shared between visitors and change slowly relative to their 30-day
window, so a 30-minute default cache is appropriate:

```text
OVERVIEW_VOTE_GRAPH_CACHE_SECONDS=1800
```

Keep this separate from `USER_VOTE_GRAPH_CACHE_SECONDS`, which can retain its
shorter default. Cache keys must include:

- graph type (`instance` or `community`);
- instance or community ID;
- `VOTE_WINDOW_DAYS`; and
- configured timezone.

Cache entries may continue to live in the application's `/tmp` cache and may be
discarded when the container is recreated. A production implementation should
retain the existing statement timeout and return a graph-specific error
fragment rather than replacing the complete page with HTTP 503.

Use independent feature flags so operators can disable either cost separately:

```text
ENABLE_INSTANCE_VOTE_GRAPHS=true
ENABLE_COMMUNITY_VOTE_GRAPHS=true
```

### Expected SQL cost

Generating the daily buckets is inexpensive. Selecting all qualifying recent
votes is the dominant cost and largely repeats work already performed by the
overview query. Running the graph as a separate asynchronous query therefore
protects page rendering, but an uncached visit can approximately double the
overview's vote-scanning work.

Initial 30-day graph-shaped queries on the copied test database measured:

| Target | Matching votes | Warm execution time | Shared buffers touched | Storage read |
| --- | ---: | ---: | ---: | ---: |
| `technology@lemmy.world` | about 397,000 | 0.85 seconds | about 12 GB | about 300 MB |
| `lemmy.world` users | about 826,000 | 0.71 seconds | about 15 GB | about 370 MB |

These results used parallel PostgreSQL plans and benefited from cached data.
Cold storage, production concurrency, different PostgreSQL settings, or a less
favorable plan can make wall-clock time several times higher. The large buffer
counts show that the queries remain substantial despite their short warm
timings.

Use `technology@lemmy.world` as an initial high-volume community test and
`lemmy.world` as the instance stress test. Benchmark cold, semi-cold, warm, and
concurrent cache-miss behavior before enabling the graphs by default.

### Existing instance-page constraint

The copied test database currently returns the `lemmy.world` instance overview
in about 1.56 seconds when `ENABLE_INSTANCE_CONTENT_COUNTS=false`. With content
counts enabled, the query repeatedly exceeds the 12-second statement timeout.
The expensive portion counts recent posts and comments authored by the 100
users on the current page; the available Lemmy indexes do not provide a
combined creator-and-published lookup for those paths.

That existing timeout should be optimized independently before adding an
instance graph. The graph must not be blamed for, or hide, a timeout already
caused by instance content counts.

### Possible later rollup

If uncached graphs create noticeable production load despite the 30-minute
cache, maintain application-owned daily rollups instead of repeatedly scanning
Lemmy's live vote tables. Keep rollup tables outside Lemmy's schema so Lemmy
upgrades do not need to manage or preserve application-specific objects.

Do not introduce rollups initially. First implement and benchmark the simpler
asynchronous cached queries, because shared overview graphs should have a much
higher cache-hit rate than user-specific graphs.

## Relative SQL cost

The copied test database used during this design discussion contained
approximately:

| Table | Estimated rows |
| --- | ---: |
| `instance` | 4,477 |
| `community` | 776 |
| `person` | 431,396 |
| `post` | 357,037 |
| `post_like` | 32.9 million |
| `comment_like` | 63.8 million |

Both vote tables have an index on `published`, supporting the configured recent
window. The relevant foreign-key lookup paths are also indexed.

For total, upvote, downvote, and latest-vote values, community aggregation is
expected to be comparable to or slightly cheaper than instance aggregation:

- instance aggregation joins recent votes to `person`;
- community aggregation joins recent votes to `post`;
- `post` is slightly smaller than `person` in the copied database; and
- the final community result has fewer groups than the instance result.

The active vote rows inside the configured time window dominate the cost, so
these table estimates are not a substitute for measuring the actual query.

If both views include distinct recent voters, the community query may become
more expensive because users can contribute to many community groups.

## Shared implementation opportunities

The two views can share:

- feature-flag and authorization enforcement;
- recent-window wording and configuration;
- sort-button and pagination behavior;
- numeric and percentage table styles;
- timeout and error handling;
- deterministic ordering conventions; and
- request-level tests for disabled, unauthenticated, unauthorized, empty, and
  paginated results.

Avoid creating a highly generic framework before the first view establishes
the real common behavior. Shared helpers or template fragments can be extracted
when the second view is implemented.

## Performance validation

Prototype both aggregate queries before building the UI. For each query:

1. use a copied production database;
2. apply current statistics with `ANALYZE`;
3. run `EXPLAIN (ANALYZE, BUFFERS, SETTINGS)`;
4. record cold, semi-cold, and warm timings;
5. compare totals with existing instance or community overviews;
6. test the configured vote window and a shorter window;
7. confirm deterministic pagination; and
8. verify that execution remains below the configured overview and Gunicorn
   timeouts.

A single PostgreSQL query may use only one CPU core when its selected plan is
not parallel. Query shape, rows processed, joins, grouping, and cache state are
more useful diagnostics than total available CPU capacity.

If uncached execution is too expensive, consider short-lived caching only after
the query has been simplified and benchmarked. Cache keys must include the vote
window, sort, page, and any active/inactive filter.

## Recommended delivery order

1. Prototype and benchmark both SQL aggregates without UI changes.
2. Decide whether each default list includes only recent activity or all known
   zero-vote rows.
3. Implement the instance list first, because it directly extends the existing
   instance search and establishes the list-view pattern.
4. Verify behavior and cost on large copied data.
5. Reuse the proven pattern for the community list.
6. Add distinct community voters only if its measured cost is acceptable.

Building both views together is estimated at roughly one and a half to three
days, including tests and realistic query validation.

## Decisions required before implementation

- Should zero-vote instances and communities be excluded, included, or exposed
  through an optional filter?
- Is a distinct recent-voter count required on the community list?
- Should domain/community text filtering be available within the list pages?
- Are `/instances` and `/communities` the preferred route names?
- Which sort should be the default?
- Is short-lived caching acceptable if uncached queries exceed the timeout?
- Should instance and community graphs use the proposed 30-minute cache?
- Should the graph feature flags default to enabled or disabled?
- Should these views continue sharing `ENABLE_DOMAIN_SEARCH`, or should a more
  accurately named feature flag replace it in a later configuration change?
