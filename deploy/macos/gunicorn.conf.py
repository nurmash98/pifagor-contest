# gunicorn для Pifagor Contest на Mac-сервере (за Cloudflare Tunnel). Порт и воркеры — из pifagor.env.
import os

bind = os.environ.get("PIFAGOR_BIND", "127.0.0.1:8090")
workers = int(os.environ.get("WEB_WORKERS", 3))
worker_class = "gthread"
threads = int(os.environ.get("WEB_THREADS", 4))
timeout = 90                 # автотесты C++/Python могут идти несколько секунд
graceful_timeout = 30
max_requests = 2000
max_requests_jitter = 200
pidfile = os.path.expanduser("~/pifagor-data/gunicorn.pid")
accesslog = "-"
errorlog = "-"
