"""Service's in-process API for trusted installed Apps.

Import the domain facade directly. Session-taking writes participate in the
caller's short Service transaction; other writes open their own transaction.
"""

from a13n_service.app_sdk._access import is_app_enabled as is_app_enabled
from a13n_service.infra.db import short_session as short_session
from a13n_service.infra.db import transaction as transaction
from a13n_service.infra.errors import ServiceError as ServiceError
