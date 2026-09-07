"""Defaults shared by Environment admission, maintenance, and process settings."""

from datetime import timedelta

DEFAULT_MAX_TARGETS = 1_000
DEFAULT_MAX_ACTIVE = 100
DEFAULT_BATCH_SIZE = 64
RENEWAL_MARGIN = timedelta(seconds=60)
FAILURE_BACKOFF = timedelta(seconds=30)
