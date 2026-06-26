# Hypothesis configuration for uxok Framework property-based testing.
# Environment detection consumed by tests/properties/conftest.py.

import os

CI_ENVIRONMENT = os.getenv("CI", "false").lower() == "true"

__all__ = ["CI_ENVIRONMENT"]
