"""pytest wrapper around the built-in self-test.

ClaudeReplay's authoritative test suite lives inside the tool itself
(`python claudereplay.py --selftest`) so it ships with the single file and
needs no test framework. This wrapper just lets `pytest` users run it too.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claudereplay  # noqa: E402


def test_selftest_passes():
    assert claudereplay.run_selftest() == 0
