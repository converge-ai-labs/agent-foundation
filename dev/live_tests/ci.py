"""Run the reviewed, account-free live journeys used by CI and local reproduction."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
TEST_ROOT = REPOSITORY / "dev" / "live_tests"


@dataclass(frozen=True)
class Suite:
    selections: tuple[str, ...]
    gates: tuple[str, ...]
    keyword: str = ""


# Explicit files/node IDs keep new opt-in provider or stress tests out of CI until reviewed.
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
            "control/test_14_async_subagents.py",
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
            "control/test_45_control_acceptance.py",
            "control/test_46_control_waiting.py",
            "control/test_47_control_concurrency.py",
            "control/test_48_control_branches.py",
            "control/test_49_control_queue.py",
            "control/test_50_control_inbox.py",
        ),
        ("--live-round-two",),
    ),
    "fork-queue": Suite(
        (
            "control/test_51_control_child_results.py",
            "control/test_52_control_steer_races.py",
            "control/test_53_control_queue_races.py",
            "control/test_54_control_fork_commands.py",
            "control/test_54_control_queue_edges.py",
            "control/test_55_control_fork_recovery.py",
            "control/test_56_control_fork_queue.py",
        ),
        ("--live-round-two",),
    ),
    "run-faults": Suite(
        (
            "run_recovery/test_37_run_persistence_faults.py",
            "control/test_38_run_control_faults.py",
            "run_recovery/test_39_run_budgets_and_drain.py",
            "control/test_40_run_acceptance_and_queue_faults.py",
            "iam/test_41_run_authority_faults.py",
            "run_recovery/test_42_run_dependency_faults.py",
        ),
        ("--live-round-two",),
    ),
    "environment-native": Suite(
        (
            "environment/test_42_environment_files.py",
            "environment/test_43_environment_storage.py",
            "environment/test_44_environment_lifecycle.py",
            "environment/test_50_local_docker_lifecycle.py",
            "environment/test_52_remote_envd_failures.py",
            "environment/test_53_docker_boundaries.py",
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
            "environment/test_55_remote_envd_service_failures.py",
            "environment/test_56_environment_worker_sharing.py",
            "environment/test_57_environment_worker_lifecycle.py",
            "environment/test_58_environment_worker_policy.py",
            "environment/test_59_environment_worker_dependencies.py",
            "environment/test_60_environment_worker_authority.py",
        ),
        ("--live-management", "--live-environments"),
        "not e2b",
    ),
}


def parse_shard(value):
    try:
        index, count = map(int, value.split("/"))
        if not 1 <= index <= count:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a one-based shard such as 1/3") from error
    return index, count


def selections(suite, shard):
    index, count = shard
    return SUITES[suite].selections[index - 1 :: count]


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("suite", choices=SUITES)
    command.add_argument("--shard", type=parse_shard, default=(1, 1), help="Disjoint file/node selections, e.g. 1/3")
    command.add_argument("--collect-only", action="store_true", help="List cases without starting any infrastructure")
    command.add_argument("-k", "--keyword", default="", help="Further restrict this suite using a pytest expression")
    command.add_argument("-x", "--exitfirst", action="store_true")
    command.add_argument("--junitxml", type=Path)
    command.add_argument("--basetemp", type=Path)
    return command


def pytest_arguments(options):
    suite = SUITES[options.suite]
    arguments = [
        *suite.gates,
        "-n",
        "0",
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
    files = [str(TEST_ROOT / selection) for selection in selections(options.suite, options.shard)]
    if not files:
        command.error("This shard has no selections; reduce the shard count")
    arguments = pytest_arguments(options)
    environment = clean_environment()
    print(
        f"Live CI suite: {options.suite} shard {options.shard[0]}/{options.shard[1]}; "
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
