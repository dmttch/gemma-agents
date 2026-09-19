"""Kernel-owned workspace leases, independent of runtime storage locations."""

import fcntl
import os
from pathlib import Path


class WorkspaceBusyError(RuntimeError):
    """Another runtime holds the workspace directory's exclusive lease."""


class WorkspaceLease:
    """Hold an advisory directory lock until close or process death.

    Lock the directory inode itself: there is no mutable lock file for a command
    to delete, and different storage directories cannot bypass exclusivity.
    Descriptors are not inherited by executed children. This coordinates Gemma
    runtimes, not editors or hostile programs; use a local trusted filesystem.
    """

    def __init__(self, workspace: Path, *, shared: bool = False):
        """Acquire without waiting; raise WorkspaceBusyError on contention."""
        self._fd = os.open(workspace.resolve(), os.O_RDONLY | os.O_DIRECTORY)
        try:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(self._fd, mode | fcntl.LOCK_NB)
        except BaseException as error:
            self.close()
            if isinstance(error, BlockingIOError):
                raise WorkspaceBusyError(
                    f"Workspace déjà ouvert par une autre instance : {workspace}. "
                    "Ferme cette instance avant de continuer."
                ) from error
            raise

    def close(self) -> None:
        """Release the lease once; kernel cleanup also handles abrupt process exit."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
