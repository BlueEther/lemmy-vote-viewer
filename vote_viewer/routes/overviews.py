# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json

from flask import (
    Blueprint,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
)
import psycopg

from ..links import local_community_path, normalize_instance_domain, safe_http_url
from ..queries import (
    COMMUNITY_OVERVIEW_SORTS,
    COMMUNITY_OVERVIEW_SQL,
    COMMUNITY_VOTE_GRAPH_SQL,
    INSTANCE_LOOKUP_SQL,
    INSTANCE_OVERVIEW_SQL,
    INSTANCE_SORTS,
)
from ..services import (
    build_vote_graph,
    enrich_community_user,
    enrich_instance_user,
    resolve_community,
)
from ..web import (
    build_community_overview_url,
    build_instance_url,
    config,
    db,
    enforce_access,
    make_pagination,
    overview_graph_cache,
    parse_page,
)


blueprint = Blueprint("overviews", __name__)


def graph_response(payload, status=200, cache_status=None):
    response = make_response(payload, status)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    if cache_status:
        response.headers["X-Vote-Graph-Cache"] = cache_status
    return response


@blueprint.route("/graph/community")
def community_vote_graph():
    settings = config()
    if (
        not settings.enable_domain_search
        or not settings.enable_community_vote_graphs
    ):
        abort(404)
    enforce_access(settings.auth_instance_require)

    try:
        community_id = int(request.args.get("community_id", ""))
    except ValueError:
        abort(400)
    if community_id <= 0:
        abort(400)

    cache_key = hashlib.sha256(
        json.dumps(
            (
                "community",
                community_id,
                settings.timezone_name,
                settings.vote_window_days,
            ),
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cache = overview_graph_cache()
    cache_state, cached_payload = cache.claim(cache_key)
    if cache_state == "hit":
        return graph_response(cached_payload, cache_status="hit")
    if cache_state == "busy":
        response = graph_response("", status=202, cache_status="busy")
        response.headers["Retry-After"] = "1"
        return response

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{settings.instance_query_timeout_seconds}s",),
                )
                cur.execute(
                    COMMUNITY_VOTE_GRAPH_SQL,
                    (
                        community_id,
                        settings.timezone_name,
                        settings.vote_window_days,
                    ),
                )
                vote_graph = build_vote_graph(cur.fetchall())
                payload = render_template(
                    "_vote_graph.html",
                    vote_graph=vote_graph,
                    vote_graph_title="Votes in community by day",
                    vote_graph_window_days=settings.vote_window_days,
                )
                cache.store(cache_key, payload)
                return graph_response(payload, cache_status="miss")
    except psycopg.errors.QueryCanceled:
        cache.release(cache_key)
        current_app.logger.warning(
            "Community vote graph query timed out for %s",
            community_id,
        )
        return graph_response(
            render_template(
                "_vote_graph_error.html",
                message="The graph took too long to calculate. Try again later.",
            ),
            status=503,
            cache_status="error",
        )
    except Exception:
        cache.release(cache_key)
        raise


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
                vote_window_days=settings.vote_window_days,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{settings.instance_query_timeout_seconds}s",),
            )
            cur.execute(
                overview_sql,
                (
                    instance["id"],
                    settings.enable_instance_content_counts,
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
        vote_window_days=settings.vote_window_days,
        content_counts_enabled=settings.enable_instance_content_counts,
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
            community_graph_url = (
                f"{settings.app_prefix}/graph/community"
                f"?community_id={community['id']}"
                if settings.enable_community_vote_graphs
                else None
            )
            community["local_path"] = local_community_path(canonical_handle)
            community["remote_url"] = (
                None
                if community["local"]
                else safe_http_url(community["actor_id"])
            )
            requested_offset = (requested_page - 1) * settings.page_size
            overview_sql = COMMUNITY_OVERVIEW_SQL.format(
                order_by=COMMUNITY_OVERVIEW_SORTS[sort],
                vote_window_days=settings.vote_window_days,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{settings.instance_query_timeout_seconds}s",),
            )
            cur.execute(
                overview_sql,
                (
                    community["id"],
                    settings.enable_community_content_counts,
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
        vote_window_days=settings.vote_window_days,
        content_counts_enabled=settings.enable_community_content_counts,
        vote_graph_url=community_graph_url,
    )
