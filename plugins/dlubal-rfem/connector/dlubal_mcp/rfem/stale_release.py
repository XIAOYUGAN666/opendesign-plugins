"""Release journal records whose uncertainty has provably ended.

Standard library only, and importable without the rest of dlubal_mcp: the
Connector's Setup helper runs this exact file with the application's bundled
Python against journals written by OLDER servers, so machines already blocked by
a stale record recover without an operator. Keep it self-contained.

An unfinished record blocks new work because nobody knows whether the model was
changed partly, fully or not at all. That uncertainty lives in the application's
memory. Once the application session that ran the operation has ended, unsaved
changes are gone and the model reopens from its last save, so the record is
released automatically. Two facts must both be proven, never guessed:

- no worker is alive anywhere: the journal's worker lock can be taken (the OS
  releases it on process death, and a live worker holds it for its whole task);
- the session has ended: every running process of the product was started after
  the operation began. A process whose start time cannot be read, or a process
  list that cannot be taken, counts as "still running".

A record whose session is still open keeps blocking; closing the application
after inspecting the model releases it. Nothing is ever re-executed.
"""

import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager

# Image-name prefixes per product, case-insensitive. A prefix, not an exact
# name: matching more processes can only keep a record blocked longer.
#
# RSECTION's is "rsection", not "rsection1": RSECTION 1 runs as RSECTION.exe
# (1.10 to 1.15 checked), and only its headless server is RSECTION1Server.exe.
# Through 1.0.17 the prefix missed the application itself, found no process,
# and read that as "session ended" — releasing a record while the RSECTION that
# may hold its partial change was still open. A shorter prefix is the safe side.
PRODUCT_IMAGE_PREFIXES = {"rfem": "rfem6", "rstab": "rstab9", "rsection": "rsection"}
PRODUCT_LABELS = {"rfem": "RFEM", "rstab": "RSTAB", "rsection": "RSECTION"}

DEFAULT_GRACE_SECONDS = 30
# Matches the bound execution.py applies to worker errors.
MAX_ERROR_CHARS = 8192

# Seconds between the Windows FILETIME epoch (1601) and the Unix epoch.
_FILETIME_UNIX_OFFSET = 11644473600

CLEARED_ERROR = (
    "Execution owner process ended before any model operation; nothing was changed in RFEM. "
    "RFEM may still be finishing a request that process had sent (for example a long "
    "get_results); if the next script reports that another client is connected, "
    "wait a minute or two and retry."
)
HELD_ERROR = "Execution owner is gone after a model operation. Operator verification of RFEM is required; inspect partial changes, then resolve this execution."
RELEASED_ERROR = (
    "Released automatically: the {label} session that ran this operation has ended, "
    "so no unsaved partial change remains. Changes saved to the model file before "
    "the interruption are possible; re-read the model before relying on it."
)


