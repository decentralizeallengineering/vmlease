# vmlease

[![tests](https://github.com/decentralizeallengineering/vmlease/actions/workflows/tests.yml/badge.svg)](https://github.com/decentralizeallengineering/vmlease/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

Lease-based libvirt VM orchestration for AI-agent fleets. Python standard library only; it shells out to `virsh` through one small module.

## Purpose

When many agents share a few VMs, hand-run `virsh start`, `virsh destroy` and `virsh snapshot-revert` calls collide: one agent powers off a VM another is using, VMs stay on forever, and two VMs that share one passed-through GPU get started together. vmlease puts a lease in front of every VM. An agent takes a lease with a time to live and a reason, and a reaper timer reclaims VMs whose lease ran out.

## Quick start

```sh
pip install --user .            # installs ~/.local/bin/vmlease
mkdir -p ~/.config/vmlease
cat > ~/.config/vmlease/config.json <<'JSON'
{
  "managed": ["build-vm", "gpu-vm-1", "gpu-vm-2"],
  "exclusive_groups": [["gpu-vm-1", "gpu-vm-2"]],
  "virsh_uri": "qemu:///system",
  "default_ttl": "2h",
  "max_ttl": "24h"
}
JSON

export VMLEASE_HOLDER=ann-example
vmlease take build-vm --ttl 2h --why "integration run"   # starts the VM if needed
vmlease ls                                                # holder, time left, reason
vmlease renew build-vm --ttl 1h
vmlease snap build-vm clean
vmlease reset build-vm clean --yes                        # needs your live lease
vmlease drop build-vm                                     # reaper reclaims it
```

Enable the reaper (a systemd user timer, every five minutes):

```sh
mkdir -p ~/.config/systemd/user
cp systemd/vmlease-reap.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now vmlease-reap.timer
vmlease reap --dry-run                                    # preview by hand
```

## Commands

| Command | Effect |
| --- | --- |
| `take <vm> --ttl 2h --holder NAME --why TEXT` | Start the VM if needed and record the lease. Refused (exit 3) while another holder has a live lease. `--wait [SECONDS]` queues first-come first-served instead (default 300 s; exit 4 on timeout). |
| `renew <vm> [--ttl]` | Reset your lease to a fresh TTL. |
| `drop <vm> [--shutdown]` | Release your lease; the reaper shuts the VM down, or `--shutdown` does it now. |
| `ls [--json]` | Holder, time left and reason for each lease. |
| `reap [--dry-run]` | Shut down managed VMs whose lease expired. Skips everything else. |
| `snap <vm> <name>` | Create a libvirt snapshot. |
| `reset <vm> <snapshot> --yes` | Revert to a snapshot. Needs `--yes` and a live lease held by the caller. |

`--holder` may be replaced by `$VMLEASE_HOLDER`. TTLs look like `90s`, `30m`, `2h`, `1d`.

State lives in `$XDG_STATE_HOME/vmlease/` (default `~/.local/state/vmlease/`): `state.json`, a `state.lock` file taken around every change, and `events.jsonl`, one JSON object per line (`take`, `renew`, `drop`, `reap`, `reap_error`, `snap`, `reset`, `wait_timeout`). Config is read from `$VMLEASE_CONFIG` or `$XDG_CONFIG_HOME/vmlease/config.json`. `$VMLEASE_STATE_DIR` overrides the state directory.

## The rules

1. Agents lease, never virsh. An agent that needs a VM runs `vmlease take`; it never starts, stops or reverts a VM by hand.
2. Only VMs in `managed` can be leased, snapshotted, reset or reaped. Every other VM on the host is invisible to vmlease.
3. One live lease per VM. A second holder is refused or queued.
4. VMs listed together in an `exclusive_groups` entry (for example two VMs that share one GPU) are never leased at the same time, by anyone.
5. Leases expire. Renew while you work; drop when done.

## Limits

- It is advisory. Anything with libvirt access can still call `virsh` and ignore leases; use libvirt permissions if you need enforcement.
- One host and one state directory. There is no cross-host locking.
- The reaper only acts on VMs that have an expired lease record. A managed VM that was never leased, or was started by hand, is left running.
- Shutdown is a graceful `virsh shutdown`. The lease record is removed once the request succeeds, so a guest that ignores ACPI stays on and is no longer tracked.
- `--wait` polls every 2 seconds, and a waiter that stops polling is dropped from the queue after 30 seconds.
- The wall clock is used for expiry; a large clock jump shifts lease deadlines.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

The tests replace the single `virsh` call point with an in-memory stub, so no libvirt is needed.

## License

Apache-2.0, see `LICENSE` and `NOTICE`.
