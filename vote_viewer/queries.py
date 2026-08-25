# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

COMMUNITY_LOOKUP_SQL = """
SELECT c.id, c.name, c.title, c.local, c.actor_id
FROM community c
JOIN instance i ON i.id = c.instance_id
WHERE lower(c.name) = lower(%s)
  AND (
      (%s::text IS NULL AND c.local = true)
      OR i.domain = %s
  )
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
ORDER BY c.local DESC, c.id
LIMIT 1
"""

USER_SUGGESTIONS_SQL = """
SELECT p.id, p.name, p.display_name, p.local, p.actor_id
FROM person p
LEFT JOIN instance i ON i.id = p.instance_id
WHERE p.deleted = false
  AND p.name ILIKE %s ESCAPE '\\'
  AND (
      %s::text IS NULL
      OR (
          p.local = false
          AND i.domain ILIKE %s ESCAPE '\\'
      )
  )
ORDER BY
    CASE WHEN lower(p.name) = lower(%s) THEN 0 ELSE 1 END,
    p.local DESC,
    lower(p.name),
    p.id
LIMIT %s
"""

USER_VOTES_SQL_TEMPLATE = """
WITH filters AS (
    SELECT
        %s::text AS content_type,
        %s::smallint AS score_filter
        {community_parameter}
),
eligible_votes AS MATERIALIZED (
    SELECT
        pl.published AS voted_at,
        'post'::text AS type,
        pl.score,
        pl.post_id,
        NULL::integer AS comment_id
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    JOIN community c ON c.id = p.community_id
    CROSS JOIN filters f
    WHERE pl.person_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'post')
      AND (f.score_filter IS NULL OR pl.score = f.score_filter)
      {post_community_filter}
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT
        cl.published AS voted_at,
        'comment'::text AS type,
        cl.score,
        cl.post_id,
        cl.comment_id
    FROM comment_like cl
    JOIN post p ON p.id = cl.post_id
    JOIN community c ON c.id = p.community_id
    CROSS JOIN filters f
    WHERE cl.person_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'comment')
      AND (f.score_filter IS NULL OR cl.score = f.score_filter)
      {comment_community_filter}
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
),
paged_votes AS MATERIALIZED (
    SELECT *
    FROM eligible_votes
    ORDER BY voted_at {direction}, type, post_id, comment_id
    LIMIT %s OFFSET %s
)
SELECT
    pv.voted_at,
    pv.type,
    pv.score,
    pv.post_id,
    pv.comment_id,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    CASE WHEN pv.type = 'post' THEN NULL
         WHEN p.deleted OR p.removed THEN '[comment on unavailable post]'
         WHEN cm.deleted THEN '[deleted comment]'
         WHEN cm.removed THEN '[removed comment]'
         ELSE cm.content END AS comment_content,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url,
    CASE
        WHEN p.deleted OR p.removed OR author.deleted
          OR (pv.type = 'comment' AND (cm.deleted OR cm.removed))
        THEN NULL
        ELSE author.name
    END AS author_name,
    CASE
        WHEN p.deleted OR p.removed OR author.deleted
          OR (pv.type = 'comment' AND (cm.deleted OR cm.removed))
        THEN NULL
        ELSE author.display_name
    END AS author_display_name,
    author.local AS author_local,
    author.actor_id AS author_url,
    CASE WHEN pv.type = 'post' THEN p.ap_id ELSE cm.ap_id END AS content_url,
    CASE WHEN pv.type = 'post' THEN p.local ELSE cm.local END AS item_local,
    (
        p.deleted OR p.removed
        OR (pv.type = 'comment' AND (cm.deleted OR cm.removed))
    ) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden
FROM paged_votes pv
JOIN post p ON p.id = pv.post_id
JOIN community c ON c.id = p.community_id
LEFT JOIN comment cm
    ON pv.type = 'comment'
   AND cm.id = pv.comment_id
JOIN person author
    ON author.id = CASE
        WHEN pv.type = 'post' THEN p.creator_id
        ELSE cm.creator_id
    END
ORDER BY pv.voted_at {direction}, pv.type, pv.post_id, pv.comment_id
"""

