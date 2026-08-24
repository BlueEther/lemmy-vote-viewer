# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later


def register_blueprints(app):
    from .items import blueprint as items_blueprint
    from .overviews import blueprint as overviews_blueprint
    from .search import blueprint as search_blueprint

    app.register_blueprint(search_blueprint)
    app.register_blueprint(overviews_blueprint)
    app.register_blueprint(items_blueprint)
