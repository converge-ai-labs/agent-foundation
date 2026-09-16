"""Configuration use cases exposed by the Control role."""

from dataclasses import dataclass

from .application import ConfigurationApplication
from .conversations import ConfigurationConversations
from .drafts import ConfigurationDrafts
from .inputs import ConfigurationInputs
from .readiness import ConfigurationReadiness
from .review import ConfigurationReviews


@dataclass(frozen=True, slots=True)
class ConfigurationService:
    conversations: ConfigurationConversations
    drafts: ConfigurationDrafts
    application: ConfigurationApplication
    inputs: ConfigurationInputs
    readiness: ConfigurationReadiness
    reviews: ConfigurationReviews
