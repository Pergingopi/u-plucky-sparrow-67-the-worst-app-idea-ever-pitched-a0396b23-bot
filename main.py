#!/usr/bin/env python3
"""Write the make.fun entry for "The Worst App Idea Ever Pitched".

Writes one sentence (1-240 characters, no trailing newline) to
``$MAKEFUN_OUTPUT_DIR/entry.txt`` (default ``/output``).

Deterministic: the same bytes are written on every run, regardless of
the caller, the working directory, or the environment (apart from an
explicit ``MAKEFUN_OUTPUT_DIR`` override). The write is crash-safe:
a temp file is created in the same directory with ``O_EXCL``, fsynced,
chmodded 0644, atomically renamed over the target, then the directory
is fsynced; the result is read back and verified byte for byte. The
output directory itself is never deleted, and stale temp files left
behind by an earlier crashed run are cleaned up first.

Exit status is 0 on success. Any problem -- an invalid entry, an
unwritable directory, a failed verification -- raises an explicit
exception with a descriptive message instead of failing silently or
through an assertion, so a bad run can never be mistaken for success.
"""

import os
import sys
import tempfile
import time

ENTRY = (
    "ClosetCheck is a premium subscription app that sends you a daily "
    "push notification confirming your umbrella is still in the closet, "
    "untouched, exactly where you left it in 2019, for only $4.99 a month."
)

MAX_CHARS = 240
MIN_CHARS = 1
ENTRY_MODE = 0o644
STALE_TMP_PREFIX = ".entry-"
STALE_TMP_SUFFIX = ".tmp"
CLEANUP_ATTEMPTS = 3
CLEANUP_RETRY_DELAY_SECONDS = 0.05


def validate_entry(text):
    """Check *text* against every rule the sealed scorer enforces.

    Raises ``ValueError`` with a specific message on the first failure:
    empty or over-long text, leading/trailing whitespace, or any control
    character (including DEL). The checks run in a fixed order so the
    reported failure is deterministic.
    """
    if not isinstance(text, str):
        raise ValueError("entry must be a string, got %s" % type(text).__name__)
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        raise ValueError(
            "entry must be %d-%d characters, got %d"
            % (MIN_CHARS, MAX_CHARS, len(text))
        )
    if text != text.strip():
        raise ValueError("entry must not start or end with whitespace")
    if not text.strip():
        raise ValueError("entry must not be only whitespace")
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise ValueError("entry contains a control character: U+%04X" % code)
    return text


def resolve_output_dir():
    """Return the directory ``entry.txt`` is written to.

    ``MAKEFUN_OUTPUT_DIR`` wins when set to a non-empty, non-whitespace
    value; an empty or whitespace-only value is treated as unset (a
    common misconfiguration when the variable is exported but blank),
    and the default ``/output`` is used instead. The directory is
    created if it does not exist.
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    out_dir = raw.strip() if raw is not None else ""
    if not out_dir:
        out_dir = "/output"
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isdir(out_dir):
        raise RuntimeError("output path is not a directory: %s" % out_dir)
    return out_dir


def _remove_quietly(path):
    """Best-effort unlink; returns True if the path is gone afterwards."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def cleanup_stale_tmp_files(out_dir):
    """Remove leftover ``.entry-*.tmp`` files from crashed earlier runs.

    Only files this program itself could have created (matching its own
    prefix and suffix) are touched; anything else in the directory is
    left alone. Each removal is retried briefly in case another process
    is momentarily holding the file, and a file that still cannot be
    removed is reported on stderr rather than aborting the run -- the
    new temp file is created with ``O_EXCL`` under a unique name, so a
    stale file cannot corrupt the fresh write.
    """
    try:
        names = os.listdir(out_dir)
    except OSError as exc:
        raise RuntimeError("cannot list output directory %s: %s" % (out_dir, exc))
    for name in names:
        if not (name.startswith(STALE_TMP_PREFIX) and name.endswith(STALE_TMP_SUFFIX)):
            continue
        path = os.path.join(out_dir, name)
        removed = False
        for _attempt in range(CLEANUP_ATTEMPTS):
            if _remove_quietly(path):
                removed = True
                break
            time.sleep(CLEANUP_RETRY_DELAY_SECONDS)
        if not removed:
            print("warning: could not remove stale temp file %s" % path, file=sys.stderr)


def _fsync_directory(out_dir):
    """Flush directory metadata so the rename survives a crash."""
    dir_fd = os.open(out_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_entry_atomically(out_dir, data):
    """Write *data* to ``entry.txt`` in *out_dir* via atomic rename.

    The temp file is created with ``O_EXCL`` in the same directory,
    written, fsynced and chmodded 0644 before ``os.replace`` moves it
    over the target; the directory is fsynced afterwards. On any error
    the temp file is removed and the exception propagates -- the old
    target, if any, is never left half-written.
    """
    target = os.path.join(out_dir, "entry.txt")
    fd, tmp_path = tempfile.mkstemp(
        prefix=STALE_TMP_PREFIX, suffix=STALE_TMP_SUFFIX, dir=out_dir
    )
    try:
        os.fchmod(fd, ENTRY_MODE)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, ENTRY_MODE)
        os.replace(tmp_path, target)
        os.chmod(target, ENTRY_MODE)
        _fsync_directory(out_dir)
    except BaseException:
        _remove_quietly(tmp_path)
        raise
    return target


def verify_written(target, data):
    """Read *target* back and confirm it holds exactly *data* at 0644.

    Raises ``RuntimeError`` on any mismatch, so a silent short write or
    a wrong mode can never pass as a successful run.
    """
    with open(target, "rb") as f:
        written = f.read()
    if written != data:
        raise RuntimeError(
            "read-back verification failed: wrote %d bytes, read %d"
            % (len(data), len(written))
        )
    mode = os.stat(target).st_mode & 0o777
    if mode != ENTRY_MODE:
        raise RuntimeError(
            "entry.txt mode is %04o, expected %04o" % (mode, ENTRY_MODE)
        )


def main():
    validate_entry(ENTRY)
    data = ENTRY.encode("utf-8")
    out_dir = resolve_output_dir()
    cleanup_stale_tmp_files(out_dir)
    target = write_entry_atomically(out_dir, data)
    verify_written(target, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
