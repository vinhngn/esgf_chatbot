"""
Analytics service - decoupled from Streamlit.
Session ID is passed in, not read from st.session_state.
"""
from __future__ import annotations

import logging
import uuid

from config import get_settings

logger = logging.getLogger(__name__)

_analytics_enabled = False
_initialized = False


def _init_analytics() -> None:
    global _analytics_enabled, _initialized
    if _initialized:
        return
    _initialized = True

    settings = get_settings()
    key = settings.SEGMENT_WRITE_KEY
    if key:
        try:
            from segment import analytics
            analytics.write_key = key
            _analytics_enabled = True
        except ImportError:
            logger.warning("segment-analytics-python not installed, analytics disabled")
    else:
        _analytics_enabled = False


def generate_session_id() -> str:
    return str(uuid.uuid4())


def track(user_id: str, event_name: str, properties: dict, session_id: str = "") -> None:
    """Track an analytics event."""
    _init_analytics()
    if not _analytics_enabled:
        return

    properties["session_id"] = session_id
    try:
        from segment import analytics
        analytics.track(user_id=user_id, event=event_name, properties=properties)
    except Exception as e:
        logger.warning(f"Analytics tracking failed: {e}")
