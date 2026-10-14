"""One supervisor per evidence directory, released on process exit."""
from pathlib import Path
import os
import stat


class StateLock:
    def __init__(self, directory):
        from .provenance import safe_directory
        directory = safe_directory(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "supervisor.lock"
        if path.is_symlink():
            raise ValueError("Supervisor lock must not be a symbolic link")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor)
            raise ValueError("Supervisor lock must be an unlinked regular file")
        self.file = os.fdopen(descriptor, "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("Another supervisor owns this state directory") from exc

    def close(self):
        if self.file.closed:
            return
        if os.name == "nt":
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
