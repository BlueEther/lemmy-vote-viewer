# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from .links import (
    actor_domain,
    build_community_overview_url,
    build_index_url,
    build_item_url,
    like_prefix_pattern,
    local_community_path,
    local_profile_path,
    make_handle,
    parse_community_handle,
    parse_item_search,
    parse_user_suggestion_input,
    remote_profile_url,
    safe_http_url,
    vote_history_path,
)
from .queries import COMMUNITY_LOOKUP_SQL, ITEM_BY_AP_ID_SQL, USER_SUGGESTIONS_SQL


def resolve_community(cur, value):
    parsed = parse_community_handle(value)
    if not parsed:
        return None, "invalid"

    name, domain = parsed
    cur.execute(COMMUNITY_LOOKUP_SQL, (name, domain, domain))
    community = cur.fetchone()
    if not community:
        return None, "not_found"

    community = dict(community)
    community_domain = actor_domain(community["actor_id"])
    community["handle"] = (
        f"!{community['name']}"
        if community["local"] or not community_domain
        else f"!{community['name']}@{community_domain}"
    )
    return community, None


def resolve_user(cur, username):
    username = username.strip()
    if username.startswith("@"):
        username = username[1:]
    if not username or len(username) > 512:
        return None

    if "@" in username:
        name, domain = username.rsplit("@", 1)
        name = name.strip()
        domain = domain.strip().lower().rstrip(".")
        if not name or not domain or "/" in domain or len(name) > 255:
            return None

        cur.execute(
            """
            SELECT
                p.id,
                p.name,
                p.display_name,
                p.local,
                p.actor_id,
                p.instance_id,
                p.deleted,
                (
                    SELECT COUNT(*)::bigint
                    FROM post authored_post
                    WHERE authored_post.creator_id = p.id
                ) AS post_count,
                (
                    SELECT COUNT(*)::bigint
                    FROM comment authored_comment
                    WHERE authored_comment.creator_id = p.id
                ) AS comment_count
            FROM person p
            WHERE lower(p.name) = lower(%s)
              AND p.local = false
              AND p.deleted = false
            """,
            (name,),
        )
        for row in cur.fetchall():
            if actor_domain(row["actor_id"]) == domain:
                row["instance_domain"] = domain
                row["handle"] = make_handle(row["name"], row["local"], row["actor_id"])
                row["profile_path"] = local_profile_path(row["handle"])
                return row
        return None

    cur.execute(
        """
        SELECT
            p.id,
            p.name,
            p.display_name,
            p.local,
            p.actor_id,
            p.instance_id,
            p.deleted,
            (
                SELECT COUNT(*)::bigint
                FROM post authored_post
                WHERE authored_post.creator_id = p.id
            ) AS post_count,
            (
                SELECT COUNT(*)::bigint
                FROM comment authored_comment
                WHERE authored_comment.creator_id = p.id
            ) AS comment_count
        FROM person p
        WHERE lower(p.name) = lower(%s)
          AND p.local = true
          AND p.deleted = false
        LIMIT 1
        """,
        (username,),
    )
    row = cur.fetchone()
    if row:
        row["instance_domain"] = actor_domain(row["actor_id"])
        row["handle"] = make_handle(row["name"], row["local"], row["actor_id"])
        row["profile_path"] = local_profile_path(row["handle"])
    return row


def find_user_suggestions(
    cur, username, community=None, limit=8, app_prefix=""
):
    parsed = parse_user_suggestion_input(username)
    if not parsed:
        return []
    name_prefix, domain_prefix = parsed
    name_pattern = like_prefix_pattern(name_prefix)
    domain_pattern = like_prefix_pattern(domain_prefix) if domain_prefix is not None else None

    cur.execute(
        USER_SUGGESTIONS_SQL,
        (
            name_pattern,
            domain_prefix,
            domain_pattern,
            name_prefix,
            limit,
        ),
    )

    suggestions = []
    for row in cur.fetchall():
        handle = make_handle(row["name"], row["local"], row["actor_id"])
        if not handle:
            continue
        suggestions.append(
            {
                "display_name": row["display_name"] or row["name"],
                "handle": handle,
                "vote_path": build_index_url(
                    handle,
                    community=community,
                    app_prefix=app_prefix,
                ),
            }
        )
    return suggestions


def enrich_user_vote(row, app_prefix=""):
    row = dict(row)
    community_domain = actor_domain(row["community_url"])
    row["community_display"] = (
        f"!{row['community_name']}"
        if row["community_local"] or not community_domain
        else f"!{row['community_name']}@{community_domain}"
    )
    row["remote_url"] = None
    if not row["item_local"] and not row["content_hidden"]:
        row["remote_url"] = safe_http_url(row["content_url"])

    if row["type"] == "post":
        row["item_vote_path"] = build_item_url(
            "post", row["post_id"], app_prefix=app_prefix
        )
        row["item_local_path"] = f"/post/{row['post_id']}"
    else:
        row["item_vote_path"] = build_item_url(
            "comment", row["comment_id"], app_prefix=app_prefix
        )
        row["item_local_path"] = f"/comment/{row['comment_id']}"

    if row["author_name"]:
        handle = make_handle(row["author_name"], row["author_local"], row["author_url"])
        row["author_handle"] = handle
        row["author_profile_path"] = local_profile_path(handle)
        row["author_vote_path"] = vote_history_path(handle, app_prefix)
        row["author_remote_url"] = remote_profile_url(
            row["author_local"], row["author_url"]
        )
    else:
        row["author_handle"] = None
        row["author_profile_path"] = None
        row["author_vote_path"] = None
        row["author_remote_url"] = None
    return row