USER_VOTES_SQL = USER_VOTES_SQL_TEMPLATE.format(
    community_parameter="",
    post_community_filter="",
    comment_community_filter="",
    direction="DESC",
)

USER_VOTES_BY_COMMUNITY_SQL = USER_VOTES_SQL_TEMPLATE.format(
    community_parameter=", %s::integer AS community_id",
    post_community_filter="AND c.id = f.community_id",
    comment_community_filter="AND c.id = f.community_id",
    direction="DESC",
)

USER_VOTES_OLDEST_SQL = USER_VOTES_SQL_TEMPLATE.format(
    community_parameter="",
    post_community_filter="",
    comment_community_filter="",
    direction="ASC",
)

USER_VOTES_OLDEST_BY_COMMUNITY_SQL = USER_VOTES_SQL_TEMPLATE.format(
    community_parameter=", %s::integer AS community_id",
    post_community_filter="AND c.id = f.community_id",
    comment_community_filter="AND c.id = f.community_id",
    direction="ASC",
)

USER_SUMMARY_SQL = """
WITH votes AS (
    SELECT pl.score, 'post'::text AS type, c.id AS community_id
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    JOIN community c ON c.id = p.community_id
    WHERE pl.person_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT cl.score, 'comment'::text AS type, c.id AS community_id
    FROM comment_like cl
    JOIN post p ON p.id = cl.post_id
    JOIN community c ON c.id = p.community_id
    WHERE cl.person_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
)
SELECT
    COUNT(*)::integer AS total,
    COUNT(*) FILTER (WHERE score > 0)::integer AS up,
    COUNT(*) FILTER (WHERE score < 0)::integer AS down,
    COUNT(*) FILTER (WHERE score = 0)::integer AS neutral,
    COUNT(*) FILTER (WHERE type = 'post')::integer AS posts,
    COUNT(*) FILTER (WHERE type = 'comment')::integer AS comments,
    COUNT(*) FILTER (
        WHERE (%s::text = 'all' OR type = %s::text)
          AND (%s::smallint IS NULL OR score = %s::smallint)
          AND (%s::integer IS NULL OR community_id = %s::integer)
    )::integer AS filtered_total
FROM votes
"""

USER_RECEIVED_SUMMARY_SQL = """
WITH received_by_type AS (
    SELECT
        'post'::text AS type,
        COALESCE(SUM(pa.upvotes), 0)::bigint AS up,
        COALESCE(SUM(pa.downvotes), 0)::bigint AS down,
        COUNT(*) FILTER (
            WHERE pa.upvotes + pa.downvotes > 0
        )::bigint AS items,
        COUNT(*) FILTER (
            WHERE pa.upvotes + pa.downvotes > 0
              AND (%s::integer IS NULL OR pa.community_id = %s::integer)
        )::bigint AS filtered_items
    FROM post_aggregates pa
    JOIN community c ON c.id = pa.community_id
    WHERE pa.creator_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT
        'comment'::text AS type,
        COALESCE(SUM(ca.upvotes), 0)::bigint AS up,
        COALESCE(SUM(ca.downvotes), 0)::bigint AS down,
        COUNT(*) FILTER (
            WHERE ca.upvotes + ca.downvotes > 0
        )::bigint AS items,
        COUNT(*) FILTER (
            WHERE ca.upvotes + ca.downvotes > 0
              AND (%s::integer IS NULL OR p.community_id = %s::integer)
        )::bigint AS filtered_items
    FROM comment cm
    JOIN comment_aggregates ca ON ca.comment_id = cm.id
    JOIN post p ON p.id = cm.post_id
    JOIN community c ON c.id = p.community_id
    WHERE cm.creator_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
)
SELECT
    COALESCE(SUM(up + down), 0)::bigint AS total,
    COALESCE(SUM(up), 0)::bigint AS up,
    COALESCE(SUM(down), 0)::bigint AS down,
    0::bigint AS neutral,
    COALESCE(SUM(up + down) FILTER (WHERE type = 'post'), 0)::bigint AS posts,
    COALESCE(SUM(up + down) FILTER (WHERE type = 'comment'), 0)::bigint AS comments,
    COALESCE(SUM(items), 0)::bigint AS items,
    COALESCE(SUM(items) FILTER (WHERE type = 'post'), 0)::bigint AS post_items,
    COALESCE(SUM(items) FILTER (WHERE type = 'comment'), 0)::bigint AS comment_items,
    COALESCE(SUM(filtered_items), 0)::bigint AS filtered_items,
    COALESCE(SUM(filtered_items) FILTER (WHERE type = 'post'), 0)::bigint AS post_filtered_items,
    COALESCE(SUM(filtered_items) FILTER (WHERE type = 'comment'), 0)::bigint AS comment_filtered_items
FROM received_by_type
"""

