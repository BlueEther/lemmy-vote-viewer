# Database compatibility

Lemmy Vote Viewer reads Lemmy's PostgreSQL schema directly. It does not use a
stable database API, so compatibility depends on the tables, columns, types,
values, and indexes provided by the installed Lemmy release.

Treat every Lemmy upgrade as a potential compatibility change. Test a copied
database before upgrading the production viewer.

## Verified versions

The following combination has been tested with a restored production database:

| Component | Verified version |
| --- | --- |
| Lemmy | 0.19.20 |
| Latest Diesel migration | `20250729152743` |
| PostgreSQL | 16.4 |

This is the verified baseline, not a promise that earlier or later releases
are incompatible. Other PostgreSQL versions may work, but they have not yet
been recorded as tested by this project.

Lemmy 1.0 is not currently verified. It introduces database migrations and
moves the HTTP API from v3 to v4. Database compatibility and authentication
compatibility must both be checked before claiming Lemmy 1.0 support. See
Lemmy's official [1.0 upgrade instructions][lemmy-1.0] and
[API v4 upgrade guide][api-v4].

## Finding the installed versions

The container image is the most reliable source for the installed Lemmy
version. Replace the container name if necessary:

```sh
docker inspect lemmy --format '{{.Config.Image}}'
```

The local instance row normally reports the running Lemmy version as well:

```sql
SELECT domain, software, version
FROM instance
WHERE domain = 'your-instance.example';
```

Record the PostgreSQL version and newest applied Lemmy migrations:

```sql
SHOW server_version;

SELECT version, run_on
FROM __diesel_schema_migrations
ORDER BY version DESC
LIMIT 10;
```

The instance row can be stale, and a container tag such as `latest` does not
identify an exact release. Record both the resolved image and migration state
when testing compatibility.

## Required database schema

[`db-grants.sql`](../db-grants.sql) is the authoritative list of columns the
viewer is permitted to read. The application currently requires:

| Table | Required columns |
| --- | --- |
| `instance` | `id`, `domain` |
| `person` | `id`, `name`, `display_name`, `local`, `actor_id`, `instance_id`, `deleted` |
| `post` | `id`, `name`, `creator_id`, `community_id`, `ap_id`, `local`, `deleted`, `removed` |
| `comment` | `id`, `creator_id`, `post_id`, `content`, `ap_id`, `local`, `deleted`, `removed` |
| `community` | `id`, `name`, `title`, `local`, `actor_id`, `instance_id`, `visibility`, `deleted`, `removed` |
| `post_like` | `post_id`, `person_id`, `score`, `published` |
| `comment_like` | `person_id`, `comment_id`, `post_id`, `score`, `published` |
| `post_aggregates` | `post_id`, `creator_id`, `community_id`, `upvotes`, `downvotes`, `published` |
| `comment_aggregates` | `comment_id`, `upvotes`, `downvotes`, `published` |

The verified schema uses integer IDs, `smallint` vote scores, `bigint`
aggregate counts, and timestamps with time zones for the required `published`
columns. The `community.visibility` column is an enum-like PostgreSQL type with
the value `Public` used for visible communities.

The viewer relies on these data semantics:

- `post_like` and `comment_like` contain the current locally recorded vote
  state, rather than a permanent vote-event audit history;
- positive, negative, and zero scores represent upvotes, downvotes, and
  neutral vote states respectively;
- aggregate rows contain the current received upvote and downvote totals;
- `local`, `actor_id`, `ap_id`, and `instance_id` identify local and federated
  users, communities, posts, and comments;
- deleted and removed flags remain boolean; and
- public communities can be selected with `visibility = 'Public'`.

## Read-only schema preflight

Run this query as a role that can inspect `information_schema.columns`. A
compatible schema returns zero rows. Any returned row names a missing table or
column required by the viewer.

