"""Configuration: a JSON file with documented defaults.

Location: $VMLEASE_CONFIG, else $XDG_CONFIG_HOME/vmlease/config.json
(default ~/.config/vmlease/config.json). A missing file means defaults.
"""
import json
import os
import re

DEFAULTS = {
    # VM names agents may lease and the reaper may shut down. Empty = none.
    "managed": [],
    # Lists of VM names that must never be leased at the same time
    # (for example VMs that share one passed-through GPU).
    "exclusive_groups": [],
    # libvirt connection URI handed to virsh -c.
    "virsh_uri": "qemu:///system",
    # TTL used when --ttl is omitted, and the upper bound for any TTL.
    "default_ttl": "2h",
    "max_ttl": "24h",
}


class ConfigError(Exception):
    pass


def config_path():
    explicit = os.environ.get("VMLEASE_CONFIG")
    if explicit:
        return explicit
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "vmlease", "config.json")


def load(path=None):
    path = path or config_path()
    cfg = dict(DEFAULTS)
    try:
        with open(path) as fh:
            user = json.load(fh)
    except FileNotFoundError:
        return cfg
    except ValueError as exc:
        raise ConfigError(f"bad config {path}: {exc}")
    if not isinstance(user, dict):
        raise ConfigError(f"bad config {path}: top level must be an object")
    unknown = set(user) - set(DEFAULTS)
    if unknown:
        raise ConfigError("unknown config keys: {}".format(", ".join(sorted(unknown))))
    cfg.update(user)
    return cfg


_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text):
    """Parse '90', '90s', '30m', '2h', '1d' into seconds."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", str(text))
    if not m:
        raise ConfigError(f"bad duration {text!r} (use e.g. 30m, 2h, 1d)")
    return int(m.group(1)) * _UNITS[m.group(2) or "s"]


def fmt_duration(seconds):
    seconds = int(seconds)
    if seconds <= 0:
        return "expired"
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