USER_RECEIVED_ITEMS_SQL_TEMPLATE = """
WITH filters AS (
    SELECT
        %s::text AS content_type,
        %s::text AS received_sort
        {community_parameter}
),
eligible_items AS MATERIALIZED (
    SELECT
        pa.published AS published_at,
        'post'::text AS type,
        pa.upvotes,
        pa.downvotes,
        pa.post_id,
        NULL::integer AS comment_id,
        CASE f.received_sort
            WHEN 'top' THEN pa.upvotes - pa.downvotes
            WHEN 'bottom' THEN pa.downvotes - pa.upvotes
            WHEN 'oldest' THEN -EXTRACT(EPOCH FROM pa.published)
            ELSE EXTRACT(EPOCH FROM pa.published)
        END AS sort_value
    FROM post_aggregates pa
    JOIN community c ON c.id = pa.community_id
    CROSS JOIN filters f
    WHERE pa.creator_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'post')
      {post_community_filter}
      AND pa.upvotes + pa.downvotes > 0
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT
        ca.published AS published_at,
        'comment'::text AS type,
        ca.upvotes,
        ca.downvotes,
        cm.post_id,
        ca.comment_id,
        CASE f.received_sort
            WHEN 'top' THEN ca.upvotes - ca.downvotes
            WHEN 'bottom' THEN ca.downvotes - ca.upvotes
            WHEN 'oldest' THEN -EXTRACT(EPOCH FROM ca.published)
            ELSE EXTRACT(EPOCH FROM ca.published)
        END AS sort_value
    FROM comment cm
    JOIN comment_aggregates ca ON ca.comment_id = cm.id
    JOIN post p ON p.id = cm.post_id
    JOIN community c ON c.id = p.community_id
    CROSS JOIN filters f
    WHERE cm.creator_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'comment')
      {comment_community_filter}
      AND ca.upvotes + ca.downvotes > 0
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
),
paged_items AS MATERIALIZED (
    SELECT *
    FROM eligible_items
    ORDER BY sort_value DESC, published_at DESC, type, post_id, comment_id
    LIMIT %s OFFSET %s
)
SELECT
    pi.published_at,
    pi.type,
    (pi.upvotes + pi.downvotes)::bigint AS total,
    pi.upvotes,
    pi.downvotes,
    pi.post_id,
    pi.comment_id,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    CASE WHEN pi.type = 'post' THEN NULL
         WHEN p.deleted OR p.removed THEN '[comment on unavailable post]'
         WHEN cm.deleted THEN '[deleted comment]'
         WHEN cm.removed THEN '[removed comment]'
         ELSE cm.content END AS comment_content,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url,
    CASE WHEN pi.type = 'post' THEN p.ap_id ELSE cm.ap_id END AS content_url,
    CASE WHEN pi.type = 'post' THEN p.local ELSE cm.local END AS item_local,
    (
        p.deleted OR p.removed
        OR (pi.type = 'comment' AND (cm.deleted OR cm.removed))
    ) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden
FROM paged_items pi
JOIN post p ON p.id = pi.post_id
JOIN community c ON c.id = p.community_id
LEFT JOIN comment cm
    ON pi.type = 'comment'
   AND cm.id = pi.comment_id
ORDER BY pi.sort_value DESC, pi.published_at DESC, pi.type, pi.post_id, pi.comment_id
"""

