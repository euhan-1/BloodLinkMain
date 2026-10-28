"""The deployed instance has 512 MB and neither scikit-learn nor joblib (requirements-dev.txt
only). The AI-recommended order runs there, so the whole path - import, rules load,
feature extraction, tree walk - must work with both packages unimportable. This runs it in a
fresh interpreter whose import system refuses them; a stray module-level `import joblib` (it
used to be the first line of sarimax_selector_common) fails this test instead of the deploy.

Run from server/: .venv\\Scripts\\python.exe -m pytest tests/test_sarimax_selector_import.py -q
"""
import subprocess
import sys
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import importlib.abc, sys

class Blocked(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("joblib", "sklearn"):
            raise ImportError(f"{name} is not installed on the deployed instance")
sys.meta_path.insert(0, Blocked())
sys.path.insert(0, sys.argv[1])

import numpy as np
import sarimax_selector_common as sel

rules = sel.load_selector_rules()
assert rules is not None, "the committed rules file must load without joblib/sklearn"
x = np.arange(154)
rng = np.random.default_rng(0)
values = np.maximum(0, 80 + 6 * np.sin(2 * np.pi * x / 7) + rng.normal(0, 5, 154)).round()
order, seasonal = sel.recommend_order_from_series(values, rules)
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("joblib", "sklearn"))
assert not loaded, loaded
print("OK", order, seasonal)
"""


class LeanImportTests(unittest.TestCase):
    def test_import_load_and_recommend_work_with_joblib_and_sklearn_unimportable(self):
        proc = subprocess.run([sys.executable, "-c", SCRIPT, str(SERVER)], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_the_guard_actually_blocks(self):
        """Control: the blocker is real, so a pass above means something."""
        probe = SCRIPT.split("import numpy")[0] + "\ntry:\n    import joblib\nexcept ImportError:\n    print('BLOCKED')\n"
        proc = subprocess.run([sys.executable, "-c", probe, str(SERVER)], capture_output=True, text=True, timeout=60)
        self.assertIn("BLOCKED", proc.stdout, proc.stdout + proc.stderr)
