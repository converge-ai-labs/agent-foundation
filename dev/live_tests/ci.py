"""Run reviewed, account-free live journeys manually."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .infrastructure.dependencies import add_infrastructure_arguments, infrastructure_environment

REPOSITORY = Path(__file__).resolve().parents[2]
TEST_ROOT = REPOSITORY / "dev" / "live_tests"


@dataclass(frozen=True)
class Suite:
    selections: tuple[str, ...]
    gates: tuple[str, ...]
    keyword: str = ""


def cases(module, *names):
    return tuple(f"{module}::{name}" for name in names)


# Explicit files/node IDs keep new opt-in provider or stress tests out of the selected suites until reviewed.
SUITES = {
    "core": Suite(
        (
            "harness_integration/test_01_basic_run.py",
            "harness_integration/test_02_continuation.py",
            "harness_integration/test_03_tools_environment.py",
            "protocol/test_04_protocol_streams.py",
            "protocol/test_04_stream_reconnect.py",
            "control/test_05_idempotency.py",
            "control/test_06_steer.py",
            "control/test_07_interrupt.py",
            "control/test_08_approval.py",
        ),
        ("--live",),
    ),
    "functional": Suite(
        (
            "control/test_13_queue_retry_fork.py",
            *cases(
                "control/test_14_async_subagents.py",
                "test_async_children_result_delivery_by_parent_status[running]",
                "test_async_children_result_delivery_by_parent_status[completed]",
                "test_async_children_result_delivery_by_parent_status[approval]",
                "test_async_children_result_delivery_by_parent_status[cancelled]",
            ),
            "iam/test_16_workspace_isolation.py",
            "harness_integration/test_17_agent_revisions.py",
            "harness_integration/test_18_plugin_execution.py",
            "environment/test_20_environment_templates.py",
            "environment/test_21_environment_lifecycle.py::test_successor_inherits_environment_by_operation",
            "environment/test_21_environment_lifecycle.py::test_continuation_updates_default_but_historical_fork_keeps_source",
            "environment/test_22_environment_access.py",
            "harness_integration/test_24_skill_execution.py",
            "harness_integration/test_25_asset_execution.py",
            "harness_integration/test_26_output_and_client_tools.py",
        ),
        ("--live-round-two", "--live-management"),
    ),
    "control": Suite(
        (
            *cases(
                "control/test_45_control_acceptance.py",
                "test_control_serializes_with_first_worker_claim",
                "test_stale_interrupt_versions_fail_without_retry_or_side_effect",
                "test_successor_response_loss_replays_exact_acceptance[state_published-continue]",
                "test_successor_response_loss_replays_exact_acceptance[committed-continue]",
                "test_successor_response_loss_replays_exact_acceptance[committed-fork]",
                "test_successor_response_loss_replays_exact_acceptance[committed-retry]",
                "test_successor_response_loss_replays_exact_acceptance[committed-feedback]",
                "test_successor_response_loss_replays_exact_acceptance[committed-waiting_continue]",
                "test_control_rejects_ineligible_current_run_states",
            ),
            *cases(
                "control/test_46_control_waiting.py",
                "test_steer_on_either_side_of_waiting_seal_binds_only_to_direct_successor",
                "test_waiting_continue_applies_defaults_and_new_input_before_inbox_without_consuming_queue",
                "test_waiting_continue_rejects_stale_and_execution_override_requests_atomically",
                "test_repeated_waiting_rolls_entire_fifo_through_replacement_attempt[after_waiting_checkpoint]",
                "test_applied_waiting_continue_checkpoint_recovers_without_reapplying_input_or_effect",
                "test_feedback_normalizes_entire_mixed_batch_and_preserves_null[client_null]",
                "test_feedback_normalizes_entire_mixed_batch_and_preserves_null[reversed_full]",
                "test_batch_feedback_invalid_requests_leave_all_pending_calls_unchanged",
            ),
            "control/test_47_control_concurrency.py",
            *cases(
                "control/test_48_control_branches.py",
                "test_cancelled_retry_preserves_original_parent_and_drops_superseded_steer",
                "test_terminal_submission_respects_selected_head_and_existing_queue[False-none-failed]",
                "test_terminal_submission_respects_selected_head_and_existing_queue[False-completed-cancelled]",
                "test_terminal_submission_respects_selected_head_and_existing_queue[False-waiting-failed]",
                "test_terminal_submission_respects_selected_head_and_existing_queue[True-none-cancelled]",
                "test_terminal_submission_respects_selected_head_and_existing_queue[True-completed-failed]",
                "test_terminal_submission_respects_selected_head_and_existing_queue[True-waiting-cancelled]",
            ),
            *cases(
                "control/test_49_control_queue.py",
                "test_late_steer_invalidates_prepared_queue_handoff_until_source_drains_inbox",
                "test_queue_mutation_and_completion_handoff_use_one_committed_intent[mutation-patch]",
                "test_queue_mutation_and_completion_handoff_use_one_committed_intent[mutation-delete]",
                "test_queue_mutation_and_completion_handoff_use_one_committed_intent[consumption-patch]",
                "test_queue_mutation_and_completion_handoff_use_one_committed_intent[consumption-reorder]",
                "test_explicit_consumers_and_background_recovery_accept_one_queued_run",
                "test_concurrent_enqueue_allocates_fifo_positions_without_advancing_thread",
                "test_queue_stale_versions_and_invalid_reorders_leave_intent_unchanged",
            ),
            *cases(
                "control/test_50_control_inbox.py",
                "test_distinct_steer_ids_preserve_identical_content_in_fifo",
                "test_concurrent_steer_overflow_is_atomic_and_capacity_is_reusable",
                "test_steer_byte_budget_accepts_exact_limit_and_rejects_one_byte_over[0-control0]",
                "test_steer_byte_budget_accepts_exact_limit_and_rejects_one_byte_over[1-control0]",
                "test_deleted_binary_steer_source_fails_before_model_and_finalizes_pending_entry",
                "test_active_command_lost_reply_replays_committed_receipt_after_control_restart",
            ),
        ),
        ("--live-round-two",),
    ),
    "fork-queue": Suite(
        (
            "control/test_51_control_child_results.py",
            *cases(
                "control/test_52_control_steer_races.py",
                "test_waiting_steer_and_successor_commit_order_controls_binding[steer-feedback]",
                "test_waiting_steer_and_successor_commit_order_controls_binding[successor-feedback]",
                "test_steer_admission_and_terminal_seal_preserve_the_winning_order[steer-cancelled]",
                "test_steer_admission_and_terminal_seal_preserve_the_winning_order[terminal-cancelled]",
                "test_interrupt_after_committed_consumption_preserves_receipt_and_status",
            ),
            *cases(
                "control/test_53_control_queue_races.py",
                "test_background_and_explicit_consume_compete_after_both_publish_initial_state",
                "test_enqueue_and_run_sealing_revalidate_the_same_thread_version[enqueue-completed]",
                "test_enqueue_and_run_sealing_revalidate_the_same_thread_version[enqueue-waiting]",
                "test_enqueue_and_run_sealing_revalidate_the_same_thread_version[terminal-completed]",
                "test_enqueue_and_run_sealing_revalidate_the_same_thread_version[terminal-failed]",
                "test_completed_thread_submission_cannot_bypass_queue_during_recovery",
                "test_interrupt_and_prepared_queue_handoff_cannot_terminalize_each_others_run",
                "test_background_queue_consumption_and_explicit_branch_commit_one_advancement[branch-retry]",
                "test_background_queue_consumption_and_explicit_branch_commit_one_advancement[recovery-retry]",
            ),
            *cases(
                "control/test_54_control_fork_commands.py",
                "test_fork_and_control_complete_before_the_other_command_is_released[fork-continue]",
                "test_fork_and_control_complete_before_the_other_command_is_released[fork-interrupt]",
                "test_fork_and_control_complete_before_the_other_command_is_released[fork-feedback]",
                "test_fork_and_control_complete_before_the_other_command_is_released[source-continue]",
                "test_fork_and_control_complete_before_the_other_command_is_released[source-interrupt]",
                "test_fork_and_control_complete_before_the_other_command_is_released[source-feedback]",
                "test_distinct_forks_sharing_an_environment_do_not_wait_for_each_other",
            ),
            *cases(
                "control/test_54_control_queue_edges.py",
                "test_running_recovery_scans_leave_waiting_head_queue_untouched_until_explicit_progress[waiting]",
                "test_recoverable_queue_head_blocks_its_tail_but_not_other_threads",
                "test_queue_last_slot_has_one_winner_and_released_capacity_accepts_rejected_intent",
                "test_consumed_queue_entry_never_requeues_when_its_run_terminates_and_control_restarts[none-failed]",
                "test_consumed_queue_entry_never_requeues_when_its_run_terminates_and_control_restarts[completed-cancelled]",
            ),
            *cases(
                "control/test_55_control_fork_recovery.py",
                "test_fork_and_execution_progress_before_the_peer_boundary_opens[fork-model]",
                "test_fork_and_execution_progress_before_the_peer_boundary_opens[fork-tool]",
                "test_fork_and_execution_progress_before_the_peer_boundary_opens[fork-checkpoint]",
                "test_fork_and_execution_progress_before_the_peer_boundary_opens[worker-model]",
                "test_fork_and_execution_progress_before_the_peer_boundary_opens[worker-tool]",
                "test_fork_and_execution_progress_before_the_peer_boundary_opens[worker-checkpoint]",
                "test_fork_and_replacement_attempt_recover_before_the_peer_is_released[fork-claim_after]",
                "test_fork_and_replacement_attempt_recover_before_the_peer_is_released[recovery-claim_after]",
            ),
            *cases(
                "control/test_56_control_fork_queue.py",
                "test_fork_and_queue_consumption_progress_before_the_peer_is_released[fork-explicit]",
                "test_fork_and_queue_consumption_progress_before_the_peer_is_released[fork-shared_handoff]",
                "test_fork_and_queue_consumption_progress_before_the_peer_is_released[queue-explicit]",
                "test_fork_and_queue_consumption_progress_before_the_peer_is_released[queue-shared_handoff]",
            ),
        ),
        ("--live-round-two",),
    ),
    "run-faults": Suite(
        (
            *cases(
                "run_recovery/test_37_run_persistence_faults.py",
                "test_worker_crash_before_checkpoint_obeys_tool_owned_idempotency",
                "test_lost_object_write_response_reconciles_without_repeating_execution",
                "test_initial_write_response_loss_retries_acceptance_with_same_idempotency_key",
                "test_terminal_candidate_survives_worker_loss_before_relational_seal",
                "test_invalid_recovery_state_fails_before_more_model_or_tool_work[missing]",
                "test_invalid_recovery_state_fails_before_more_model_or_tool_work[harness_schema]",
                "test_invalid_recovery_state_fails_before_more_model_or_tool_work[digest]",
                "test_plugin_removed_between_attempts_fails_recovery_without_default_substitution",
                "test_incompatible_installed_plugin_state_cannot_resume_execution",
            ),
            "control/test_38_run_control_faults.py",
            "run_recovery/test_39_run_budgets_and_drain.py",
            "control/test_40_run_acceptance_and_queue_faults.py",
            "iam/test_41_run_authority_faults.py",
            *cases(
                "run_recovery/test_42_run_dependency_faults.py",
                "test_explicit_model_rejections_retry_boundedly[run_faults0-2-429]",
                "test_explicit_model_rejections_retry_boundedly[run_faults0-15-503]",
                "test_model_stream_failure_recovers_within_budget_without_false_completion[run_faults0-1-truncated]",
                "test_model_stream_failure_recovers_within_budget_without_false_completion[run_faults0-1-malformed]",
                "test_model_stream_failure_recovers_within_budget_without_false_completion[run_faults0-1-timeout]",
                "test_model_stream_failure_recovers_within_budget_without_false_completion[run_faults0-5-truncated]",
                "test_real_plugin_failure_does_not_create_an_effect_or_success_result",
                "test_dependency_flaps_recover_in_original_worker_process",
                "test_redis_outage_preserves_control_intent_without_worker_restart",
                "test_sustained_object_outage_exhausts_budget_then_same_worker_recovers[tcp-run_faults0]",
                "test_lost_database_renewal_stops_tool_and_same_worker_recovers",
                "test_mcp_error_or_unknown_effect_is_reported_without_automatic_replay",
            ),
        ),
        ("--live-round-two",),
    ),
    "environment-native": Suite(
        (
            *cases(
                "environment/test_42_environment_files.py",
                "test_file_traversal_rejects_source_and_destination_before_mutation",
                "test_file_symlink_escape_and_provider_link_replacement_semantics",
                "test_file_read_only_rejects_all_mutations_before_consuming_upload",
                "test_missing_file_errors_preserve_directory_entries[direct-local-read]",
                "test_missing_file_errors_preserve_directory_entries[direct-local-remove]",
                "test_missing_file_errors_preserve_directory_entries[local-envd-stream]",
                "test_missing_file_errors_preserve_directory_entries[local-envd-move]",
                "test_missing_file_errors_preserve_directory_entries[http-envd-text]",
                "test_missing_file_errors_preserve_directory_entries[http-envd-copy]",
                "test_missing_file_errors_preserve_directory_entries[websocket-envd-stat]",
                "test_missing_file_errors_preserve_directory_entries[websocket-envd-replace]",
                "test_missing_file_errors_preserve_directory_entries[docker-list]",
                "test_missing_file_errors_preserve_directory_entries[docker-patch]",
                "test_append_requires_existing_file_for_local_and_envd",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[direct-local-read]",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[direct-local-copy-directory]",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[local-envd-stream]",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[local-envd-move-over-directory]",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[http-envd-text]",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[websocket-envd-list-file]",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[docker-replace-directory]",
                "test_wrong_file_types_preserve_source_and_nonempty_destination[docker-mkdir-below-file]",
                "test_writable_provider_exposes_mkdir_copy_and_patch",
                "test_os_permission_failures_do_not_disclose_or_modify_files",
                "test_aborted_stream_preserves_destination_and_cleans_staging",
            ),
            "environment/test_43_environment_storage.py",
            *cases(
                "environment/test_44_environment_lifecycle.py",
                "test_close_fences_file_facets_and_fresh_scope_preserves_workspace",
                "test_close_serializes_with_real_preparation[direct-local-complete]",
                "test_close_serializes_with_real_preparation[local-envd-cancel]",
                "test_close_serializes_with_real_preparation[http-envd-complete]",
                "test_close_serializes_with_real_preparation[websocket-envd-cancel]",
                "test_close_serializes_with_real_preparation[docker-cancel]",
                "test_docker_stop_resume_and_metadata_recovery_preserve_only_target_identity",
                "test_docker_external_removal_rebuilds_only_managed_targets",
                "test_docker_lost_create_response_recovers_one_owned_container",
                "test_docker_lost_mutation_response_is_reconciled_without_repeating_effect",
            ),
            "environment/test_50_local_docker_lifecycle.py",
            "environment/test_52_remote_envd_failures.py",
            *cases(
                "environment/test_53_docker_boundaries.py",
                "test_bootstrap_failure_never_retargets_or_mutates_container[prepare-missing-directory]",
                "test_bootstrap_failure_never_retargets_or_mutates_container[reconcile-missing-credential]",
                "test_bootstrap_failure_never_retargets_or_mutates_container[stop-corrupt-manifest]",
                "test_bootstrap_failure_never_retargets_or_mutates_container[destroy-corrupt-config]",
                "test_engine_connection_loss_is_unknown_not_target_absence",
                "test_destroy_preserves_external_named_volume_and_its_contents",
            ),
            "environment/test_54_docker_storage.py",
        ),
        ("--live-environments",),
    ),
    "environment-service": Suite(
        (
            "environment/test_21_environment_lifecycle.py::test_stopped_and_deleted_managed_environment_recovers",
            "environment/test_21_environment_lifecycle.py::test_process_handle_cannot_cross_rebuilt_environment_generation",
            "environment/test_28_environment_backends.py",
            "environment/test_51_docker_service_lifecycle.py",
            *cases(
                "environment/test_55_remote_envd_service_failures.py",
                "test_service_reports_unknown_effect_without_replay_and_accepts_fresh_use[http-envd-connection-loss]",
                "test_service_reports_unknown_effect_without_replay_and_accepts_fresh_use[websocket-envd-daemon-restart]",
            ),
            *cases(
                "environment/test_56_environment_worker_sharing.py",
                "test_first_prepare_is_serialized_between_processes[direct-local-on_run]",
                "test_first_prepare_is_serialized_between_processes[direct-local-on_use]",
                "test_repeated_worker_handoffs_release_resources_and_preserve_shared_writes[direct-local]",
                "test_concurrent_workers_preserve_owner_and_release_only_their_scope[direct-local]",
                "test_first_prepare_is_serialized_between_processes[docker-on_run]",
                "test_first_prepare_is_serialized_between_processes[docker-on_use]",
                "test_one_user_lost_does_not_close_other_worker_use[docker-cancel]",
                "test_one_user_lost_does_not_close_other_worker_use[docker-crash]",
                "test_repeated_worker_handoffs_release_resources_and_preserve_shared_writes[docker]",
                "test_concurrent_workers_preserve_owner_and_release_only_their_scope[docker]",
                "test_one_user_lost_does_not_close_other_worker_use[http-envd-crash]",
                "test_one_user_lost_does_not_close_other_worker_use[websocket-envd-cancel]",
            ),
            "environment/test_57_environment_worker_lifecycle.py",
            "environment/test_58_environment_worker_policy.py",
            "environment/test_59_environment_worker_dependencies.py",
            "environment/test_60_environment_worker_authority.py",
        ),
        ("--live-management", "--live-environments"),
        "not e2b",
    ),
}


# These state-compatible groups are the only journeys allowed to share a lab.
SMOKE_GROUPS = {
    "core": (
        *cases("harness_integration/test_01_basic_run.py", "test_basic_run"),
        *cases("harness_integration/test_02_continuation.py", "test_continuation_preserves_context"),
        *cases("harness_integration/test_03_tools_environment.py", "test_environment_tool_writes_and_reads_file"),
        *cases(
            "protocol/test_04_protocol_streams.py",
            "test_native_event_contracts",
            "test_hosted_and_native_event_contracts",
            "test_hosted_disconnect_reconnect_and_cancel",
            "test_hosted_waiting_and_feedback_contract",
            "test_hosted_invalid_input_and_conflicting_reuse_do_not_accept_runs",
        ),
        *cases("protocol/test_04_stream_reconnect.py", "test_stream_disconnect_does_not_cancel_run"),
        *cases("control/test_05_idempotency.py", "test_start_is_idempotent"),
        *cases("control/test_07_interrupt.py", "test_interrupt_active_execution"),
        *cases("control/test_08_approval.py", "test_approval_feedback"),
    ),
    "round-two": (
        *cases(
            "control/test_13_queue_retry_fork.py",
            "test_busy_thread_consumes_queued_submissions_in_order",
            "test_retry_creates_new_run_with_original_intent",
            "test_fork_retains_history_in_a_new_thread",
        ),
        *cases("iam/test_16_workspace_isolation.py", "test_other_workspace_cannot_read_stream_or_control_run"),
    ),
    "management": (
        *cases(
            "harness_integration/test_18_plugin_execution.py",
            "test_installed_plugin_configuration_controls_real_effect",
            "test_worker_rejects_invalid_plugin_selection",
        ),
        *cases(
            "harness_integration/test_26_output_and_client_tools.py",
            "test_structured_output_enforces_schema_and_retry_budget",
            "test_client_tool_feedback_rejects_invalid_duplicate_and_stale_results",
        ),
    ),
}
SUITES["smoke"] = Suite(
    tuple(selection for group in SMOKE_GROUPS.values() for selection in group),
    ("--live", "--live-round-two", "--live-management", "--live-shared-labs"),
)


def parse_shard(value):
    try:
        index, count = map(int, value.split("/"))
        if not 1 <= index <= count:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a one-based shard such as 1/3") from error
    return index, count


def selections(suite, shard, *, smoke_group=None):
    index, count = shard
    modules = {}
    selected = SMOKE_GROUPS[smoke_group] if smoke_group is not None else SUITES[suite].selections
    for selection in selected:
        modules.setdefault(selection.split("::", 1)[0], []).append(selection)
    # Keep module-scoped backends in one job even when selecting individual cases.
    return tuple(selection for group in list(modules.values())[index - 1 :: count] for selection in group)


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("suite", choices=SUITES)
    command.add_argument("--smoke-group", choices=SMOKE_GROUPS, help="Run one shared lab group from the smoke suite")
    command.add_argument(
        "--workers",
        type=int,
        choices=range(1, 5),
        default=1,
        help="Pytest workers (2-4 require smoke --smoke-group=core; each owns a separate lab)",
    )
    command.add_argument("--shard", type=parse_shard, default=(1, 1), help="Disjoint file/node selections, e.g. 1/3")
    command.add_argument("--collect-only", action="store_true", help="List cases without starting any infrastructure")
    command.add_argument("-k", "--keyword", default="", help="Further restrict this suite using a pytest expression")
    command.add_argument("-x", "--exitfirst", action="store_true")
    command.add_argument("--junitxml", type=Path)
    command.add_argument("--basetemp", type=Path)
    add_infrastructure_arguments(command)
    return command


def pytest_arguments(options):
    suite = SUITES[options.suite]
    arguments = [
        *suite.gates,
        "-n",
        str(options.workers) if options.workers > 1 and not options.collect_only else "0",
        "-q" if options.collect_only else "-v",
        "-ra",
        "--tb=short",
        "--durations=30",
        "-o",
        "log_cli=true",
        "-o",
        "log_cli_level=INFO",
        "--log-disable=httpx2",
    ]
    if options.workers > 1 and not options.collect_only:
        arguments.extend(("--dist=load", "--max-worker-restart=0"))
    keywords = [value for value in (suite.keyword, options.keyword) if value]
    if keywords:
        arguments.extend(("-k", " and ".join(f"({value})" for value in keywords)))
    if options.collect_only:
        arguments.append("--collect-only")
    if options.exitfirst:
        arguments.append("-x")
    for name in ("junitxml", "basetemp"):
        if value := getattr(options, name):
            arguments.append(f"--{name}={value.resolve()}")
    return arguments


def clean_environment():
    # Only fixture-owned services/accounts participate. Native binary and image
    # overrides remain available to reproduce the exact CI build locally.
    image_overrides = {"LIVE_TEST_SANDBOX_IMAGE", "LIVE_TEST_FILE_RESOURCE_IMAGE", "LIVE_TEST_DOCKER_RESOURCE_IMAGE"}
    return {
        key: value
        for key, value in os.environ.items()
        if (not key.startswith(("A13N_SERVICE_", "AWS_", "LIVE_TEST_", "OTEL_")) or key in image_overrides)
        and key not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    }


def main(arguments=None):
    command = parser()
    options = command.parse_args(arguments)
    if options.smoke_group is not None and options.suite != "smoke":
        command.error("--smoke-group requires suite=smoke")
    if options.workers > 1 and (options.suite != "smoke" or options.smoke_group != "core"):
        command.error("--workers > 1 requires suite=smoke --smoke-group=core")
    try:
        infrastructure = infrastructure_environment(options)
    except ValueError as error:
        command.error(str(error))
    files = [
        str(TEST_ROOT / selection)
        for selection in selections(options.suite, options.shard, smoke_group=options.smoke_group)
    ]
    if not files:
        command.error("This shard has no selections; reduce the shard count")
    arguments = pytest_arguments(options)
    environment = clean_environment()
    environment.update(infrastructure)
    if options.basetemp and not options.collect_only:
        # pytest creates basetemp itself, but requires its parent to exist.
        options.basetemp.resolve().parent.mkdir(parents=True, exist_ok=True)
    print(
        f"Live suite: {options.suite} group={options.smoke_group or 'all'} "
        f"shard {options.shard[0]}/{options.shard[1]} workers={options.workers}; "
        f"{len(files)} explicit selections; external accounts disabled",
        flush=True,
    )
    if options.suite == "core" and not options.collect_only:
        os.environ.clear()
        os.environ.update(environment)
        from .isolated import run

        return asyncio.run(run(arguments, files=files))
    return subprocess.call([sys.executable, "-m", "pytest", *files, *arguments], cwd=REPOSITORY, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
