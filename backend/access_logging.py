"""Uvicorn access logging with private query strings removed in every mode."""
import logging
import os
import re


_ROUTINE = re.compile(
    r'^/api/(?:media(?:/|$)|playback/sessions/[^/]+(?:/(?:position|lease|queue|events))?$|'
    r'play/status$|queue(?:/status)?$|mood$|sleep/timer$|energy/trajectory$|'
    r'ingest/(?:status|summary|progress|phase-status)$|health$)'
)


class AccessLogFilter(logging.Filter):
    def __init__(self, quiet=True):
        super().__init__()
        self.quiet = quiet

    def filter(self, record):
        # Uvicorn provides (client, method, target, HTTP version, status).
        # Rewrite before formatting so even failed media requests never log
        # query-string grants or credentials. Unknown log shapes remain visible.
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return True
        client, method, target, version, status = record.args
        path = str(target).split('?', 1)[0]
        record.args = (client, method, path, version, status)
        try:
            success = 200 <= int(status) < 400
        except (TypeError, ValueError):
            return True
        return not (self.quiet and success and _ROUTINE.match(path))


def configure_access_logging():
    logger = logging.getLogger('uvicorn.access')
    for existing in list(logger.filters):
        if isinstance(existing, AccessLogFilter):
            logger.removeFilter(existing)
    quiet = os.environ.get('RADIO_QUIET_ACCESS_LOGS', 'true').lower() not in ('false', '0', 'no')
    logger.addFilter(AccessLogFilter(quiet=quiet))
