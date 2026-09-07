"""Resolve exact Account objects and provider conversation policy."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.reception import InputBatchingPolicy
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.errors import NativeError

from .admission_domain import BatchConfiguration
from .admission_models import AgentThreadBindingRecord
from .provider import ExternalRef, InboundEvent, ProviderIrrelevantEventRouting, ProviderRequiresBindingRouting


@dataclass(frozen=True, slots=True)
class EligibleRouting:
    configuration: BatchConfiguration
    external_ref: ExternalRef
    binding: AgentThreadBindingRecord | None


@dataclass(frozen=True, slots=True)
class IrrelevantRouting:
    reason_code: str


async def resolve_routing(
    session: AsyncSession, *, adapter: IngressAdapter, account: AccountRecord, event: InboundEvent
) -> EligibleRouting | IrrelevantRouting:
    if account.status != "active" or not account.receive_enabled or account.deleted_at is not None:
        return IrrelevantRouting("receiving_disabled")
    try:
        kind, identifier = adapter.event_target(event)
    except ValueError as error:
        raise NativeError(
            "invalid_provider_event", "Provider event target is invalid.", category=ErrorCategory.invalid_request
        ) from error
    target = await session.scalar(
        select(AccountTargetRecord).where(
            AccountTargetRecord.account_id == account.id,
            AccountTargetRecord.target_kind == kind,
            AccountTargetRecord.external_target_id == identifier,
        )
    )
    if target is not None and not target.receive_enabled:
        return IrrelevantRouting("target_receiving_disabled")
    try:
        default = adapter.reception_defaults(
            event, account.provider_config_json, config_version=account.provider_config_version
        )
        policy = (
            target.provider_policy_json
            if target is not None and target.provider_policy_json is not None
            else account.provider_policy_json
        )
        if policy is None:
            policy = default.provider_policy
        classification = adapter.classify(
            event, policy, account.provider_config_json, config_version=account.provider_config_version
        )
    except ValueError as error:
        raise NativeError(
            "invalid_provider_event", "Provider event cannot be classified.", category=ErrorCategory.invalid_request
        ) from error
    if isinstance(classification, ProviderIrrelevantEventRouting):
        return IrrelevantRouting(classification.reason_code)
    ref = event.refs.get(classification.external_ref_key)
    if ref is None:
        raise NativeError(
            "correlation_ref_missing",
            "Provider event has no conversation reference.",
            category=ErrorCategory.invalid_request,
        )
    binding = await session.scalar(
        select(AgentThreadBindingRecord)
        .where(
            AgentThreadBindingRecord.account_id == account.id,
            AgentThreadBindingRecord.external_ref_kind == ref.kind,
            AgentThreadBindingRecord.external_ref_id == ref.id,
        )
        .with_for_update()
    )
    if isinstance(classification, ProviderRequiresBindingRouting) and (binding is None or binding.thread_id is None):
        return IrrelevantRouting("binding_required")
    batching = (
        target.input_batching_json
        if target is not None and target.input_batching_json is not None
        else account.input_batching_json
    )
    return EligibleRouting(
        configuration=BatchConfiguration(
            account_id=account.id,
            account_version=account.version,
            target_id=target.id if target is not None else None,
            target_version=target.version if target is not None else None,
            target_kind=kind,
            external_target_id=identifier,
            provider_key=account.provider_key,
            provider_context_version=event.normalization_version,
            provider_context=classification.provider_context,
            provider_policy=policy,
            native_actions=classification.native_actions,
            input_batching=InputBatchingPolicy.model_validate(batching)
            if batching is not None
            else default.input_batching,
        ),
        external_ref=ref,
        binding=binding,
    )
