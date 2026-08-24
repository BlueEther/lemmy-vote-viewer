# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from flask import Blueprint, abort, redirect, request

from .. import application as legacy


blueprint = Blueprint("overviews", __name__)


@blueprint.route("/instance/<domain>")
def instance_overview(domain):
    if not legacy.ENABLE_DOMAIN_SEARCH:
        abort(404)
    legacy.enforce_access(legacy.AUTH_INSTANCE_REQUIRE)

    normalized_domain = legacy.normalize_instance_domain(domain)
    if not normalized_domain:
        abort(404)

    sort = request.args.get("sort", "total")
    if sort not in legacy.INSTANCE_SORTS:
        sort = "total"
    requested_page = legacy.parse_page()

    with legacy.db() as conn:
        with conn.cursor() as cur:
            cur.execute(legacy.INSTANCE_LOOKUP_SQL, (normalized_domain,))
            instance = cur.fetchone()
            if not instance:
                abort(404)

            canonical_domain = legacy.normalize_instance_domain(instance["domain"])
            if not canonical_domain:
                abort(404)

            requested_offset = (requested_page - 1) * legacy.PAGE_SIZE
            overview_sql = legacy.INSTANCE_OVERVIEW_SQL.format(
                order_by=legacy.INSTANCE_SORTS[sort],
                vote_window_days=legacy.INSTANCE_VOTE_WINDOW_DAYS,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{legacy.INSTANCE_QUERY_TIMEOUT_SECONDS}s",),
            )
            cur.execute(
                overview_sql,
                (
                    instance["id"],
                    requested_offset,
                    requested_offset + legacy.PAGE_SIZE,
                ),
            )
            result_rows = cur.fetchall()
            overview = result_rows[0]
            summary = {
                "known_users": overview["known_users"],
                "voting_users": overview["voting_users"],
                "total": overview["summary_total"],
                "up": overview["summary_up"],
                "down": overview["summary_down"],
                "neutral": overview["summary_neutral"],
            }
            pagination = legacy.make_pagination(summary["voting_users"], requested_page)
            if pagination["page"] != requested_page:
                return redirect(
                    legacy.build_instance_url(canonical_domain, sort, pagination["page"])
                )
            rows = [
                legacy.enrich_instance_user(row)
                for row in result_rows
                if row["id"] is not None
            ]

    sort_urls = {
        key: legacy.build_instance_url(canonical_domain, key)
        for key in legacy.INSTANCE_SORTS
    }
    if pagination["has_prev"]:
        pagination["prev_url"] = legacy.build_instance_url(
            canonical_domain, sort, pagination["prev_page"]
        )
    if pagination["has_next"]:
        pagination["next_url"] = legacy.build_instance_url(
            canonical_domain, sort, pagination["next_page"]
        )

    return legacy.render_template(
        "instance.html",
        instance=instance,
        domain=canonical_domain,
        summary=summary,
        rows=rows,
        sort=sort,
        sort_urls=sort_urls,
        pagination=pagination,
        vote_window_days=legacy.INSTANCE_VOTE_WINDOW_DAYS,
    )



@blueprint.route("/community/<community_handle>")
def community_overview(community_handle):
    if not legacy.ENABLE_DOMAIN_SEARCH:
        abort(404)
    legacy.enforce_access(legacy.AUTH_INSTANCE_REQUIRE)

    sort = request.args.get("sort", "total")
    if sort not in legacy.COMMUNITY_OVERVIEW_SORTS:
        sort = "total"
    requested_page = legacy.parse_page()

    with legacy.db() as conn:
        with conn.cursor() as cur:
            community, community_error = legacy.resolve_community(
                cur,
                f"!{community_handle}",
            )
            if community_error or not community:
                abort(404)

            canonical_handle = community["handle"]
            community["local_path"] = legacy.local_community_path(canonical_handle)
            community["remote_url"] = (
                None
                if community["local"]
                else legacy.safe_http_url(community["actor_id"])
            )
            requested_offset = (requested_page - 1) * legacy.PAGE_SIZE
            overview_sql = legacy.COMMUNITY_OVERVIEW_SQL.format(
                order_by=legacy.COMMUNITY_OVERVIEW_SORTS[sort],
                vote_window_days=legacy.INSTANCE_VOTE_WINDOW_DAYS,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{legacy.INSTANCE_QUERY_TIMEOUT_SECONDS}s",),
            )
            cur.execute(
                overview_sql,
                (
                    community["id"],
                    community["id"],
                    requested_offset,
                    requested_offset + legacy.PAGE_SIZE,
                ),
            )
            result_rows = cur.fetchall()
            overview = result_rows[0]
            summary = {
                "voting_users": overview["voting_users"],
                "total": overview["summary_total"],
                "up": overview["summary_up"],
                "down": overview["summary_down"],
                "neutral": overview["summary_neutral"],
            }
            pagination = legacy.make_pagination(
                summary["voting_users"],
                requested_page,
            )
            if pagination["page"] != requested_page:
                return redirect(
                    legacy.build_community_overview_url(
                        canonical_handle,
                        sort,
                        pagination["page"],
                    )
                )
            rows = [
                legacy.enrich_community_user(row, canonical_handle)
                for row in result_rows
                if row["id"] is not None
            ]

    sort_urls = {
        key: legacy.build_community_overview_url(canonical_handle, key)
        for key in legacy.COMMUNITY_OVERVIEW_SORTS
    }
    if pagination["has_prev"]:
        pagination["prev_url"] = legacy.build_community_overview_url(
            canonical_handle,
            sort,
            pagination["prev_page"],
        )
    if pagination["has_next"]:
        pagination["next_url"] = legacy.build_community_overview_url(
            canonical_handle,
            sort,
            pagination["next_page"],
        )

    return legacy.render_template(
        "community.html",
        community=community,
        summary=summary,
        rows=rows,
        sort=sort,
        sort_urls=sort_urls,
        pagination=pagination,
        vote_window_days=legacy.INSTANCE_VOTE_WINDOW_DAYS,
    )
