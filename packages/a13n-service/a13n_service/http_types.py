"""Shared HTTP parameter declarations."""

from typing import Annotated

from fastapi import Header

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]
