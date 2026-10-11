"""Test-wide environment defaults applied before the app settings are imported."""

import os

os.environ.setdefault("JOB_WORKER_EMBEDDED", "false")