USER_RECEIVED_ITEMS_SQL = USER_RECEIVED_ITEMS_SQL_TEMPLATE.format(
    community_parameter="",
    post_community_filter="",
    comment_community_filter="",
)

USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL = USER_RECEIVED_ITEMS_SQL_TEMPLATE.format(
    community_parameter=", %s::integer AS community_id",
    post_community_filter="AND c.id = f.community_id",
    comment_community_filter="AND c.id = f.community_id",
)

COMMUNITY_SUMMARY_SORTS = {
    "total": "(cs.cast_total + cs.received_total) DESC, lower(c.name), cs.community_id",
    "cast": "cs.cast_total DESC, lower(c.name), cs.community_id",
    "received": "cs.received_total DESC, lower(c.name), cs.community_id",
    "down": "(cs.cast_down + cs.received_down) DESC, lower(c.name), cs.community_id",
    "name": "lower(c.name), cs.community_id",
}

USER_COMMUNITY_SUMMARY_SQL = """
WITH cast_by_type AS (
    SELECT
        p.community_id,
        COUNT(*)::bigint AS total,
        COUNT(*) FILTER (WHERE pl.score > 0)::bigint AS post_up,
        COUNT(*) FILTER (WHERE pl.score < 0)::bigint AS post_down,
        0::bigint AS comment_up,
        0::bigint AS comment_down,
        COUNT(*) FILTER (WHERE pl.score = 0)::bigint AS neutral
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    WHERE pl.person_id = %s
    GROUP BY p.community_id

    UNION ALL

    SELECT
        p.community_id,
        COUNT(*)::bigint AS total,
        0::bigint AS post_up,
        0::bigint AS post_down,
        COUNT(*) FILTER (WHERE cl.score > 0)::bigint AS comment_up,
        COUNT(*) FILTER (WHERE cl.score < 0)::bigint AS comment_down,
        COUNT(*) FILTER (WHERE cl.score = 0)::bigint AS neutral
    FROM comment_like cl
    JOIN post p ON p.id = cl.post_id
    WHERE cl.person_id = %s
    GROUP BY p.community_id
),
cast_by_community AS (
    SELECT
        community_id,
        SUM(total)::bigint AS total,
        SUM(post_up)::bigint AS post_up,
        SUM(post_down)::bigint AS post_down,
        SUM(comment_up)::bigint AS comment_up,
        SUM(comment_down)::bigint AS comment_down,
        SUM(neutral)::bigint AS neutral
    FROM cast_by_type
    GROUP BY community_id
),
received_by_type AS (
    SELECT
        pa.community_id,
        SUM(pa.upvotes + pa.downvotes)::bigint AS total,
        SUM(pa.upvotes)::bigint AS post_up,
        SUM(pa.downvotes)::bigint AS post_down,
        0::bigint AS comment_up,
        0::bigint AS comment_down
    FROM post_aggregates pa
    WHERE pa.creator_id = %s
      AND pa.upvotes + pa.downvotes > 0
    GROUP BY pa.community_id

    UNION ALL

    SELECT
        p.community_id,
        SUM(ca.upvotes + ca.downvotes)::bigint AS total,
        0::bigint AS post_up,
        0::bigint AS post_down,
        SUM(ca.upvotes)::bigint AS comment_up,
        SUM(ca.downvotes)::bigint AS comment_down
    FROM comment cm
    JOIN comment_aggregates ca ON ca.comment_id = cm.id
    JOIN post p ON p.id = cm.post_id
    WHERE cm.creator_id = %s
      AND ca.upvotes + ca.downvotes > 0
    GROUP BY p.community_id
),
received_by_community AS (
    SELECT
        community_id,
        SUM(total)::bigint AS total,
        SUM(post_up)::bigint AS post_up,
        SUM(post_down)::bigint AS post_down,
        SUM(comment_up)::bigint AS comment_up,
        SUM(comment_down)::bigint AS comment_down
    FROM received_by_type
    GROUP BY community_id
),
community_summary AS MATERIALIZED (
    SELECT
        COALESCE(cv.community_id, rv.community_id) AS community_id,
        COALESCE(cv.total, 0)::bigint AS cast_total,
        COALESCE(cv.post_up, 0)::bigint AS cast_post_up,
        COALESCE(cv.post_down, 0)::bigint AS cast_post_down,
        COALESCE(cv.comment_up, 0)::bigint AS cast_comment_up,
        COALESCE(cv.comment_down, 0)::bigint AS cast_comment_down,
        COALESCE(cv.neutral, 0)::bigint AS cast_neutral,
        COALESCE(rv.total, 0)::bigint AS received_total,
        COALESCE(rv.post_up, 0)::bigint AS received_post_up,
        COALESCE(rv.post_down, 0)::bigint AS received_post_down,
        COALESCE(rv.comment_up, 0)::bigint AS received_comment_up,
        COALESCE(rv.comment_down, 0)::bigint AS received_comment_down,
        COALESCE(rv.post_down, 0)::bigint
          + COALESCE(rv.comment_down, 0)::bigint AS received_down,
        COALESCE(cv.post_down, 0)::bigint
          + COALESCE(cv.comment_down, 0)::bigint AS cast_down
    FROM cast_by_community cv
    FULL OUTER JOIN received_by_community rv
      ON rv.community_id = cv.community_id
)
SELECT
    cs.*,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url,
    COUNT(*) OVER ()::integer AS community_count
FROM community_summary cs
JOIN community c ON c.id = cs.community_id
WHERE c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
ORDER BY {order_by}
LIMIT %s OFFSET %s
"""

