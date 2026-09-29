"""Shared pytest fixtures for all test files."""

from pathlib import Path
import pytest


@pytest.fixture
def repo_root():
    """Return the repository root directory (parent of tests/)."""
    return Path(__file__).resolve().parent.parent


# Module-level constant for files that use BASE directly at import time
REPO_ROOT = Path(__file__).resolve().parent.parent
