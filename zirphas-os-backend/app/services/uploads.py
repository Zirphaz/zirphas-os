"""Keep files created during a request aligned with its database transaction."""

import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_PENDING_FILES = "zirphas_pending_uploads"
_HOOKS_INSTALLED = "zirphas_upload_hooks_installed"


def _remove_rolled_back_uploads(session: Session) -> None:
    for path in session.info.pop(_PENDING_FILES, set()):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Preserve the database error while making cleanup failures observable.
            logger.exception("Não foi possível remover o upload revertido: %s", path)


def _retain_committed_uploads(session: Session) -> None:
    session.info.pop(_PENDING_FILES, None)


def register_pending_upload(session: Session, path: Path) -> None:
    """Register before writing so write, flush, and commit failures all clean up.

    The request owns an explicit rollback on failure. Successful commits release
    the paths so a later rollback on the same session cannot delete saved photos.
    """
    if not session.info.get(_HOOKS_INSTALLED):
        event.listen(session, "after_rollback", _remove_rolled_back_uploads)
        event.listen(session, "after_commit", _retain_committed_uploads)
        session.info[_HOOKS_INSTALLED] = True
    session.info.setdefault(_PENDING_FILES, set()).add(path.resolve())
