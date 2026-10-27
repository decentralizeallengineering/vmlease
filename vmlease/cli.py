"""Command line entry point."""
import argparse
import json
import os
import sys

from . import config as cfgmod
from . import core, virsh


def _holder(args):
    holder = args.holder or os.environ.get("VMLEASE_HOLDER")
    if not holder:
        raise core.LeaseError("--holder (or VMLEASE_HOLDER) is required", 2)
    return holder


def _parser():
    p = argparse.ArgumentParser(
        prog="vmlease", description="Lease-based libvirt VM orchestration.")
    p.add_argument("--config", help="config file (default: see README)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, holder=True):
        sp = sub.add_parser(name, help=help_)
        if holder:
            sp.add_argument("--holder", help="lease holder (or $VMLEASE_HOLDER)")
        return sp

    sp = add("take", "lease a VM, starting it if needed")
    sp.add_argument("vm")
    sp.add_argument("--ttl", help="e.g. 30m, 2h (default from config)")
    sp.add_argument("--why", default="", help="reason, shown in ls")
    sp.add_argument("--wait", nargs="?", const=300, default=0, type=int,
                    metavar="SECONDS", help="queue instead of refusing (default 300s)")
    sp = add("renew", "extend your lease")
    sp.add_argument("vm")
    sp.add_argument("--ttl")
    sp = add("drop", "release your lease")
    sp.add_argument("vm")
    sp.add_argument("--shutdown", action="store_true", help="shut down now")
    sp = add("ls", "list leases", holder=False)
    sp.add_argument("--json", action="store_true")
    sp = add("reap", "shut down VMs with expired leases", holder=False)
    sp.add_argument("--dry-run", action="store_true")
    sp = add("snap", "create a snapshot", holder=False)
    sp.add_argument("vm")
    sp.add_argument("name")
    sp = add("reset", "revert a VM to a snapshot (needs your live lease)")
    sp.add_argument("vm")
    sp.add_argument("snapshot")
    sp.add_argument("--yes", action="store_true", help="confirm data loss")
    return p


def _run(args):
    cfg = cfgmod.load(args.config)
    virsh.configure(cfg["virsh_uri"])
    if args.cmd == "take":
        lease = core.take(cfg, args.vm, _holder(args), args.ttl, args.why, args.wait)
        print("leased {} to {} for {}".format(
            args.vm, lease["holder"],
            cfgmod.fmt_duration(lease["expires"] - core.now())))
    elif args.cmd == "renew":
        lease = core.renew(cfg, args.vm, _holder(args), args.ttl)
        print("renewed {}: {} left".format(
            args.vm, cfgmod.fmt_duration(lease["expires"] - core.now())))
    elif args.cmd == "drop":
        core.drop(cfg, args.vm, _holder(args), args.shutdown)
        print(f"dropped {args.vm}")
    elif args.cmd == "ls":
        rows = core.list_leases()
        if args.json:
            print(json.dumps(rows))
        else:
            for r in rows:
                left = cfgmod.fmt_duration(r["left"])
                print(f"{r['vm']:<20} {r['holder']:<16} {left:<8} {r['why']}")
    elif args.cmd == "reap":
        for vm, action in core.reap(cfg, args.dry_run):
            print(f"{vm}: {action}")
    elif args.cmd == "snap":
        core.snap(cfg, args.vm, args.name)
        print(f"snapshot {args.name} of {args.vm} created")
    elif args.cmd == "reset":
        core.reset(cfg, args.vm, args.snapshot, _holder(args), args.yes)
        print(f"reset {args.vm} to {args.snapshot}")


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        _run(args)
    except core.LeaseError as exc:
        print(f"vmlease: {exc}", file=sys.stderr)
        return exc.code
    except (cfgmod.ConfigError, virsh.VirshError) as exc:
        print(f"vmlease: {exc}", file=sys.stderr)
        return 1
    return 0