def enrich_item(item, app_prefix=""):
    item = dict(item)
    community_domain = actor_domain(item["community_url"])
    item["community_display"] = (
        f"!{item['community_name']}"
        if item["community_local"] or not community_domain
        else f"!{item['community_name']}@{community_domain}"
    )
    item["community_overview_path"] = build_community_overview_url(
        item["community_display"], app_prefix=app_prefix
    )
    item["community_local_path"] = local_community_path(
        item["community_display"]
    )
    item["community_remote_url"] = (
        None
        if item["community_local"]
        else safe_http_url(item["community_url"])
    )
    item["remote_url"] = None
    if not item["item_local"] and not item["content_hidden"]:
        item["remote_url"] = safe_http_url(item["content_url"])
    if item.get("type") == "post":
        item["item_vote_path"] = build_item_url(
            "post", item["post_id"], app_prefix=app_prefix
        )
        item["item_local_path"] = f"/post/{item['post_id']}"
    elif item.get("type") == "comment":
        item["item_vote_path"] = build_item_url(
            "comment", item["comment_id"], app_prefix=app_prefix
        )
        item["item_local_path"] = f"/comment/{item['comment_id']}"
    item["post_remote_url"] = None
    if (
        item.get("post_local") is False
        and not item.get("post_hidden", False)
    ):
        item["post_remote_url"] = safe_http_url(item.get("post_url"))
    return item


def enrich_community_summary(row, user_handle, app_prefix=""):
    row = dict(row)
    community_domain = actor_domain(row["community_url"])
    row["community_display"] = (
        f"!{row['community_name']}"
        if row["community_local"] or not community_domain
        else f"!{row['community_name']}@{community_domain}"
    )
    row["community_local_path"] = local_community_path(
        row["community_display"]
    )
    row["community_remote_url"] = (
        None
        if row["community_local"]
        else safe_http_url(row["community_url"])
    )
    row["overview_path"] = build_community_overview_url(
        row["community_display"], app_prefix=app_prefix
    )
    row["cast_path"] = build_index_url(
        user_handle,
        history_view="cast",
        community=row["community_display"],
        app_prefix=app_prefix,
    )
    row["received_path"] = build_index_url(
        user_handle,
        history_view="received",
        community=row["community_display"],
        app_prefix=app_prefix,
    )
    return row


def enrich_voter(row, app_prefix=""):
    row = dict(row)
    handle = make_handle(row["voter_name"], row["voter_local"], row["voter_url"])
    row["voter_handle"] = handle
    row["voter_display"] = f"@{handle}" if handle else ""
    row["voter_profile_path"] = local_profile_path(handle)
    row["voter_vote_path"] = vote_history_path(handle, app_prefix)
    row["voter_remote_url"] = remote_profile_url(
        row["voter_local"], row["voter_url"]
    )
    return row


def enrich_instance_user(row, app_prefix=""):
    row = dict(row)
    handle = make_handle(row["name"], row["local"], row["actor_id"])
    row["handle"] = handle
    row["profile_path"] = local_profile_path(handle)
    row["remote_url"] = remote_profile_url(row["local"], row["actor_id"])
    row["vote_path"] = vote_history_path(handle, app_prefix)
    row["down_percent"] = (row["down"] / row["total"] * 100) if row["total"] else 0
    return row


def enrich_community_user(row, community_handle, app_prefix=""):
    row = dict(row)
    handle = make_handle(row["name"], row["local"], row["actor_id"])
    row["handle"] = handle
    row["profile_path"] = local_profile_path(handle)
    row["remote_url"] = remote_profile_url(row["local"], row["actor_id"])
    row["vote_path"] = build_index_url(
        handle,
        community=community_handle,
        app_prefix=app_prefix,
    )
    row["down_percent"] = (
        row["down"] / row["total"] * 100 if row["total"] else 0
    )
    return row


def resolve_item_search(item_query, lemmy_base_url, database):
    parsed = parse_item_search(item_query, lemmy_base_url)
    if not parsed:
        return None, "invalid"

    if parsed["local_item"]:
        return parsed["local_item"], None

    ap_urls = parsed["ap_urls"]
    with database() as conn:
        with conn.cursor() as cur:
            cur.execute(ITEM_BY_AP_ID_SQL, (*ap_urls, *ap_urls))
            row = cur.fetchone()
    if not row:
        return None, "not_found"
    return (row["kind"], row["item_id"]), None
