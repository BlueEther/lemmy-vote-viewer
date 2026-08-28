# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


AUTH_REQUIREMENTS = frozenset(
    {"disabled", "none", "login", "allowlist", "admin"}
)


@dataclass(frozen=True)
class AppConfig:
    app_version: str
    database_url: str
    enable_domain_search: bool
    enable_instance_content_counts: bool
    enable_community_content_counts: bool
    enable_user_vote_graphs: bool
    app_prefix: str
    page_size: int
    instance_query_timeout_seconds: int
    vote_window_days: int
    timezone_name: str
    display_timezone: ZoneInfo
    lemmy_base_url: str | None
    lemmy_instance: str | None
    auth_provider: str
    auth_search_require: str
    auth_instance_require: str
    auth_allowed_users: frozenset[str]
    auth_cookie_name: str
    auth_cache_seconds: int
    auth_timeout_seconds: float
    lemmy_internal_url: str | None
    lemmy_login_url: str | None


def boolean_env(environ, name, default=False):
    value = environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RuntimeError(f"{name} must be either true or false")


def bounded_int_env(environ, name, default, minimum, maximum):
    try:
        value = int(environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def bounded_float_env(environ, name, default, minimum, maximum):
    try:
        value = float(environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def lemmy_instance_config(value):
    if not value:
        return None, None
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            return None, None
        base_url = f"{parsed.scheme.lower()}://{parsed.netloc}"
        instance = (parsed.hostname or "").lower().rstrip(".")
        return (base_url, instance) if instance else (None, None)
    except ValueError:
        return None, None


def auth_requirement_env(environ, name, default="none"):
    requirement = environ.get(name, default).strip().lower()
    if requirement not in AUTH_REQUIREMENTS:
        choices = ", ".join(sorted(AUTH_REQUIREMENTS))
        raise RuntimeError(f"{name} must be one of: {choices}")
    return requirement


def load_config(environ=None, project_root=None):
    environ = os.environ if environ is None else environ
    project_root = (
        Path(__file__).resolve().parent.parent
        if project_root is None
        else Path(project_root)
    )

    app_version = (project_root / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
    if not app_version:
        raise RuntimeError("VERSION file is empty")

    database_url = environ["DATABASE_URL"]
    enable_domain_search = boolean_env(
        environ, "ENABLE_DOMAIN_SEARCH", False
    )
    enable_instance_content_counts = boolean_env(
        environ, "ENABLE_INSTANCE_CONTENT_COUNTS", True
    )
    enable_community_content_counts = boolean_env(
        environ, "ENABLE_COMMUNITY_CONTENT_COUNTS", True
    )
    enable_user_vote_graphs = boolean_env(
        environ, "ENABLE_USER_VOTE_GRAPHS", True
    )

    raw_prefix = environ.get("APP_PREFIX", "/votes").strip()
    app_prefix = "" if raw_prefix in ("", "/") else "/" + raw_prefix.strip("/")

    page_size = bounded_int_env(environ, "PAGE_SIZE", 100, 20, 250)
    instance_query_timeout_seconds = bounded_int_env(
        environ,
        "INSTANCE_QUERY_TIMEOUT_SECONDS",
        12,
        5,
        12,
    )
    vote_window_days = bounded_int_env(
        environ,
        "VOTE_WINDOW_DAYS",
        30,
        1,
        365,
    )

    timezone_name = environ.get("TIMEZONE", "UTC").strip() or "UTC"
    try:
        display_timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise RuntimeError(f"Invalid TIMEZONE: {timezone_name}") from exc

    raw_lemmy_base_url = environ.get("LEMMY_BASE_URL", "").strip()
    lemmy_base_url, lemmy_instance = lemmy_instance_config(
        raw_lemmy_base_url
    )
    if raw_lemmy_base_url and not lemmy_base_url:
        raise RuntimeError(
            "LEMMY_BASE_URL must be an HTTP(S) origin without credentials, "
            "a path, query, or fragment"
        )

    auth_provider = environ.get("AUTH_PROVIDER", "none").strip().lower()
    if auth_provider not in ("none", "lemmy"):
        raise RuntimeError("AUTH_PROVIDER must be either none or lemmy")

    auth_search_require = auth_requirement_env(
        environ, "AUTH_SEARCH_REQUIRE"
    )
    auth_instance_require = auth_requirement_env(
        environ, "AUTH_INSTANCE_REQUIRE"
    )
    authentication_required = {"login", "allowlist", "admin"}
    if auth_provider == "none" and (
        auth_search_require in authentication_required
        or auth_instance_require in authentication_required
    ):
        raise RuntimeError(
            "AUTH_PROVIDER must be lemmy when an authentication requirement "
            "is enabled"
        )

    auth_allowed_users = frozenset(
        username.strip().casefold()
        for username in environ.get("AUTH_ALLOWED_USERS", "").split(",")
        if username.strip()
    )
    auth_cookie_name = environ.get("AUTH_COOKIE_NAME", "jwt").strip() or "jwt"
    auth_cache_seconds = bounded_int_env(
        environ, "AUTH_CACHE_SECONDS", 60, 0, 300
    )
    auth_timeout_seconds = bounded_float_env(
        environ, "AUTH_TIMEOUT_SECONDS", 3.0, 1.0, 10.0
    )

    auth_internal_url = environ.get("LEMMY_INTERNAL_URL", "").strip()
    lemmy_internal_url, _ = lemmy_instance_config(
        auth_internal_url or lemmy_base_url or ""
    )
    if auth_internal_url and not lemmy_internal_url:
        raise RuntimeError(
            "LEMMY_INTERNAL_URL must be an HTTP(S) origin without credentials, "
            "a path, query, or fragment"
        )
    if auth_provider == "lemmy" and not lemmy_internal_url:
        raise RuntimeError(
            "LEMMY_INTERNAL_URL or LEMMY_BASE_URL is required for Lemmy "
            "authentication"
        )

    lemmy_login_url = f"{lemmy_base_url}/login" if lemmy_base_url else None

    return AppConfig(
        app_version=app_version,
        database_url=database_url,
        enable_domain_search=enable_domain_search,
        enable_instance_content_counts=enable_instance_content_counts,
        enable_community_content_counts=enable_community_content_counts,
        enable_user_vote_graphs=enable_user_vote_graphs,
        app_prefix=app_prefix,
        page_size=page_size,
        instance_query_timeout_seconds=instance_query_timeout_seconds,
        vote_window_days=vote_window_days,
        timezone_name=timezone_name,
        display_timezone=display_timezone,
        lemmy_base_url=lemmy_base_url,
        lemmy_instance=lemmy_instance,
        auth_provider=auth_provider,
        auth_search_require=auth_search_require,
        auth_instance_require=auth_instance_require,
        auth_allowed_users=auth_allowed_users,
        auth_cookie_name=auth_cookie_name,
        auth_cache_seconds=auth_cache_seconds,
        auth_timeout_seconds=auth_timeout_seconds,
        lemmy_internal_url=lemmy_internal_url,
        lemmy_login_url=lemmy_login_url,
    )
