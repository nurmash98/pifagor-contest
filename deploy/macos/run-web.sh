#!/bin/bash
# Запуск сайта (вызывается launchd: kz.pifagor.web)
cd "$(dirname "$0")/../.." || exit 1
source deploy/macos/env.sh
load_env "$PIFAGOR_ENV"
mkdir -p "$PIFAGOR_DATA/cache"
exec venv/bin/gunicorn pifagor_contest.wsgi:application -c deploy/macos/gunicorn.conf.py
