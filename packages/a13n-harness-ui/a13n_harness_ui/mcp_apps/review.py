"""Built-in App review through normal Host Model resolution, never a Harness Run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from a13n_harness.capabilities.tool_review import AgentToolReviewer, ToolReviewError, ToolReviewRequest
from a13n_harness.metering import HostModelUsage, ModelCallUsage
from a13n_harness.model_calls import ModelCall
from a13n_harness.pricing import CatalogModelCostCapability
from a13n_harness.tools.policy import InvocationDecisionKind

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore
from a13n_harness_ui.model_runtime import HarnessUiModelResolver, SubscriptionSource, model_recipe_id

if TYPE_CHECKING:
    from .operations import Admission, AppOperation, AppOperations


class AppReviewer:
    def __init__(
        self,
        operations: AppOperations,
        *,
        api_keys: ApiKeyStore,
        subscription_sources: Mapping[str, SubscriptionSource],
    ) -> None:
        self.operations = operations
        self.api_keys = api_keys
        self.subscription_sources = dict(subscription_sources)

    async def __call__(
        self, admission: Admission, operation: AppOperation
    ) -> tuple[InvocationDecisionKind, str | None]:
        config = admission.policy.config
        recipe = next(
            (
                item.model
                for item in admission.owner.node.capabilities
                if item.capability == "ToolPermissionsCapability"
            ),
            None,
        )
        if config is None or recipe is None or config.model != model_recipe_id(recipe):
            raise HarnessUiError("The configured App reviewer is unavailable.", code="mcp_app_review_unsupported")
        model = await HarnessUiModelResolver(
            {config.model: recipe}, api_keys=self.api_keys, subscription_sources=self.subscription_sources
        ).resolve(config.model, thread_id=admission.owner.thread_id)
        operations = self.operations

        class Check:
            async def check(self, call: ModelCall) -> None:
                if call.model_id != config.model:
                    raise HarnessUiError("The reviewer Model changed.", code="mcp_app_decision_stale")
                await operations.authorize_review(admission, operation)

        async def report(record: ModelCallUsage) -> None:
            operations.retain_review_usage(admission.owner.thread_id, operation, record)

        usage = HostModelUsage(
            check=Check(),
            cost=CatalogModelCostCapability(),
            source="tool.review",
            tool_id=operation.tool_id,
            tool_call_id=operation.operation_id,
            report=report,
        )
        try:
            result = await AgentToolReviewer(model, config).review_for_host(
                ToolReviewRequest(
                    tool_id=operation.tool_id,
                    tool_call_id=operation.operation_id,
                    tool_name=operation.name,
                    description=admission.tool.description,
                    parameters_schema=admission.tool.input_schema,
                    arguments=operation.arguments,
                ),
                usage=usage,
            )
        except ToolReviewError as exc:
            return (
                "deny" if exc.code == "tool_review_timeout" else config.on_error,
                "The App review timed out." if exc.code == "tool_review_timeout" else "The App review failed.",
            )
        return admission.policy.decision_for(operation.tool_id, result.assessment), result.assessment.reason
