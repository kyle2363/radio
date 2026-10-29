"""Quiet routine success traffic while retaining failures and redacting URLs."""
import logging

import pytest


@pytest.mark.parametrize('path', [
    '/api/play/status', '/api/queue/status', '/api/playback/sessions/s/position',
    '/api/playback/sessions/s/lease', '/api/media/tracks/1', '/api/ingest/progress',
    '/api/playback/sessions/s/events',
])
def test_routine_success_can_be_quiet_but_failures_are_logged(path):
    from access_logging import AccessLogFilter
    for status in (200, 206, 304, 401, 409, 500):
        record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
                                   ('client', 'GET', path + '?token=PRIVATE', '1.1', status), None)
        assert AccessLogFilter(quiet=True).filter(record) == (status >= 400)
        assert 'PRIVATE' not in record.getMessage()


def test_verbose_mode_keeps_success_and_redacts_query_for_all_endpoints():
    from access_logging import AccessLogFilter
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
                               ('client', 'POST', '/api/unknown?secret=PRIVATE', '1.1', 200), None)
    assert AccessLogFilter(quiet=False).filter(record)
    assert 'PRIVATE' not in record.getMessage()
