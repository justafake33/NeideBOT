import os
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

def init_sentry():
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,
        environment=os.getenv("APP_ENV", "production"),
    )