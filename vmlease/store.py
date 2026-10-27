"""JSON state file, lock file and JSONL event log under XDG_STATE_HOME."""
import contextlib
import fcntl
import json
import os
import time


def state_dir():
    explicit = os.environ.get("VMLEASE_STATE_DIR")
    if explicit:
        return explicit
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "vmlease")


def _path(name):
    return os.path.join(state_dir(), name)


@contextlib.contextmanager
def locked():
    """Exclusive advisory lock around every read-modify-write of the state."""
    os.makedirs(state_dir(), exist_ok=True)
    with open(_path("state.lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load():
    try:
        with open(_path("state.json")) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    data.setdefault("leases", {})
    data.setdefault("waiters", {})
    return data


def save(data):
    tmp = _path("state.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, _path("state.json"))


def log_event(event, ts=None, **fields):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(ts if ts is not None else time.time())),
           "event": event}
    rec.update(fields)
    os.makedirs(state_dir(), exist_ok=True)
    with open(_path("events.jsonl"), "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
