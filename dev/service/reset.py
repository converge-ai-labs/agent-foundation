"""Rebuild every owned local store before provisioning a selected baseline."""

import logging
import shutil
import traceback
from functools import partial

from a13n_logging import JsonFormatter
from a13n_service.database import DatabaseMigrator

from .environment import Environment


def reset(environment: Environment, state: str) -> None:
    """Reset an environment while the CLI owns its exclusive lifecycle lock."""
    if state not in {"empty", "seeded"}:
        raise ValueError("State must be empty or seeded")
    if state == "seeded" and (
        environment.settings.service.role != "all" or environment.settings.iam.smtp_host is not None
    ):
        raise ValueError("Seeded reset requires role=all and no external SMTP server")
    environment.require_stopped()
    print(f"Resetting owned local Service state to {state}...", flush=True)
    marker = environment.incomplete
    marker.write_text(state + "\n")
    environment.compose("down", "--volumes", "--remove-orphans")
    if environment.state.exists():
        shutil.rmtree(environment.state)
    environment.state.mkdir(parents=True)
    environment.compose("up", "-d", "--wait")
    settings = environment.settings
    DatabaseMigrator(settings.database_config(), settings.migration_config()).upgrade()
    if state == "seeded":
        import anyio

        from .seed import seed

        log_path = environment.state / "seed.log"
        log_path.touch(mode=0o600)
        loggers = [logging.getLogger(name) for name in (None, "a13n_service", "a13n_harness", "pydantic_ai")]
        previous = [(logger, logger.handlers[:], logger.propagate) for logger in loggers]
        handler = logging.FileHandler(log_path)
        handler.setFormatter(JsonFormatter())
        for logger in loggers:
            logger.handlers = [handler]
            logger.propagate = False
        try:
            anyio.run(partial(seed, settings, model_port=environment.ports.model))
        except Exception as error:
            traceback.print_exception(error, file=handler.stream)
            raise RuntimeError(f"Seeding failed; diagnostic log: {log_path}. Run reset again to recover.") from None
        finally:
            for logger, handlers, propagate in previous:
                logger.handlers = handlers
                logger.propagate = propagate
            handler.close()
    marker.unlink()
    print(f"Local Service state is {state}. Application processes remain stopped.", flush=True)
