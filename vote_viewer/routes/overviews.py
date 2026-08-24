# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from flask import Blueprint, abort, redirect, render_template, request

from ..links import local_community_path, normalize_instance_domain, safe_http_url
from ..queries import (
    COMMUNITY_OVERVIEW_SORTS,
    COMMUNITY_OVERVIEW_SQL,
    INSTANCE_LOOKUP_SQL,
    INSTANCE_OVERVIEW_SQL,
    INSTANCE_SORTS,
)
from ..services import enrich_community_user, enrich_instance_user, resolve_community
from ..web import (
    build_community_overview_url,
    build_instance_url,
    config,
    db,
    enforce_access,
    make_pagination,
    parse_page,
)


blueprint = Blueprint("overviews", __name__)


@blueprint.route("/instance/<domain>")
def instance_overview(domain):
    settings = config()
    if not settings.enable_domain_search:
        abort(404)
    enforce_access(settings.auth_instance_require)

    normalized_domain = normalize_instance_domain(domain)
    if not normalized_domain:
        abort(404)

    sort = request.args.get("sort", "total")
    if sort not in INSTANCE_SORTS:
        sort = "total"
    requested_page = parse_page()

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(INSTANCE_LOOKUP_SQL, (normalized_domain,))
            instance = cur.fetchone()
            if not instance:
                abort(404)

            canonical_domain = normalize_instance_domain(instance["domain"])
            if not canonical_domain:
                abort(404)

            requested_offset = (requested_page - 1) * settings.page_size
            overview_sql = INSTANCE_OVERVIEW_SQL.format(
                order_by=INSTANCE_SORTS[sort],
                vote_window_days=settings.instance_vote_window_days,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{settings.instance_query_timeout_seconds}s",),
            )
            cur.execute(
                overview_sql,
                (
                    instance["id"],
                    requested_offset,
                    requested_offset + settings.page_size,
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
            pagination = make_pagination(summary["voting_users"], requested_page)
            if pagination["page"] != requested_page:
                return redirect(
                    build_instance_url(canonical_domain, sort, pagination["page"])
                )
            rows = [
                enrich_instance_user(row, settings.app_prefix)
                for row in result_rows
                if row["id"] is not None
            ]

    sort_urls = {
        key: build_instance_url(canonical_domain, key)
        for key in INSTANCE_SORTS
    }
    if pagination["has_prev"]:
        pagination["prev_url"] = build_instance_url(
            canonical_domain, sort, pagination["prev_page"]
        )
    if pagination["has_next"]:
        pagination["next_url"] = build_instance_url(
            canonical_domain, sort, pagination["next_page"]
        )

    return render_template(
        "instance.html",
        instance=instance,
        domain=canonical_domain,
        summary=summary,
        rows=rows,
        sort=sort,
        sort_urls=sort_urls,
        pagination=pagination,
        vote_window_days=settings.instance_vote_window_days,
    )



@blueprint.route("/community/<community_handle>")
def community_overview(community_handle):
    settings = config()
    if not settings.enable_domain_search:
        abort(404)
    enforce_access(settings.auth_instance_require)

    sort = request.args.get("sort", "total")
    if sort not in COMMUNITY_OVERVIEW_SORTS:
        sort = "total"
    requested_page = parse_page()

    with db() as conn:
        with conn.cursor() as cur:
            community, community_error = resolve_community(
                cur,
                f"!{community_handle}",
            )
            if community_error or not community:
                abort(404)

            canonical_handle = community["handle"]
            community["local_path"] = local_community_path(canonical_handle)
            community["remote_url"] = (
                None
                if community["local"]
                else safe_http_url(community["actor_id"])
            )
            requested_offset = (requested_page - 1) * settings.page_size
            overview_sql = COMMUNITY_OVERVIEW_SQL.format(
                order_by=COMMUNITY_OVERVIEW_SORTS[sort],
                vote_window_days=settings.instance_vote_window_days,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{settings.instance_query_timeout_seconds}s",),
            )
            cur.execute(
                overview_sql,
                (
                    community["id"],
                    community["id"],
                    requested_offset,
                    requested_offset + settings.page_size,
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
            pagination = make_pagination(
                summary["voting_users"],
                requested_page,
            )
            if pagination["page"] != requested_page:
                return redirect(
                    build_community_overview_url(
                        canonical_handle,
                        sort,
                        pagination["page"],
                    )
                )
            rows = [
                enrich_community_user(row, canonical_handle, settings.app_prefix)
                for row in result_rows
                if row["id"] is not None
            ]

    sort_urls = {
        key: build_community_overview_url(canonical_handle, key)
        for key in COMMUNITY_OVERVIEW_SORTS
    }
    if pagination["has_prev"]:
        pagination["prev_url"] = build_community_overview_url(
            canonical_handle,
            sort,
            pagination["prev_page"],
        )
    if pagination["has_next"]:
        pagination["next_url"] = build_community_overview_url(
            canonical_handle,
            sort,
            pagination["next_page"],
        )

    return render_template(
        "community.html",
        community=community,
        summary=summary,
        rows=rows,
        sort=sort,
        sort_urls=sort_urls,
        pagination=pagination,
        vote_window_days=settings.instance_vote_window_days,
    )
