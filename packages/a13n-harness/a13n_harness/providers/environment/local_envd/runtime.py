from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tempfile
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)

A13N_ENVD_EXECUTABLE = "A13N_ENVD_EXECUTABLE"
_PROVIDER_KEY = "local_envd"
_PLATFORM_EXECUTABLE = "a13n-envd.exe" if os.name == "nt" else "a13n-envd"

type LocalEnvdRuntimeAllocator = Callable[[], AbstractAsyncContextManager[Path]]


@dataclass(frozen=True, slots=True)
class LocalEnvdProviderRuntime:
    executable: Path
    allocate_private_runtime: LocalEnvdRuntimeAllocator

    def __post_init__(self) -> None:
        executable = self.executable.expanduser()
        if "\x00" in str(executable) or not executable.is_absolute():
            raise ValueError("Local Envd runtime executable must be an absolute path without NUL")
        if not callable(self.allocate_private_runtime):
            raise TypeError("Local Envd private runtime allocator must be callable")
        object.__setattr__(self, "executable", executable)


@dataclass(frozen=True, slots=True)
class TemporaryLocalEnvdRuntimeAllocator:
    """Allocate one private temporary directory per entered Local Envd adapter."""

    parent: Path | None = None
    prefix: str = "a13n-local-envd-"

    def __post_init__(self) -> None:
        if self.parent is not None:
            parent = self.parent.expanduser()
            if "\x00" in str(parent):
                raise ValueError("temporary runtime parent must not contain NUL")
            object.__setattr__(self, "parent", parent)
        if not self.prefix or "\x00" in self.prefix:
            raise ValueError("temporary runtime prefix must be nonblank and contain no NUL")

    def __call__(self) -> AbstractAsyncContextManager[Path]:
        @asynccontextmanager
        async def allocate() -> AsyncGenerator[Path]:
            directory = await asyncio.to_thread(
                tempfile.mkdtemp,
                prefix=self.prefix,
                dir=str(self.parent) if self.parent is not None else None,
            )
            root = Path(directory).resolve()
            try:
                if os.name == "posix":
                    await asyncio.to_thread(root.chmod, 0o700)
                yield root
            finally:
                await asyncio.to_thread(shutil.rmtree, root)

        return allocate()


def resolve_a13n_envd_executable(explicit_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve and validate one exact Host-selected a13n-envd executable."""

    candidate: str | os.PathLike[str] | None = explicit_path
    source = "explicit path"
    if candidate is None:
        candidate = os.environ.get(A13N_ENVD_EXECUTABLE)
        source = A13N_ENVD_EXECUTABLE
    if candidate is None:
        discovered = shutil.which(_PLATFORM_EXECUTABLE)
        if discovered is None:
            raise _runtime_error(
                f"Could not find {_PLATFORM_EXECUTABLE!r} through explicit configuration, "
                f"{A13N_ENVD_EXECUTABLE}, or PATH."
            )
        candidate = discovered
        source = "PATH"

    raw = Path(candidate).expanduser()
    if "\x00" in str(raw):
        raise _runtime_error(f"The Local Envd executable selected by {source} contains NUL.")
    resolved = Path(os.path.abspath(raw))
    try:
        metadata = resolved.stat()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _runtime_error(f"The Local Envd executable selected by {source} does not exist: {raw}") from exc
    except (OSError, ValueError) as exc:
        raise _runtime_error(f"The Local Envd executable selected by {source} cannot be inspected: {raw}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise _runtime_error(f"The Local Envd executable selected by {source} is not a regular file: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise _runtime_error(f"The Local Envd executable selected by {source} is not executable: {resolved}")
    return resolved


def _runtime_error(description: str) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_runtime_invalid",
        category=EnvironmentProviderErrorCategory.INVALID,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.REFRESH_RUNTIME,
        context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
    )
