"""Run first-round HTTP journeys in an owned, disposable local installation."""

import asyncio
import signal
import sys

from .round_two_lab import REPOSITORY, open_lab, private_json


async def run(arguments):
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with open_lab(suite="core") as lab:
        await lab.client.preflight()
        files = sorted((REPOSITORY / "dev" / "live_tests").glob("test_0[1-8]_*.py"))
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
    raise SystemExit(asyncio.run(run(sys.argv[1:])))
