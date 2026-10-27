"""The only module that runs virsh. Tests replace `_run`."""
import subprocess

URI = "qemu:///system"
TIMEOUT = 120


class VirshError(Exception):
    pass


def _run(args):
    """Run `virsh -c URI <args>` and return stdout. Raise VirshError on failure."""
    cmd = ["virsh", "-c", URI] + list(args)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VirshError("virsh {}: {}".format(" ".join(args), exc))
    if res.returncode != 0:
        raise VirshError("virsh {}: {}".format(" ".join(args), res.stderr.strip()))
    return res.stdout


def configure(uri):
    global URI
    URI = uri


def state(vm):
    return _run(["domstate", vm]).strip()


def is_running(vm):
    return state(vm) == "running"


def start(vm):
    _run(["start", vm])


def shutdown(vm):
    _run(["shutdown", vm])


def snapshot_create(vm, name):
    _run(["snapshot-create-as", vm, name])


def snapshot_revert(vm, name):
    _run(["snapshot-revert", vm, name, "--running"])
