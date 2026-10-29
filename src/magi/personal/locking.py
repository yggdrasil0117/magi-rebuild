from pathlib import Path


class ApplicationLock:
    """A single-process lock shared by chat and management entry points."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            self.file.seek(0)
            self.file.write(b"0")
            self.file.flush()
            self.file.seek(0)
            try:
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            except ImportError:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError(
                "MAGI is already running; exit chat before managing memories"
            ) from exc
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.file is None:
            return
        try:
            self.file.seek(0)
            try:
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None
