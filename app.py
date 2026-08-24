# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from vote_viewer import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
