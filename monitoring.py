import os
import logging
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

logger = logging.getLogger(__name__)


def init_sentry():
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        logger.warning("SENTRY_DSN não configurado — monitoramento desativado")
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            FlaskIntegration(),
            LoggingIntegration(
                level=logging.WARNING,
                event_level=logging.ERROR,
            ),
        ],
        traces_sample_rate=0.1,
        environment=os.getenv("RAILWAY_ENVIRONMENT", "production"),
        release=os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    )
    logger.info("Sentry inicializado (env=%s)", os.getenv("RAILWAY_ENVIRONMENT", "production"))
