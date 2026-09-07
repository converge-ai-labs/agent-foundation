"""Run-local Monty resource ownership for CodeAct."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from pydantic_monty import AsyncMonty, AsyncMontySession, CollectString, MontyComplete, ResourceLimits

from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.executor import AdmitFn, DispatchFn, MontyExecutor, ReleaseFn


@dataclass
class CodeActExecution:
    """Raw result from one sandbox feed."""

    completed: MontyComplete
    printed: str


@dataclass
class CodeActRunState:
    """Monty resources owned by one Pydantic AI agent run."""

    config: CodeActConfig
    _pool: AsyncMonty | None = field(default=None, init=False, repr=False)
    _inline_session: AsyncMontySession | None = field(default=None, init=False, repr=False)
    _pool_stack: AsyncExitStack = field(default_factory=AsyncExitStack, init=False, repr=False)
    _inline_stack: AsyncExitStack = field(default_factory=AsyncExitStack, init=False, repr=False)
    _inline_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @property
    def limits(self) -> ResourceLimits:
        return {
            "max_duration_secs": self.config.timeout_seconds,
            "max_memory": self.config.max_memory_bytes,
            "max_recursion_depth": self.config.max_recursion_depth,
        }

    async def _get_pool(self) -> AsyncMonty:
        if self._pool is None:
            self._pool = await self._pool_stack.enter_async_context(
                AsyncMonty(
                    min_processes=1,
                    max_processes=2,
                    request_timeout=self.config.timeout_seconds,
                )
            )
        return self._pool

    async def _get_inline_session(self) -> AsyncMontySession:
        if self._inline_session is None:
            pool = await self._get_pool()
            self._inline_session = await self._inline_stack.enter_async_context(
                pool.checkout(script_name="run_code.py", limits=self.limits)
            )
        return self._inline_session

    async def reset_inline(self) -> None:
        await self._inline_stack.aclose()
        self._inline_stack = AsyncExitStack()
        self._inline_session = None

    async def execute_inline(
        self,
        code: str,
        *,
        dispatch: DispatchFn,
        admit: AdmitFn,
        release: ReleaseFn,
        valid_names: set[str],
        sequential_names: set[str],
        global_sequential: bool,
        restart: bool,
    ) -> CodeActExecution:
        if self._inline_lock.locked():
            raise RuntimeError("Another run_code call already owns the inline session")
        async with self._inline_lock:
            try:
                if restart:
                    await self.reset_inline()
                session = await self._get_inline_session()
                result = await self._execute(
                    session,
                    code,
                    dispatch=dispatch,
                    admit=admit,
                    release=release,
                    valid_names=valid_names,
                    sequential_names=sequential_names,
                    global_sequential=global_sequential,
                )
            except BaseException:
                await self.reset_inline()
                raise
            return result

    async def execute_program(
        self,
        code: str,
        *,
        script_name: str,
        inputs: dict[str, Any],
        dispatch: DispatchFn,
        admit: AdmitFn,
        release: ReleaseFn,
        valid_names: set[str],
        sequential_names: set[str],
        global_sequential: bool,
    ) -> CodeActExecution:
        pool = await self._get_pool()
        async with pool.checkout(script_name=script_name, limits=self.limits) as session:
            return await self._execute(
                session,
                code,
                inputs=inputs,
                dispatch=dispatch,
                admit=admit,
                release=release,
                valid_names=valid_names,
                sequential_names=sequential_names,
                global_sequential=global_sequential,
            )

    async def _execute(
        self,
        session: AsyncMontySession,
        code: str,
        *,
        dispatch: DispatchFn,
        admit: AdmitFn,
        release: ReleaseFn,
        valid_names: set[str],
        sequential_names: set[str],
        global_sequential: bool,
        inputs: dict[str, Any] | None = None,
    ) -> CodeActExecution:
        capture = CollectString(max_bytes=self.config.max_output_bytes)
        state = await session.feed_start(code, inputs=inputs, print_callback=capture)
        completed = await MontyExecutor(
            dispatch=dispatch,
            admit=admit,
            release=release,
            valid_names=valid_names,
            sequential_names=sequential_names,
            global_sequential=global_sequential,
            max_concurrency=self.config.max_concurrency,
        ).run(state)
        return CodeActExecution(completed=completed, printed=capture.output)

    async def close(self) -> None:
        try:
            await self.reset_inline()
        finally:
            await self._pool_stack.aclose()
            self._pool_stack = AsyncExitStack()
            self._pool = None
