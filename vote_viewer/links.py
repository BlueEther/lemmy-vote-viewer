# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import math
import re
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

def safe_http_url(value):
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return value

def actor_domain(actor_id):
    url = safe_http_url(actor_id)
    if not url:
        return None
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".") or None
    except ValueError:
        return None

def make_handle(name, local, actor_id):
    if not name:
        return None
    if local:
        return name
    domain = actor_domain(actor_id)
    return f"{name}@{domain}" if domain else name

def local_profile_path(handle):
    if not handle:
        return None
    return "/u/" + quote(handle, safe="@._~-")

def remote_profile_url(local, actor_id):
    if local:
        return None
    return safe_http_url(actor_id)

def local_community_path(handle):
    if not handle:
        return None
    return "/c/" + quote(handle.removeprefix("!"), safe="@._~-")

LOCAL_ITEM_PATH = re.compile(r"^/(post|comment)/(\d+)/?$")

def parse_local_item_path(path):
    match = LOCAL_ITEM_PATH.fullmatch(path)
    if not match:
        return None
    item_id = int(match.group(2))
    return (match.group(1), item_id) if item_id > 0 else None

def url_origin(parsed):
    try:
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return None
    default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
    return scheme, host, port or default_port

def parse_item_search(value, lemmy_base_url=None):
    value = value.strip()
    if not value:
        return None

    if value.startswith("/"):
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            return None
        local_item = parse_local_item_path(parsed.path)
        return {"local_item": local_item} if local_item else None

    url = safe_http_url(value)
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.username or parsed.password or url_origin(parsed) is None:
            return None
    except ValueError:
        return None

    clean_url = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )
    alternate_url = clean_url[:-1] if clean_url.endswith("/") else clean_url

    local_item = None
    if lemmy_base_url:
        base = urlsplit(lemmy_base_url)
        if url_origin(parsed) == url_origin(base):
            local_item = parse_local_item_path(parsed.path)

    return {
        "local_item": local_item,
        "ap_urls": (clean_url, alternate_url),
    }

def parse_page(value):
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(page, 1_000_000))

def make_pagination(total, requested_page, page_size):
    page_count = max(1, math.ceil(total / page_size)) if total else 1
    page = min(max(1, requested_page), page_count)
    return {
        "page": page,
        "page_count": page_count,
        "total": total,
        "offset": (page - 1) * page_size,
        "has_prev": page > 1,
        "has_next": page < page_count,
        "prev_page": page - 1,
        "next_page": page + 1,
    }

def build_index_url(
    username,
    content_type="all",
    score_filter=None,
    page=1,
    history_view="cast",
    history_sort="date",
    community=None,
    community_sort="total",
    app_prefix="",
):
    params = {"user": username}
    if history_view == "received":
        params["view"] = "received"
        if history_sort != "date":
            params["sort"] = history_sort
    elif history_view == "communities":
        params["view"] = "communities"
        if community_sort != "total":
            params["sort"] = community_sort
    elif history_sort != "date":
        params["sort"] = history_sort
    if content_type != "all":
        params["type"] = content_type
    if score_filter is not None:
        params["score"] = str(score_filter)
    if community:
        params["community"] = community
    if page > 1:
        params["page"] = str(page)
    return f"{app_prefix}/?{urlencode(params)}"

def build_item_url(kind, item_id, page=1, app_prefix="", sort="vote"):
    path = f"{app_prefix}/item/{kind}/{item_id}"
    params = {}
    if sort != "vote":
        params["sort"] = sort
    if page > 1:
        params["page"] = str(page)
    return f"{path}?{urlencode(params)}" if params else path

def build_instance_url(domain, sort="total", page=1, app_prefix=""):
    path = f"{app_prefix}/instance/{quote(domain, safe='.-')}"
    params = {}
    if sort != "total":
        params["sort"] = sort
    if page > 1:
        params["page"] = str(page)
    return f"{path}?{urlencode(params)}" if params else path

def build_community_overview_url(handle, sort="total", page=1, app_prefix=""):
    community = handle.removeprefix("!")
    path = f"{app_prefix}/community/{quote(community, safe='@._~-')}"
    params = {}
    if sort != "total":
        params["sort"] = sort
    if page > 1:
        params["page"] = str(page)
    return f"{path}?{urlencode(params)}" if params else path

def vote_history_path(handle, app_prefix=""):
    return (
        build_index_url(handle, "all", None, 1, app_prefix=app_prefix)
        if handle
        else None
    )

INSTANCE_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

def normalize_instance_domain(value):
    value = value.strip().lower().rstrip(".")
    if value.startswith("@"):
        value = value[1:]
    if not value or len(value) > 253 or "/" in value or "@" in value:
        return None
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(value) > 253:
        return None
    labels = value.split(".")
    if not labels or any(not INSTANCE_LABEL.fullmatch(label) for label in labels):
        return None
    return value

def parse_community_handle(value):
    value = value.strip()
    if not value.startswith("!") or len(value) > 512:
        return None

    handle = value[1:]
    if "@" in handle:
        name, domain = handle.rsplit("@", 1)
        domain = normalize_instance_domain(domain)
        if not domain:
            return None
    else:
        name = handle
        domain = None

    name = name.strip()
    if (
        not name
        or len(name) > 255
        or any(character.isspace() for character in name)
        or any(character in name for character in "!/@")
    ):
        return None
    return name, domain

def parse_user_suggestion_input(username):
    username = username.strip()
    if username.startswith("@"):
        username = username[1:]

    if "@" in username:
        name_prefix, domain_prefix = username.rsplit("@", 1)
        name_prefix = name_prefix.strip()
        domain_prefix = domain_prefix.strip().lower().rstrip(".")
        if "/" in domain_prefix or len(domain_prefix) > 255:
            return None
    else:
        name_prefix = username
        domain_prefix = None

    if len(name_prefix) < 2 or len(name_prefix) > 255:
        return None
    return name_prefix, domain_prefix

def like_prefix_pattern(value):
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        + "%"
    )