COMMUNITY_OVERVIEW_SQL = """
WITH source_votes AS (
    SELECT pl.person_id, pl.score, pl.published AS voted_at
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    WHERE p.community_id = %s
      AND pl.published >= CURRENT_TIMESTAMP - INTERVAL '{vote_window_days} days'

    UNION ALL

    SELECT cl.person_id, cl.score, cl.published AS voted_at
    FROM comment_like cl
    JOIN post p ON p.id = cl.post_id
    WHERE p.community_id = %s
      AND cl.published >= CURRENT_TIMESTAMP - INTERVAL '{vote_window_days} days'
),
vote_totals AS MATERIALIZED (
    SELECT
        sv.person_id,
        COUNT(*)::bigint AS total,
        COUNT(*) FILTER (WHERE sv.score > 0)::bigint AS up,
        COUNT(*) FILTER (WHERE sv.score < 0)::bigint AS down,
        COUNT(*) FILTER (WHERE sv.score = 0)::bigint AS neutral,
        MAX(sv.voted_at) AS latest_vote
    FROM source_votes sv
    GROUP BY sv.person_id
),
active_voters AS MATERIALIZED (
    SELECT
        pe.id,
        pe.name,
        pe.display_name,
        pe.local,
        pe.actor_id,
        vt.total,
        vt.up,
        vt.down,
        vt.neutral,
        vt.latest_vote
    FROM vote_totals vt
    JOIN person pe ON pe.id = vt.person_id
    WHERE pe.deleted = false
),
summary AS (
    SELECT
        COUNT(*) AS voting_users,
        COALESCE(SUM(total), 0)::bigint AS total,
        COALESCE(SUM(up), 0)::bigint AS up,
        COALESCE(SUM(down), 0)::bigint AS down,
        COALESCE(SUM(neutral), 0)::bigint AS neutral
    FROM active_voters
),
ranked_users AS (
    SELECT
        av.*,
        ROW_NUMBER() OVER (ORDER BY {order_by}) AS sort_position
    FROM active_voters av
),
paged_users AS (
    SELECT *
    FROM ranked_users
    WHERE sort_position > %s
      AND sort_position <= %s
)
SELECT
    summary.voting_users,
    summary.total AS summary_total,
    summary.up AS summary_up,
    summary.down AS summary_down,
    summary.neutral AS summary_neutral,
    pu.id,
    pu.name,
    pu.display_name,
    pu.local,
    pu.actor_id,
    pu.total,
    pu.up,
    pu.down,
    pu.neutral,
    pu.latest_vote,
    pu.sort_position
FROM summary
LEFT JOIN paged_users pu ON true
ORDER BY pu.sort_position
"""

