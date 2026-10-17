"""Error diagnostics without exception messages, request URLs or user payloads."""

import logging
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


def log_error(
    event: str,
    error: BaseException,
    *,
    update_id: Optional[int] = None,
    attempt: Optional[int] = None,
) -> None:
    """Keep error type and code locations; never stringify the error or update."""
    frames = []
    tb = getattr(error, "__traceback__", None)
    while tb is not None:
        code = tb.tb_frame.f_code
        frames.append(f"{Path(code.co_filename).name}:{tb.tb_lineno}:{code.co_name}")
        tb = tb.tb_next
    logger.error(
        "%s error_type=%s update_id=%s attempt=%s locations=%s",
        event,
        type(error).__name__,
        update_id if isinstance(update_id, int) else "-",
        attempt if isinstance(attempt, int) else "-",
        " > ".join(frames[-6:]) or "-",
    )
