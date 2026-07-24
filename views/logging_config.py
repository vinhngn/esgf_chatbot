from __future__ import annotations

import logging

from tornado.iostream import StreamClosedError
from tornado.websocket import WebSocketClosedError


class _DisconnectedClientFilter(logging.Filter):
    """Drop expected disconnect noise while preserving real server errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        exception = record.exc_info[1] if record.exc_info else None
        if isinstance(exception, (StreamClosedError, WebSocketClosedError)):
            return False
        message = record.getMessage()
        return not (
            "WebSocketClosedError" in message or "StreamClosedError" in message
        )


def configure_streamlit_logging() -> None:
    disconnect_filter = _DisconnectedClientFilter()
    for name in ("asyncio", "tornado.application", "tornado.general"):
        logger = logging.getLogger(name)
        if not any(
            isinstance(item, _DisconnectedClientFilter) for item in logger.filters
        ):
            logger.addFilter(disconnect_filter)
