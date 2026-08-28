# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from functools import wraps

from flask import current_app, request

from .database import connect_database
from .links import (
    build_community_overview_url as _build_community_overview_url,
    build_index_url as _build_index_url,
    build_instance_url as _build_instance_url,
    build_item_url as _build_item_url,
    make_pagination as _make_pagination,
    parse_page as _parse_page,
)
from .services import resolve_item_search as _resolve_item_search


def config():
    return current_app.config["VOTE_VIEWER_CONFIG"]


def auth_manager():
    return current_app.extensions["vote_viewer_auth"]


def graph_cache():
    return current_app.extensions["vote_viewer_graph_cache"]


def db():
    return connect_database(config().database_url)


def enforce_access(requirement):
    return auth_manager().enforce_access(requirement)


def require_access(config_attribute):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            enforce_access(getattr(config(), config_attribute))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def parse_page():
    return _parse_page(request.args.get("page", "1"))


def make_pagination(total, requested_page):
    return _make_pagination(total, requested_page, config().page_size)


def build_index_url(
    username,
    content_type="all",
    score_filter=None,
    page=1,
    history_view="cast",
    history_sort="date",
    community=None,
    community_sort="total",
):
    return _build_index_url(
        username,
        content_type,
        score_filter,
        page,
        history_view,
        history_sort,
        community,
        community_sort,
        config().app_prefix,
    )


def build_item_url(kind, item_id, page=1, sort="vote"):
    return _build_item_url(
        kind, item_id, page, config().app_prefix, sort
    )


def build_instance_url(domain, sort="total", page=1):
    return _build_instance_url(domain, sort, page, config().app_prefix)


def build_community_overview_url(handle, sort="total", page=1):
    return _build_community_overview_url(
        handle,
        sort,
        page,
        config().app_prefix,
    )


def resolve_item_search(item_query):
    return _resolve_item_search(item_query, config().lemmy_base_url, db)