```sql
WITH required(table_name, column_name) AS (
    VALUES
        ('instance', 'id'),
        ('instance', 'domain'),
        ('person', 'id'),
        ('person', 'name'),
        ('person', 'display_name'),
        ('person', 'local'),
        ('person', 'actor_id'),
        ('person', 'instance_id'),
        ('person', 'deleted'),
        ('post', 'id'),
        ('post', 'name'),
        ('post', 'creator_id'),
        ('post', 'community_id'),
        ('post', 'ap_id'),
        ('post', 'local'),
        ('post', 'deleted'),
        ('post', 'removed'),
        ('comment', 'id'),
        ('comment', 'creator_id'),
        ('comment', 'post_id'),
        ('comment', 'content'),
        ('comment', 'ap_id'),
        ('comment', 'local'),
        ('comment', 'deleted'),
        ('comment', 'removed'),
        ('community', 'id'),
        ('community', 'name'),
        ('community', 'title'),
        ('community', 'local'),
        ('community', 'actor_id'),
        ('community', 'instance_id'),
        ('community', 'visibility'),
        ('community', 'deleted'),
        ('community', 'removed'),
        ('post_like', 'post_id'),
        ('post_like', 'person_id'),
        ('post_like', 'score'),
        ('post_like', 'published'),
        ('comment_like', 'person_id'),
        ('comment_like', 'comment_id'),
        ('comment_like', 'post_id'),
        ('comment_like', 'score'),
        ('comment_like', 'published'),
        ('post_aggregates', 'post_id'),
        ('post_aggregates', 'creator_id'),
        ('post_aggregates', 'community_id'),
        ('post_aggregates', 'upvotes'),
        ('post_aggregates', 'downvotes'),
        ('post_aggregates', 'published'),
        ('comment_aggregates', 'comment_id'),
        ('comment_aggregates', 'upvotes'),
        ('comment_aggregates', 'downvotes'),
        ('comment_aggregates', 'published')
)
SELECT r.table_name, r.column_name
FROM required r
LEFT JOIN information_schema.columns c
    ON c.table_schema = 'public'
   AND c.table_name = r.table_name
   AND c.column_name = r.column_name
WHERE c.column_name IS NULL
ORDER BY r.table_name, r.column_name;
```

This preflight confirms names only. It does not prove compatible data types,
values, relationships, indexes, query plans, or HTTP authentication behavior.

After the preflight passes, rerun `db-grants.sql`. It revokes broad table and
column access before granting only the columns listed above. PostgreSQL applies
these changes transactionally, and `ON_ERROR_STOP` causes an incompatible grant
to fail rather than continuing with a partial configuration.

## Changes that can break compatibility

Review Lemmy migrations for the following changes:

- renamed, removed, or relocated required tables or columns;
- ID columns changed from `integer` to another type, because some viewer query
  parameters are explicitly cast to `integer`;
- vote scores or aggregate counts changing type or meaning;
- changes to `community.visibility` or the `Public` value;
- changes to local, ActivityPub, deletion, or removal fields;
- changes to aggregate maintenance or vote federation semantics;
- removed or substantially changed indexes used by vote-history and overview
  queries; and
- removal of Lemmy's v3 `/api/v3/site` endpoint used for optional
  authentication.

A schema can pass the column preflight but still produce incorrect results or
slow queries when one of these semantics changes.

## Testing a new Lemmy release

1. Read the Lemmy release notes, upgrade instructions, and database migrations.
2. Back up production and verify that the backup can be restored. Follow
   Lemmy's official [backup and restore guidance][backup].
3. Restore the backup into an isolated database as described in the README's
   local database testing section. Do not test migrations against production.
4. Run the target Lemmy release against the isolated database so its migrations
   complete, then record the image, PostgreSQL version, and latest Diesel
   migration.
5. Run the read-only schema preflight above and inspect any type or enum changes.
6. Apply the candidate version's `db-grants.sql` to the isolated database.
7. Run the unit tests and start the viewer against the migrated database.
8. Smoke-test user cast, received, and community views; post and comment voter
   pages; ActivityPub URL lookup; community and instance overviews; pagination;
   and every enabled authentication requirement.
9. Compare representative totals with Lemmy and confirm that removed, deleted,
   private, and federated content still behaves as documented.
10. Measure the slow overview and user-history queries with realistic data,
    including large instances and cold-cache runs. A functionally correct query
    can still be incompatible with the configured statement timeout.
11. Add the verified combination and migration ID to this document before
    deploying the release to production.

If any step fails, keep the existing production Lemmy and viewer versions until
the schema, query, grant, or API difference is understood and addressed.

[api-v4]: https://join-lemmy.org/docs/contributors/09-api-v4.html
[backup]: https://join-lemmy.org/docs/administration/backup_and_restore.html
[lemmy-1.0]: https://join-lemmy.org/docs/administration/1.0_upgrade.html