@contextmanager
def worker_lock(path):
    """OS releases this lock on process death; recovery cannot unlock live work."""
    with open(str(path) + ".worker-lock", "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _process_start_times(prefix):
    """Unix start times of every process whose image name starts with prefix.

    None when the list or any matching process cannot be read: an unreadable
    process might be the session that ran the operation.
    """
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    snapshot_all_processes, query_limited_information = 0x2, 0x1000
    invalid = wintypes.HANDLE(-1).value

    snapshot = kernel32.CreateToolhelp32Snapshot(snapshot_all_processes, 0)
    if not snapshot or snapshot == invalid:
        return None
    times = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        if not more:
            return None
        while more:
            if entry.szExeFile.lower().startswith(prefix):
                handle = kernel32.OpenProcess(
                    query_limited_information, False, entry.th32ProcessID
                )
                if not handle:
                    return None
                try:
                    created, ignored = ctypes.c_ulonglong(), ctypes.c_ulonglong()
                    if not kernel32.GetProcessTimes(
                        handle,
                        ctypes.byref(created),
                        ctypes.byref(ignored),
                        ctypes.byref(ignored),
                        ctypes.byref(ignored),
                    ):
                        return None
                    times.append(created.value / 1e7 - _FILETIME_UNIX_OFFSET)
                finally:
                    kernel32.CloseHandle(handle)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return times


def _unavailable_start_times(prefix):
    return None


# Replaceable in tests. Off Windows nothing can be proven, so nothing releases.
start_times = _process_start_times if os.name == "nt" else _unavailable_start_times


def session_ended(product, started):
    """True only when the product session that ran an operation provably ended.

    No running process of the product at all also counts as ended.
    """
    prefix = PRODUCT_IMAGE_PREFIXES.get(product)
    if prefix is None:
        return False
    try:
        times = start_times(prefix)
    except (OSError, AttributeError, ValueError):
        return False
    return times is not None and all(created > started for created in times)


def _has_candidates(path, grace_seconds):
    """Read-only look before taking the worker lock.

    A worker that cannot take the lock fails, so the lock is only taken when a
    record may need it. Records only qualify while admission is refused anyway
    (a long live task or an unresolved one), so no worker can be starting then.
    """
    db = sqlite3.connect(str(path), timeout=5)
    try:
        if not db.execute("PRAGMA table_info(executions)").fetchall():
            return False
        return (
            db.execute(
                "SELECT 1 FROM executions WHERE (state='running' AND started < ?)"
                " OR state='unknown' LIMIT 1",
                (time.time() - grace_seconds,),
            ).fetchone()
            is not None
        )
    finally:
        db.close()


def release(path, product="rfem", grace_seconds=DEFAULT_GRACE_SECONDS):
    """Resolve abandoned and uncertain records that no longer need a person.

    Returns counts: cleared (never wrote, as since 1.0.15), released (session
    ended), held (moved to 'unknown' now), and live (the worker lock was taken
    by someone else, so nothing was examined).
    """
    counts = {"cleared": 0, "released": 0, "held": 0, "live": False}
    if not _has_candidates(path, grace_seconds):
        return counts
    released_error = RELEASED_ERROR.format(
        label=PRODUCT_LABELS.get(product, product.upper())
    )
    try:
        with worker_lock(path):
            db = sqlite3.connect(str(path), timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    columns = {
                        r[1] for r in db.execute("PRAGMA table_info(executions)")
                    }
                    if not columns:
                        return counts
                    # A journal written before 1.0.15 has no mutation record, so an
                    # unfinished row from that era cannot be proven harmless. Never
                    # migrate an older server's schema from here.
                    mutated = "mutated" if "mutated" in columns else "1 AS mutated"
                    # The grace period keeps this off a 'running' row whose worker
                    # was admitted but has not yet reached its own lock acquisition.
                    # An 'unknown' row is written by a worker that already ended.
                    rows = db.execute(
                        f"SELECT id, state, error, {mutated}, started FROM executions"
                        " WHERE (state='running' AND started < ?) OR state='unknown'",
                        (time.time() - grace_seconds,),
                    ).fetchall()
                    ended = {}
                    for row in rows:
                        if row["state"] == "running" and not row["mutated"]:
                            state, error, key = "failed", CLEARED_ERROR, "cleared"
                        else:
                            started = row["started"]
                            if started not in ended:
                                ended[started] = session_ended(product, started)
                            if ended[started]:
                                # Keep what the record said before: the original
                                # unknown result stays readable after release.
                                previous = f" Previous state {row['state']}: {row['error'] or '-'}"
                                error = (released_error + previous)[:MAX_ERROR_CHARS]
                                state, key = "failed", "released"
                            elif row["state"] == "running":
                                state, error, key = "unknown", HELD_ERROR, "held"
                            else:
                                continue
                        db.execute(
                            "UPDATE executions SET state=?, error=?, finished=? WHERE id=? AND state=?",
                            (state, error, time.time(), row["id"], row["state"]),
                        )
                        counts[key] += 1
            finally:
                db.close()
    except OSError:
        counts["live"] = True
    return counts


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Release journal records whose worker is gone and whose application session has ended. Never reruns a script."
    )
    parser.add_argument("--state-file", required=True)
    parser.add_argument(
        "--product", choices=sorted(PRODUCT_IMAGE_PREFIXES), required=True
    )
    args = parser.parse_args()
    if not os.path.isfile(args.state_file):
        print(json.dumps({"error": "journal not found"}))
        sys.exit(2)
    try:
        result = release(args.state_file, args.product)
    except sqlite3.Error as exc:
        print(json.dumps({"error": type(exc).__name__}))
        sys.exit(1)
    print(json.dumps(result))
