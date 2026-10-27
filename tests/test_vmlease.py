import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from vmlease import cli, core, virsh


class FakeVirsh:
    """Stands in for virsh._run; keeps a domain state table and a call log."""

    def __init__(self, states):
        self.states = dict(states)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        verb, vm = args[0], args[1]
        if vm not in self.states:
            raise virsh.VirshError(f"no such domain {vm}")
        if verb == "domstate":
            return self.states[vm] + "\n"
        if verb == "start":
            self.states[vm] = "running"
        elif verb == "shutdown":
            self.states[vm] = "shut off"
        return ""

    def verbs(self, verb):
        return [c[1] for c in self.calls if c[0] == verb]


class Base(unittest.TestCase):
    managed = ("vm-a", "vm-b", "vm-c")
    groups = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg_path = os.path.join(self.tmp.name, "config.json")
        with open(cfg_path, "w") as fh:
            json.dump({"managed": self.managed, "exclusive_groups": self.groups}, fh)
        env = mock.patch.dict(os.environ, {
            "VMLEASE_STATE_DIR": os.path.join(self.tmp.name, "state"),
            "VMLEASE_CONFIG": cfg_path})
        env.start()
        self.addCleanup(env.stop)
        self.fake = FakeVirsh({"vm-a": "shut off", "vm-b": "shut off",
                               "vm-c": "running", "vm-other": "running"})
        patch = mock.patch.object(virsh, "_run", self.fake)
        patch.start()
        self.addCleanup(patch.stop)
        self.clock = [1_000_000.0]
        clk = mock.patch.object(core, "now", lambda: self.clock[0])
        clk.start()
        self.addCleanup(clk.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def events(self):
        path = os.path.join(self.tmp.name, "state", "events.jsonl")
        with open(path) as fh:
            return [json.loads(line) for line in fh]


class TakeAndLs(Base):
    def test_take_starts_vm_and_ls_shows_lease(self):
        code, _, _ = self.run_cli("take", "vm-a", "--ttl", "2h",
                                  "--holder", "ann", "--why", "build check")
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.states["vm-a"], "running")
        self.clock[0] += 600
        code, out, _ = self.run_cli("ls", "--json")
        rows = json.loads(out)
        self.assertEqual(rows, [{"vm": "vm-a", "holder": "ann",
                                 "why": "build check", "left": 6600}])
        self.assertEqual(self.events()[0]["event"], "take")


class Conflict(Base):
    def test_second_holder_refused_and_unmanaged_refused(self):
        self.assertEqual(self.run_cli("take", "vm-a", "--holder", "ann")[0], 0)
        code, _, err = self.run_cli("take", "vm-a", "--holder", "bob")
        self.assertEqual(code, 3)
        self.assertIn("leased by ann", err)
        # --wait with a short timeout queues, then gives up
        with mock.patch.object(core.time, "sleep",
                               lambda s: self.clock.__setitem__(0, self.clock[0] + 10)):
            code, _, err = self.run_cli("take", "vm-a", "--holder", "bob", "--wait", "25")
        self.assertEqual(code, 4)
        self.assertIn("timed out", err)
        # after the holder drops, the other holder can take it
        self.assertEqual(self.run_cli("drop", "vm-a", "--holder", "ann")[0], 0)
        self.assertEqual(self.run_cli("take", "vm-a", "--holder", "bob")[0], 0)
        code, _, err = self.run_cli("take", "vm-other", "--holder", "ann")
        self.assertEqual(code, 2)


class Reap(Base):
    def test_reap_shuts_down_only_expired(self):
        self.run_cli("take", "vm-a", "--ttl", "10m", "--holder", "ann")
        self.run_cli("take", "vm-b", "--ttl", "3h", "--holder", "bob")
        self.clock[0] += 1800
        _, out, _ = self.run_cli("reap", "--dry-run")
        self.assertIn("vm-a: would shut down", out)
        self.assertEqual(self.fake.verbs("shutdown"), [])
        _code, out, _ = self.run_cli("reap")
        self.assertEqual(self.fake.verbs("shutdown"), ["vm-a"])
        self.assertEqual(self.fake.states["vm-b"], "running")
        rows = json.loads(self.run_cli("ls", "--json")[1])
        self.assertEqual([r["vm"] for r in rows], ["vm-b"])


class Allowlist(Base):
    managed = ("vm-a",)

    def test_reap_ignores_unmanaged(self):
        self.run_cli("take", "vm-a", "--ttl", "1m", "--holder", "ann")
        # a stale lease record for a VM that left the allowlist must be ignored
        state = os.path.join(self.tmp.name, "state", "state.json")
        with open(state) as fh:
            data = json.load(fh)
        data["leases"]["vm-other"] = dict(data["leases"]["vm-a"])
        with open(state, "w") as fh:
            json.dump(data, fh)
        self.clock[0] += 3600
        self.run_cli("reap")
        self.assertEqual(self.fake.verbs("shutdown"), ["vm-a"])
        self.assertEqual(self.fake.states["vm-other"], "running")


class GpuGroup(Base):
    groups = (("vm-a", "vm-b"),)

    def test_second_vm_in_group_refused(self):
        self.assertEqual(self.run_cli("take", "vm-a", "--holder", "ann")[0], 0)
        code, _, err = self.run_cli("take", "vm-b", "--holder", "bob")
        self.assertEqual(code, 3)
        self.assertIn("GPU group", err)
        self.assertEqual(self.fake.states["vm-b"], "shut off")
        # vm-c is outside the group and unaffected
        self.assertEqual(self.run_cli("take", "vm-c", "--holder", "bob")[0], 0)


if __name__ == "__main__":
    unittest.main()
