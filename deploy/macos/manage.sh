#!/bin/bash
# Любая команда manage.py с настройками сервера: bash deploy/macos/manage.sh createsuperuser
cd "$(dirname "$0")/../.." || exit 1
source deploy/macos/env.sh
load_env "$PIFAGOR_ENV"
exec venv/bin/python manage.py "$@"
