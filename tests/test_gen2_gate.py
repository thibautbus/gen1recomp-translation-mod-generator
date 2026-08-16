import subprocess
import unittest
from pathlib import Path

from pipeline.builder import _which_luajit

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / ".cache" / "dependencies" / "gen1recomp"
FIXTURES_ROOT = ROOT / "tools" / "gen2_gate_fixtures"
GATE_SCRIPT = ROOT / "tools" / "gate_gen2.lua"


class Gen2GateTests(unittest.TestCase):
    """tools/gate_gen2.lua is the "generation = 2" loading gate `modkit
    validate` cannot provide: its driver never injects `generation` into
    Loader.new, so a manifest naming only games=["gold"] is gated out before
    its entry chunk ever runs and still reports "ok".

    This wraps the Lua gate so it runs as part of this repo's own
    non-regression suite, not only as a standalone tool.
    """

    def test_gate_gen2_broken_and_fixed_fixtures(self):
        luajit = _which_luajit()
        if luajit is None:
            self.skipTest("luajit is unavailable")
        if not (ENGINE_ROOT / "src").is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        result = subprocess.run(
            [luajit, str(GATE_SCRIPT), str(ENGINE_ROOT), str(FIXTURES_ROOT)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"gate_gen2.lua failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("all gen2 gate checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
