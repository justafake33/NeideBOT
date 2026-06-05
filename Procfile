web: gunicorn app:app --workers 1 --threads 8 --timeout 120
worker: RUN_BOT_SERVICES=1 python bot_worker.py
