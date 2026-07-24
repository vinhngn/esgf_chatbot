from __future__ import annotations

import logging

from tornado.websocket import WebSocketClosedError

from views.logging_config import _DisconnectedClientFilter


def test_streamlit_log_filter_drops_expected_websocket_disconnect() -> None:
    error = WebSocketClosedError()
    record = logging.LogRecord(
        name="asyncio",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Task exception was never retrieved",
        args=(),
        exc_info=(type(error), error, None),
    )

    assert _DisconnectedClientFilter().filter(record) is False


def test_streamlit_log_filter_keeps_real_errors() -> None:
    record = logging.LogRecord(
        name="asyncio",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Database worker failed",
        args=(),
        exc_info=None,
    )

    assert _DisconnectedClientFilter().filter(record) is True
