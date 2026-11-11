# .. _persister_example:

import contextlib
import hashlib
import os
import tempfile
from pathlib import Path

from ..serialize import deserialize, serialize

# Suffix of the candidate file a refresh writes to before it replaces the
# cassette. It's never read back as a cassette, so a failed run leaves its
# partial recording for debugging without the next test picking it up.
CANDIDATE_SUFFIX = ".candidate"

# Suffix of the hidden lock file that serializes candidate commits per path.
LOCK_SUFFIX = ".lock"


class CassetteNotFoundError(FileNotFoundError):
    pass


class CassetteDecodeError(ValueError):
    pass


class CassetteConflictError(OSError):
    """Raised when a refresh can't replace the cassette because another
    process committed a different version first.

    The candidate file is kept so the recording isn't lost.
    """


# Sentinel: callers that don't ask for a concurrency check pass nothing, while
# a refresh of a cassette that didn't exist passes ``None`` explicitly.
_UNSET = object()


class FilesystemPersister:
    @classmethod
    @contextlib.contextmanager
    def _commit_lock(cls, cassette_path):
        """Cross-process lock making the version check and replacement atomic.

        A separate lock file (not the cassette itself) is used so the lock can
        be taken before the first recording creates the cassette, and isn't
        disturbed by replacing the cassette underneath it. The file is kept
        rather than deleted, since removing a lock file another process is
        waiting on would break mutual exclusion.
        """
        lock_path = cls._lock_path(cassette_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            # msvcrt.locking needs a byte to lock; flock below doesn't care.
            # Append mode always writes at the end, so measure the size there
            # rather than at the read position (which starts at 0).
            if lock_file.seek(0, os.SEEK_END) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                import time

                # msvcrt has no blocking lock, so retry the non-blocking one,
                # matching the blocking semantics of flock below.
                while True:
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @classmethod
    def load_cassette(cls, cassette_path, serializer):
        cassette_path = Path(cassette_path)  # if cassette path is already Path this is no operation
        if not cassette_path.is_file():
            raise CassetteNotFoundError()
        try:
            with cassette_path.open() as f:
                data = f.read()
        except UnicodeDecodeError as err:
            raise CassetteDecodeError("Can't read Cassette, Encoding is broken") from err

        if not data.strip():
            # An empty file is treated like a missing cassette and recorded again, as in Ruby VCR.
            raise CassetteNotFoundError()

        return deserialize(data, serializer)

    @staticmethod
    def save_cassette(cassette_path, cassette_dict, serializer):
        data = serialize(cassette_dict, serializer)
        cassette_path = Path(cassette_path)  # if cassette path is already Path this is no operation

        cassette_folder = cassette_path.parent
        if not cassette_folder.exists():
            cassette_folder.mkdir(parents=True)

        with cassette_path.open("w") as f:
            f.write(data)

    @classmethod
    def save_candidate(cls, cassette_path, cassette_dict, serializer):
        """Write a refresh result to a unique candidate file next to the
        cassette and return its path.

        Each refresh gets its own candidate file, so concurrent refreshes
        don't overwrite each other's in-progress recordings. A candidate is
        only ever read back by :meth:`commit_candidate`, never as a cassette,
        so an unfinished refresh can't become what the next test loads.
        """
        data = serialize(cassette_dict, serializer)
        cassette_path = Path(cassette_path)

        cassette_folder = cassette_path.parent
        if not cassette_folder.exists():
            cassette_folder.mkdir(parents=True)

        fd, candidate_path = tempfile.mkstemp(
            prefix=f".{cassette_path.name}.",
            suffix=CANDIDATE_SUFFIX,
            dir=cassette_folder,
        )
        try:
            # mkstemp creates the file 0600, but an open("w") write (the old
            # behavior) honors the umask, usually ending up 0644. Match that so
            # committing the candidate doesn't tighten the cassette's mode.
            current_umask = os.umask(0)
            os.umask(current_umask)
            os.chmod(candidate_path, 0o666 & ~current_umask)
            with os.fdopen(fd, "w") as f:
                f.write(data)
        except BaseException:
            # Don't leak the unique temp file if writing fails.
            with contextlib.suppress(FileNotFoundError):
                os.unlink(candidate_path)
            raise
        return Path(candidate_path)

    @classmethod
    def commit_candidate(cls, cassette_path, candidate_path, expected_signature=_UNSET):
        """Atomically replace the cassette with ``candidate_path``.

        With ``expected_signature`` set (as returned by
        :meth:`version_signature` when the refresh started), the replacement
        only goes through while the cassette on disk is still the version the
        refresh started from. This stops two refreshes of the same old
        version from having the later finisher silently overwrite the earlier
        one's result. A missing baseline (the first recording) has a
        ``None`` signature, which a still-missing cassette matches.

        The signature check and the replacement run inside a cross-process
        lock, so another process can't commit in between the check and the
        replace. The candidate is kept (not deleted) if the check fails, so
        the recording is available for inspection.
        """
        cassette_path = Path(cassette_path)
        candidate_path = Path(candidate_path)
        with cls._commit_lock(cassette_path):
            if expected_signature is not _UNSET:
                current_signature = cls.version_signature(cassette_path)
                if current_signature != expected_signature:
                    raise CassetteConflictError(
                        f"Can't replace cassette {cassette_path}: it changed since this refresh "
                        f"started (another process committed a new version). The new recording is "
                        f"kept at {candidate_path} for inspection.",
                    )
            os.replace(candidate_path, cassette_path)

    @staticmethod
    def discard_candidate(candidate_path):
        """Remove a candidate file without touching the cassette."""
        with contextlib.suppress(FileNotFoundError):
            os.unlink(candidate_path)

    @staticmethod
    def version_signature(cassette_path):
        """Identity of the cassette version a refresh started from.

        ``None`` when the cassette doesn't exist (the first recording).
        Otherwise the SHA-256 of its contents. A content hash is used rather
        than inode/mtime metadata because two refreshes that finish close
        together can produce same-sized files, and an inode may be reused or a
        timestamp rounded to the same value, letting a stale baseline pass.
        Hashing the bytes reliably detects any committed replacement.
        """
        digest = hashlib.sha256()
        try:
            with open(cassette_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
        except FileNotFoundError:
            return None
        return digest.hexdigest()

    @staticmethod
    def _lock_path(cassette_path):
        cassette_path = Path(cassette_path)
        return cassette_path.with_name(f".{cassette_path.name}{LOCK_SUFFIX}")
