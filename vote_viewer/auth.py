# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
import threading
import time
from functools import wraps
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from flask import abort, g, request


class AuthenticationUnavailable(Exception):
    pass


class NoAuthRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AuthManager:
    CACHE_MAX_ENTRIES = 1024

    def __init__(self, config, http_opener=None):
        self.config = config
        self.cache = {}
        self.cache_lock = threading.Lock()
        self.http_opener = http_opener or build_opener(NoAuthRedirectHandler())

    def cached_auth_user(self, cache_key):
        if self.config.auth_cache_seconds == 0:
            return False, None
        now = time.monotonic()
        with self.cache_lock:
            cached = self.cache.get(cache_key)
            if cached and cached[0] > now:
                return True, cached[1]
            if cached:
                self.cache.pop(cache_key, None)
        return False, None

    def cache_auth_user(self, cache_key, user):
        if self.config.auth_cache_seconds == 0:
            return
        now = time.monotonic()
        with self.cache_lock:
            if len(self.cache) >= self.CACHE_MAX_ENTRIES:
                expired_keys = [
                    key
                    for key, (expires_at, _) in self.cache.items()
                    if expires_at <= now
                ]
                for key in expired_keys:
                    self.cache.pop(key, None)
            if len(self.cache) >= self.CACHE_MAX_ENTRIES:
                self.cache.pop(next(iter(self.cache)))
            self.cache[cache_key] = (
                now + self.config.auth_cache_seconds,
                user,
            )

    def validate_lemmy_token(self, token):
        cache_key = hashlib.sha256(token.encode("utf-8")).digest()
        cache_hit, user = self.cached_auth_user(cache_key)
        if cache_hit:
            return user

        auth_request = Request(
            f"{self.config.lemmy_internal_url}/api/v3/site",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": (
                    f"lemmy-vote-viewer/{self.config.app_version}"
                ),
            },
        )
        try:
            with self.http_opener.open(
                auth_request,
                timeout=self.config.auth_timeout_seconds,
            ) as response:
                response_body = response.read(1_048_577)
                if len(response_body) > 1_048_576:
                    raise AuthenticationUnavailable
                payload = json.loads(response_body)
        except HTTPError as exc:
            if exc.code in (400, 401, 403):
                self.cache_auth_user(cache_key, None)
                return None
            raise AuthenticationUnavailable from exc
        except (
            URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            raise AuthenticationUnavailable from exc

        my_user = payload.get("my_user") if isinstance(payload, dict) else None
        local_user_view = (
            my_user.get("local_user_view")
            if isinstance(my_user, dict)
            else None
        )
        local_user = (
            local_user_view.get("local_user")
            if isinstance(local_user_view, dict)
            else None
        )
        person = (
            local_user_view.get("person")
            if isinstance(local_user_view, dict)
            else None
        )
        if not isinstance(local_user, dict) or not isinstance(person, dict):
            self.cache_auth_user(cache_key, None)
            return None

        username = person.get("name")
        if (
            not isinstance(username, str)
            or not username
            or person.get("banned", False)
            or person.get("deleted", False)
        ):
            self.cache_auth_user(cache_key, None)
            return None

        user = {
            "username": username,
            "admin": bool(local_user.get("admin", False)),
        }
        self.cache_auth_user(cache_key, user)
        return user

    def authenticated_user(self):
        if self.config.auth_provider != "lemmy":
            return None
        if getattr(g, "auth_unavailable", False):
            raise AuthenticationUnavailable
        if hasattr(g, "auth_user"):
            return g.auth_user
        token = request.cookies.get(self.config.auth_cookie_name, "")
        if not token or len(token) > 4096 or "\n" in token or "\r" in token:
            g.auth_user = None
            return None
        try:
            g.auth_user = self.validate_lemmy_token(token)
        except AuthenticationUnavailable:
            g.auth_unavailable = True
            raise
        return g.auth_user

    def access_requirement_met(self, user, requirement):
        if requirement == "disabled":
            return False
        if requirement == "none":
            return True
        if not user:
            return False
        if requirement == "login":
            return True
        if requirement == "admin":
            return user["admin"]
        return (
            user["admin"]
            or user["username"].casefold()
            in self.config.auth_allowed_users
        )

    def enforce_access(self, requirement):
        if requirement == "disabled":
            abort(404)
        if requirement == "none":
            return None
        user = self.authenticated_user()
        if not user:
            abort(401)
        if not self.access_requirement_met(user, requirement):
            abort(403)
        return user

    def require_access(self, requirement):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                self.enforce_access(requirement)
                return view(*args, **kwargs)

            return wrapped

        return decorator