ITEM_BY_AP_ID_SQL = """
SELECT 'post'::text AS kind, p.id AS item_id
FROM post p
JOIN community c ON c.id = p.community_id
WHERE p.ap_id IN (%s, %s)
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false

UNION ALL

SELECT 'comment'::text AS kind, cm.id AS item_id
FROM comment cm
JOIN post p ON p.id = cm.post_id
JOIN community c ON c.id = p.community_id
WHERE cm.ap_id IN (%s, %s)
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
LIMIT 1
"""

INSTANCE_LOOKUP_SQL = """
SELECT id, domain
FROM instance
WHERE domain = %s
LIMIT 1
"""

INSTANCE_OVERVIEW_SQL = """
WITH target_instance AS MATERIALIZED (
    SELECT %s::integer AS id
),
source_votes AS (
    SELECT pl.person_id, pl.score, pl.published AS voted_at
    FROM post_like pl
    JOIN person pe ON pe.id = pl.person_id
    WHERE pe.instance_id = (SELECT id FROM target_instance)
      AND pe.deleted = false
      AND pl.published >= CURRENT_TIMESTAMP - INTERVAL '{vote_window_days} days'

    UNION ALL

    SELECT cl.person_id, cl.score, cl.published AS voted_at
    FROM comment_like cl
    JOIN person pe ON pe.id = cl.person_id
    WHERE pe.instance_id = (SELECT id FROM target_instance)
      AND pe.deleted = false
      AND cl.published >= CURRENT_TIMESTAMP - INTERVAL '{vote_window_days} days'
),
vote_totals AS MATERIALIZED (
    SELECT
        person_id,
        COUNT(*)::bigint AS total,
        COUNT(*) FILTER (WHERE score > 0)::bigint AS up,
        COUNT(*) FILTER (WHERE score < 0)::bigint AS down,
        COUNT(*) FILTER (WHERE score = 0)::bigint AS neutral,
        MAX(voted_at) AS latest_vote
    FROM source_votes
    GROUP BY person_id
),
summary AS (
    SELECT
        (
            SELECT COUNT(*)
            FROM person pe
            WHERE pe.instance_id = (SELECT id FROM target_instance)
              AND pe.deleted = false
        ) AS known_users,
        COUNT(*) AS voting_users,
        COALESCE(SUM(total), 0)::bigint AS total,
        COALESCE(SUM(up), 0)::bigint AS up,
        COALESCE(SUM(down), 0)::bigint AS down,
        COALESCE(SUM(neutral), 0)::bigint AS neutral
    FROM vote_totals
),
ranked_users AS (
    SELECT
        pe.id,
        pe.name,
        pe.display_name,
        pe.local,
        pe.actor_id,
        vt.total,
        vt.up,
        vt.down,
        vt.neutral,
        vt.latest_vote,
        ROW_NUMBER() OVER (ORDER BY {order_by}) AS sort_position
    FROM vote_totals vt
    JOIN person pe ON pe.id = vt.person_id
),
paged_users AS (
    SELECT *
    FROM ranked_users
    WHERE sort_position > %s
      AND sort_position <= %s
)
SELECT
    summary.known_users,
    summary.voting_users,
    summary.total AS summary_total,
    summary.up AS summary_up,
    summary.down AS summary_down,
    summary.neutral AS summary_neutral,
    pu.id,
    pu.name,
    pu.display_name,
    pu.local,
    pu.actor_id,
    pu.total,
    pu.up,
    pu.down,
    pu.neutral,
    pu.latest_vote,
    pu.sort_position
FROM summary
LEFT JOIN paged_users pu ON true
ORDER BY pu.sort_position
"""

INSTANCE_SORTS = {
    "total": "vt.total DESC, lower(pe.name), pe.id",
    "down": "vt.down DESC, vt.total DESC, lower(pe.name), pe.id",
    "down_ratio": (
        "CASE WHEN vt.total >= 10 "
        "THEN vt.down::numeric / vt.total ELSE -1 END DESC, "
        "vt.total DESC, lower(pe.name), pe.id"
    ),
    "up": "vt.up DESC, vt.total DESC, lower(pe.name), pe.id",
    "recent": "vt.latest_vote DESC, lower(pe.name), pe.id",
    "username": "lower(pe.name), pe.id",
}

