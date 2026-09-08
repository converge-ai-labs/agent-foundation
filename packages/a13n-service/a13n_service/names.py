"""Shared display-label bounds; features retain their normalization policies."""

from typing import Annotated

from pydantic import StringConstraints

DISPLAY_NAME_MAX_LENGTH = 128
# Unicode casefold maps one scalar to at most three scalars. Never truncate a unique key.
CASEFOLDED_NAME_MAX_LENGTH = 3 * DISPLAY_NAME_MAX_LENGTH

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH)]
