"""Reuse only reviewed state-compatible journeys; fault labs remain disposable."""

from contextlib import AsyncExitStack, asynccontextmanager

from .client import ACTIVE, LiveClient
from .round_two_lab import open_lab


class SharedLabs:
    def __init__(self):
        self.suite = None
        self.lab = None
        self.stack = AsyncExitStack()

    async def get(self, suite):
        if suite != self.suite:
            await self.close()
            self.lab = await self.stack.enter_async_context(open_lab(suite=suite, smoke=True))
            self.suite = suite
        return self.lab

    async def close(self):
        self.lab = self.suite = None
        await self.stack.aclose()

    @asynccontextmanager
    async def case(self, suite, request):
        lab = await self.get(suite)
        original = lab.client
        # Each case owns its Run/case ledger, even while processes and data remain.
        client = LiveClient(lab.config, original.http)
        lab.client = client
        clean = False
        try:
            yield lab
        finally:
            try:
                await client.cleanup()
                for run_id in client.runs:
                    await client.wait(
                        lambda run_id=run_id: client.run(run_id), lambda run: run["status"] not in ACTIVE, run_id
                    )
                clean = True
            finally:
                lab.client = original
                # Failed journeys can leave untracked queue entries or pending work.
                # Never pass those side effects to the next case in the group.
                report = getattr(request.node, "live_call_report", None)
                if not clean or report is None or report.failed:
                    await self.close()
