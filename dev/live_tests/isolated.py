"""Run first-round HTTP journeys in an owned, disposable local installation."""

import argparse
import asyncio
import os
import signal

from .infrastructure.dependencies import add_infrastructure_arguments, infrastructure_environment
from .infrastructure.round_two_lab import REPOSITORY, open_lab, private_json


async def run(arguments, *, files=None):
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with open_lab(suite="core") as lab:
        await lab.client.preflight()
        if files is None:
            files = sorted((REPOSITORY / "dev" / "live_tests").rglob("test_0[1-8]_*.py"), key=lambda path: path.name)
        log_path = lab.root / f"process-{len(lab.processes)}.log"
        print(f"Control and Worker ready. Running tests; output: {log_path}", flush=True)
        process = await lab.spawn(
            "pytest", *map(str, files), "--live", "-v", "--tb=short", "--durations=10", *arguments
        )
        try:
            async with asyncio.timeout(600):
                result = await process.wait()
        finally:
            if process.returncode is None:
                await lab.stop(process)
            print(log_path.read_text(), flush=True)
        resources = await lab.client.collection(f"/api/v1/workspaces/{lab.config['workspace_id']}/runs")
        private_json(lab.root / "results.json", {"arguments": arguments, "exit_code": result, "runs": resources})
        print(f"Results retained in {lab.root}; removing owned services and containers.", flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_infrastructure_arguments(parser)
    options, arguments = parser.parse_known_args()
    try:
        os.environ.update(infrastructure_environment(options))
    except ValueError as error:
        parser.error(str(error))
    raise SystemExit(asyncio.run(run(arguments)))
