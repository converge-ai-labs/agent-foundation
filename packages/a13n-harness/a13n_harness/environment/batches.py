"""Bounded ordered file batches for providers using individual file primitives."""

import json
from collections.abc import Sequence

from a13n_harness.environment.files import (
    MAX_FILE_BATCH_BYTES,
    MAX_FILE_BATCH_OPERATIONS,
    FileBatchValue,
    FileMkdirOperation,
    FileOperation,
    FileOperator,
    FileRemoveOperation,
    FileStatOperation,
    FileWriteTextOperation,
)
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError


def validate_file_batch(operations: Sequence[FileOperation]) -> None:
    if (
        len(operations) > MAX_FILE_BATCH_OPERATIONS
        or len(json.dumps([item.model_dump() for item in operations]).encode()) > MAX_FILE_BATCH_BYTES
    ):
        raise EnvironmentError("File batch exceeds its request limit.", code="environment_too_large")


def file_operation_action(operation: FileOperation) -> EnvironmentAction:
    return {
        "stat": EnvironmentAction.FILE_STAT,
        "mkdir": EnvironmentAction.FILE_MKDIR,
        "write_text": EnvironmentAction.FILE_WRITE_TEXT,
        "remove": EnvironmentAction.FILE_REMOVE,
    }[operation.action]


async def apply_file_batch(files: FileOperator, operations: Sequence[FileOperation]) -> tuple[FileBatchValue, ...]:
    validate_file_batch(operations)
    results: list[FileBatchValue] = []
    for operation in operations:
        try:
            match operation:
                case FileStatOperation():
                    result = await files.stat(operation.path)
                case FileMkdirOperation():
                    result = await files.mkdir(operation.path, parents=operation.parents, exist_ok=operation.exist_ok)
                case FileWriteTextOperation():
                    result = await files.write_text(operation.path, operation.text, mode=operation.mode)
                case FileRemoveOperation():
                    result = await files.remove(operation.path, recursive=operation.recursive)
        except EnvironmentError as error:
            if (
                isinstance(operation, (FileStatOperation, FileRemoveOperation))
                and operation.missing_ok
                and error.code == "environment_not_found"
            ):
                result = None
            else:
                raise
        results.append(result)
    return tuple(results)
