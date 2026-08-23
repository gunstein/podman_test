import os

import pytest


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "ignore_https_errors": os.getenv("E2E_IGNORE_HTTPS_ERRORS") == "true",
    }
