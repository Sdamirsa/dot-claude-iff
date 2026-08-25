#!/usr/bin/env python3
"""run_tests.py - the whole suite, one command.

`.claude` is a dotted directory, which makes plain `unittest discover` fussy about package
roots. This runner sets the paths explicitly so `python3 .claude/tools/tests/run_tests.py`
works from anywhere, and so a project adopting this system has one command to trust.

    python3 .claude/tools/tests/run_tests.py            # everything
    python3 .claude/tools/tests/run_tests.py hooks obs  # only matching modules
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for path in (str(TOOLS), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)


def main(argv: list) -> int:
    selectors = [a.lower() for a in argv if not a.startswith("-")]
    verbosity = 1 if "-q" in argv else 2

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    found = []
    for module_path in sorted(HERE.glob("test_*.py")):
        name = module_path.stem
        if selectors and not any(sel in name for sel in selectors):
            continue
        found.append(name)
        suite.addTests(loader.loadTestsFromName(name))

    if not found:
        print(f"no test modules matched {selectors or '*'}", file=sys.stderr)
        return 2

    # stderr, because TextTestRunner also writes there: mixing streams reorders the output.
    print(f"running {len(found)} module(s): {', '.join(found)}\n", file=sys.stderr, flush=True)
    result = unittest.TextTestRunner(verbosity=verbosity, buffer=False).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"\n{passed} passed · {len(result.failures)} failed · {len(result.errors)} errored "
          f"· {len(result.skipped)} skipped")
    print("TESTS_OK" if result.wasSuccessful() else "TESTS_FAIL")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
