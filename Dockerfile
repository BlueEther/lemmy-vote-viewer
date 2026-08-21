# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 voteviewer \
    && useradd \
       --uid 10001 \
       --gid 10001 \
       --no-create-home \
       --shell /usr/sbin/nologin \
       voteviewer

COPY --chown=voteviewer:voteviewer app.py .
COPY --chown=voteviewer:voteviewer VERSION .
COPY --chown=voteviewer:voteviewer templates ./templates
COPY --chown=voteviewer:voteviewer static ./static

USER voteviewer

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--worker-tmp-dir", "/tmp", "--timeout", "15", "--graceful-timeout", "5", "--keep-alive", "2", "--max-requests", "1000", "--max-requests-jitter", "100", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
