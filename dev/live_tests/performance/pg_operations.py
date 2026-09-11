"""Single PostgreSQL statement and COMMIT calls on pre-acquired connections."""

from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4

from sqlalchemy import text

from .operations import Operation


async def measure_postgres(benchmark, engine, config):
    # A benchmark-owned table makes statement size/contention explicit without
    # replacing any of the production tables used by the Service scenarios.
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE performance_calls (id text PRIMARY KEY, payload bytea NOT NULL)"))
    for size in config.payload_bytes:
        for concurrency in config.concurrency:
            for name in ("select", "insert", "update", "commit"):
                if f"pg.{name}" not in config.scenarios:
                    continue

                @asynccontextmanager
                async def prepare(count, operation=name, payload_size=size):
                    async with AsyncExitStack() as stack:
                        connections = [await stack.enter_async_context(engine.connect()) for _ in range(count)]
                        identifiers = [uuid4().hex for _ in range(count)]
                        body = b"p" * payload_size
                        updated_body = b"u" * payload_size
                        calls = []
                        for connection, identifier in zip(connections, identifiers, strict=True):
                            if operation != "insert":
                                await connection.execute(
                                    text("INSERT INTO performance_calls VALUES (:id, :payload)"),
                                    {"id": identifier, "payload": body},
                                )
                            # Commit setup; measured COMMIT owns exactly one pending UPDATE.
                            await connection.commit()
                            if operation == "commit":
                                await connection.execute(
                                    text("UPDATE performance_calls SET payload=:payload WHERE id=:id"),
                                    {"id": identifier, "payload": updated_body},
                                )
                            else:
                                await connection.begin()
                                # Materialize the driver's BEGIN outside the single-statement timer.
                                await connection.execute(text("SELECT 1"))

                            async def call(conn=connection, key=identifier):
                                if operation == "commit":
                                    return await conn.commit()
                                statements = {
                                    "select": "SELECT payload FROM performance_calls WHERE id=:id",
                                    "insert": "INSERT INTO performance_calls VALUES (:id, :payload)",
                                    "update": "UPDATE performance_calls SET payload=:payload WHERE id=:id",
                                }
                                return await conn.execute(
                                    text(statements[operation]),
                                    {"id": key, "payload": updated_body if operation == "update" else body},
                                )

                            async def verify(result, conn=connection, key=identifier):
                                if operation == "select":
                                    assert result.scalar_one() == body
                                elif operation != "commit":
                                    assert result.rowcount == 1
                                await conn.commit()
                                stored = await conn.scalar(
                                    text("SELECT payload FROM performance_calls WHERE id=:id"), {"id": key}
                                )
                                assert stored == (updated_body if operation in {"update", "commit"} else body)

                            calls.append(Operation(call, verify))
                        try:
                            yield calls
                        finally:
                            for connection, identifier in zip(connections, identifiers, strict=True):
                                await connection.rollback()
                                await connection.execute(
                                    text("DELETE FROM performance_calls WHERE id=:id"), {"id": identifier}
                                )
                                await connection.commit()

                await benchmark.measure(
                    f"pg.{name}",
                    concurrency,
                    prepare,
                    configuration={"payload_bytes": size, "keys": "independent", "pool_size": config.pg_pool_size},
                    boundary="One AsyncConnection.execute call; connection acquisition and transaction commit excluded"
                    if name != "commit"
                    else "One COMMIT call for a transaction with one prepared UPDATE; connection acquisition and UPDATE excluded",
                )
