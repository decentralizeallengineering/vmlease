"""Lease operations. One lease per VM; every change happens under the lock."""
import time

from . import config as cfgmod
from . import store, virsh

WAITER_STALE = 30  # seconds without a heartbeat before a queued waiter is ignored
POLL = 2           # seconds between attempts while waiting


def now():
    return time.time()


class LeaseError(Exception):
    def __init__(self, msg, code=1):
        Exception.__init__(self, msg)
        self.code = code


class Busy(LeaseError):
    """The VM cannot be leased right now; --wait may retry."""

    def __init__(self, msg):
        LeaseError.__init__(self, msg, 3)


def _live(lease):
    return lease is not None and lease["expires"] > now()


def _require_managed(cfg, vm):
    if vm not in cfg["managed"]:
        raise LeaseError(f"{vm} is not in the managed allowlist", 2)


def _ttl_seconds(cfg, ttl):
    secs = cfgmod.parse_duration(ttl if ttl else cfg["default_ttl"])
    cap = cfgmod.parse_duration(cfg["max_ttl"])
    if secs <= 0 or secs > cap:
        raise LeaseError("ttl must be between 1s and {}".format(cfg["max_ttl"]), 2)
    return secs


def _group_peers(cfg, vm):
    peers = set()
    for group in cfg["exclusive_groups"]:
        if vm in group:
            peers.update(g for g in group if g != vm)
    return peers


def _check_free(cfg, data, vm, holder, wait):
    lease = data["leases"].get(vm)
    if _live(lease) and lease["holder"] != holder:
        raise Busy("{} is leased by {} ({} left): {}".format(
            vm, lease["holder"], cfgmod.fmt_duration(lease["expires"] - now()),
            lease["why"]))
    for peer in sorted(_group_peers(cfg, vm)):
        plea = data["leases"].get(peer)
        if _live(plea):
            raise Busy("{} shares a GPU group with {}, leased by {}".format(
                vm, peer, plea["holder"]))
    if wait or data["waiters"].get(vm):
        queue = [w for w in data["waiters"].get(vm, [])
                 if now() - w["beat"] < WAITER_STALE]
        if queue and queue[0]["holder"] != holder:
            raise Busy("{} has a queue; {} is first".format(vm, queue[0]["holder"]))


def _beat(data, vm, holder):
    queue = data["waiters"].setdefault(vm, [])
    for w in queue:
        if w["holder"] == holder:
            w["beat"] = now()
            return
    queue.append({"holder": holder, "since": now(), "beat": now()})


def _unqueue(data, vm, holder):
    queue = [w for w in data["waiters"].get(vm, []) if w["holder"] != holder]
    if queue:
        data["waiters"][vm] = queue
    else:
        data["waiters"].pop(vm, None)


def take(cfg, vm, holder, ttl=None, why="", wait=0):
    _require_managed(cfg, vm)
    secs = _ttl_seconds(cfg, ttl)
    deadline = now() + wait
    while True:
        with store.locked():
            data = store.load()
            if wait:
                _beat(data, vm, holder)
            try:
                _check_free(cfg, data, vm, holder, wait)
            except Busy as exc:
                if not wait:
                    raise
                if now() >= deadline:
                    _unqueue(data, vm, holder)
                    store.save(data)
                    store.log_event("wait_timeout", vm=vm, holder=holder)
                    raise LeaseError(f"timed out waiting for {vm}: {exc}", 4)
                store.save(data)
                busy = True
            else:
                busy = False
                started = False
                if not virsh.is_running(vm):
                    virsh.start(vm)
                    started = True
                _unqueue(data, vm, holder)
                lease = {"holder": holder, "why": why, "acquired": now(),
                         "expires": now() + secs}
                data["leases"][vm] = lease
                store.save(data)
                store.log_event("take", vm=vm, holder=holder, why=why, ttl=secs,
                                started=started)
                return lease
        if busy:
            time.sleep(POLL)


def _own_live(data, vm, holder):
    lease = data["leases"].get(vm)
    if not _live(lease) or lease["holder"] != holder:
        raise LeaseError(f"{vm} has no live lease held by {holder}", 5)
    return lease


def renew(cfg, vm, holder, ttl=None):
    secs = _ttl_seconds(cfg, ttl)
    with store.locked():
        data = store.load()
        lease = _own_live(data, vm, holder)
        lease["expires"] = now() + secs
        store.save(data)
        store.log_event("renew", vm=vm, holder=holder, ttl=secs)
        return lease


def drop(cfg, vm, holder, shutdown=False):
    """Release a lease. The reaper (or --shutdown) then reclaims the VM."""
    with store.locked():
        data = store.load()
        _own_live(data, vm, holder)
        if shutdown:
            virsh.shutdown(vm)
            del data["leases"][vm]
        else:
            data["leases"][vm]["expires"] = now()
        store.save(data)
        store.log_event("drop", vm=vm, holder=holder, shutdown=shutdown)


def list_leases():
    with store.locked():
        data = store.load()
    rows = []
    for vm, lease in sorted(data["leases"].items()):
        rows.append({"vm": vm, "holder": lease["holder"], "why": lease["why"],
                     "left": max(0, int(lease["expires"] - now()))})
    return rows


def reap(cfg, dry_run=False):
    """Shut down managed VMs whose lease expired and that hold no live lease.

    Returns a list of (vm, action) pairs. Unmanaged VMs are never touched.
    """
    actions = []
    with store.locked():
        data = store.load()
        for vm, lease in sorted(data["leases"].items()):
            if vm not in cfg["managed"] or _live(lease):
                continue
            try:
                running = virsh.is_running(vm)
                if running and not dry_run:
                    virsh.shutdown(vm)
            except virsh.VirshError as exc:
                actions.append((vm, f"error: {exc}"))
                if not dry_run:
                    store.log_event("reap_error", vm=vm, error=str(exc))
                continue
            actions.append((vm, "would shut down" if dry_run and running
                            else "shut down" if running else "release stale lease"))
            if not dry_run:
                del data["leases"][vm]
                store.log_event("reap", vm=vm, holder=lease["holder"],
                                shutdown=running)
        if not dry_run:
            store.save(data)
    return actions


def snap(cfg, vm, name):
    _require_managed(cfg, vm)
    virsh.snapshot_create(vm, name)
    store.log_event("snap", vm=vm, name=name)


def reset(cfg, vm, snapshot, holder, yes):
    _require_managed(cfg, vm)
    if not yes:
        raise LeaseError("reset discards VM state; pass --yes", 2)
    with store.locked():
        data = store.load()
        _own_live(data, vm, holder)
        virsh.snapshot_revert(vm, snapshot)
        store.log_event("reset", vm=vm, holder=holder, snapshot=snapshot)
