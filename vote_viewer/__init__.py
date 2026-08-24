# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later


def create_app():
    """Return the configured Flask application.

    Application construction remains in ``application`` temporarily while the
    behavior-preserving module extraction is in progress.
    """
    from .application import app

    return app