COMMUNITY_OVERVIEW_SORTS = {
    "total": "av.total DESC, lower(av.name), av.id",
    "down": "av.down DESC, av.total DESC, lower(av.name), av.id",
    "down_ratio": (
        "CASE WHEN av.total >= 10 "
        "THEN av.down::numeric / av.total ELSE -1 END DESC, "
        "av.total DESC, lower(av.name), av.id"
    ),
    "up": "av.up DESC, av.total DESC, lower(av.name), av.id",
    "recent": "av.latest_vote DESC, lower(av.name), av.id",
    "username": "lower(av.name), av.id",
}

POST_ITEM_SQL = """
SELECT
    p.id AS post_id,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    p.ap_id AS content_url,
    p.local AS item_local,
    (p.deleted OR p.removed) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url
FROM post p
JOIN community c ON c.id = p.community_id
WHERE p.id = %s
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
LIMIT 1
"""

COMMENT_ITEM_SQL = """
SELECT
    cm.id AS comment_id,
    CASE WHEN p.deleted OR p.removed THEN '[comment on unavailable post]'
         WHEN cm.deleted THEN '[deleted comment]'
         WHEN cm.removed THEN '[removed comment]'
         ELSE cm.content END AS comment_content,
    cm.ap_id AS content_url,
    cm.local AS item_local,
    (p.deleted OR p.removed OR cm.deleted OR cm.removed) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden,
    p.id AS post_id,
    p.ap_id AS post_url,
    p.local AS post_local,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url
FROM comment cm
JOIN post p ON p.id = cm.post_id
JOIN community c ON c.id = p.community_id
WHERE cm.id = %s
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
LIMIT 1
"""

POST_VOTERS_SQL = """
SELECT
    pl.published AS voted_at,
    pl.score,
    voter.id AS voter_id,
    voter.name AS voter_name,
    voter.display_name AS voter_display_name,
    voter.local AS voter_local,
    voter.actor_id AS voter_url
FROM post_like pl
JOIN person voter ON voter.id = pl.person_id
WHERE pl.post_id = %s
  AND voter.deleted = false
ORDER BY pl.score DESC, lower(voter.name), voter.id
LIMIT %s OFFSET %s
"""

COMMENT_VOTERS_SQL = """
SELECT
    cl.published AS voted_at,
    cl.score,
    voter.id AS voter_id,
    voter.name AS voter_name,
    voter.display_name AS voter_display_name,
    voter.local AS voter_local,
    voter.actor_id AS voter_url
FROM comment_like cl
JOIN person voter ON voter.id = cl.person_id
WHERE cl.comment_id = %s
  AND voter.deleted = false
ORDER BY cl.score DESC, lower(voter.name), voter.id
LIMIT %s OFFSET %s
"""

POST_VOTER_SUMMARY_SQL = """
SELECT
    COUNT(*)::integer AS total,
    COUNT(*) FILTER (WHERE pl.score > 0)::integer AS up,
    COUNT(*) FILTER (WHERE pl.score < 0)::integer AS down,
    COUNT(*) FILTER (WHERE pl.score = 0)::integer AS neutral
FROM post_like pl
JOIN person voter ON voter.id = pl.person_id
WHERE pl.post_id = %s
  AND voter.deleted = false
"""

COMMENT_VOTER_SUMMARY_SQL = """
SELECT
    COUNT(*)::integer AS total,
    COUNT(*) FILTER (WHERE cl.score > 0)::integer AS up,
    COUNT(*) FILTER (WHERE cl.score < 0)::integer AS down,
    COUNT(*) FILTER (WHERE cl.score = 0)::integer AS neutral
FROM comment_like cl
JOIN person voter ON voter.id = cl.person_id
WHERE cl.comment_id = %s
  AND voter.deleted = false
"""
