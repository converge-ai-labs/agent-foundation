"""Fault boundaries around real receipt commits in the opted-in test Worker."""

from functools import wraps

import anyio


def install(faults):
    from a13n_service.interactions.attempts import AttemptExecutionService

    ingest = AttemptExecutionService.ingest_usage

    @wraps(ingest)
    async def ingest_usage(self, authority, *, harness_run_id, records):
        facts = {"run_id": authority.run_id, "fence": authority.attempt_number, "records": len(records)}
        delayed = await faults.take("usage.delivery_delayed", **facts)
        if delayed is not None:
            # Model an already dispatched receipt arriving after its sender is
            # cancelled. Shield only this explicit transport fault, outside any
            # database session; production ingestion still validates ownership.
            with anyio.CancelScope(shield=True):
                await delayed.apply()
                await ingest(self, authority, harness_run_id=harness_run_id, records=records)
                await faults.reach("usage.after_ingest", **facts)
            return
        await faults.reach("usage.before_ingest", **facts)
        result = await ingest(self, authority, harness_run_id=harness_run_id, records=records)
        # Both barriers are outside the production method's committed transaction.
        await faults.reach("usage.after_ingest", **facts)
        replay = await faults.take("usage.redeliver", **facts)
        if replay is not None:
            await replay.apply()
            # Redeliver the actual immutable Harness records, with the original
            # authority, as a caller could after losing the commit acknowledgement.
            await ingest(self, authority, harness_run_id=harness_run_id, records=records)
            await faults.reach("usage.replayed", **facts)
        return result

    AttemptExecutionService.ingest_usage = ingest_usage
