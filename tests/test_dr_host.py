"""Include the DR host package's regression suite in the project suite."""
import unittest
from pathlib import Path


def load_tests(loader, tests, pattern):
    directory = Path(__file__).resolve().parents[1] / "deploy/dr/tests"
    return unittest.TestLoader().discover(str(directory), top_level_dir=str(directory))
