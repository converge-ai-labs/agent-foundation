"""Normalize captured auxiliary Model recipes identically for Runs and Host operations."""

from pydantic import JsonValue

from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.model_runtime import model_recipe_id

from .models import ResolvedCapabilityRecipe, ResolvedModelRecipe


def capability_configuration(
    item: ResolvedCapabilityRecipe, models: dict[str, ResolvedModelRecipe]
) -> dict[str, JsonValue]:
    configuration = dict(item.configuration)
    if item.model is None:
        return configuration
    auxiliary_id = model_recipe_id(item.model)
    previous = models.setdefault(auxiliary_id, item.model)
    if previous != item.model:
        raise CompositionError("Model recipe identity collision.", code="model_recipe_collision")
    review = configuration.get("review") if item.capability == "ToolPermissionsCapability" else configuration
    if not isinstance(review, dict):
        raise CompositionError("Tool review must be an object.", code="capability_configuration_invalid")
    review = dict(review)
    review["model"] = auxiliary_id
    overrides = review.get("model_settings", {})
    if not isinstance(overrides, dict):
        raise CompositionError("Auxiliary Model settings must be an object.", code="capability_model_settings_invalid")
    review["model_settings"] = {**item.model.settings, **overrides}
    if item.capability == "ToolPermissionsCapability":
        configuration["review"] = review
        return configuration
    return review
