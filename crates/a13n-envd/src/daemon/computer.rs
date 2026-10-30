//! EIP integration reuses Session admission, mutation evidence and byte transfers.
#![allow(
    clippy::result_large_err,
    reason = "generated EIP errors are the public contract"
)]
use super::*;
use crate::{computer::Action, resource::map_path_error};

impl Session {
    pub(super) async fn read_computer_clipboard(
        &self,
        params: eip::ComputerReadClipboardParams,
    ) -> Result<eip::ComputerReadClipboardResult, EIPError> {
        self.ensure_ready()?;
        let (work, admitted) =
            self.admit_owned_record("computer.read_clipboard", &params.context, &params)?;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let id = params.context.operation_id.clone();
        let worker_id = id.clone();
        let computer = self.computer.clone();
        let ledger = self.operations.clone();
        let filesystem = self.filesystem.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            let result = tokio::task::spawn_blocking(move || {
                computer.reserve_clipboard(&filesystem)?;
                computer.read_clipboard(params.max_bytes, &|| {
                    ledger.interruption(&worker_id).is_some()
                })
            })
            .await
            .map_err(|_| {
                protocol_error(ErrorType::InternalError, "clipboard reader worker failed")
            })??;
            operation.finish(&result, None).map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }

    pub(super) async fn write_computer_clipboard(
        &self,
        params: eip::ComputerWriteClipboardParams,
    ) -> Result<eip::ComputerWriteClipboardResult, EIPError> {
        const METHOD: &str = "computer.write_clipboard";
        self.ensure_ready()?;
        let (work, admitted) = self.admit_owned_record(METHOD, &params.context, &params)?;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let (operation, _) = mutation_receipt_at(operation, METHOD, ReceiptStage::Dispatched)?;
        let id = params.context.operation_id.clone();
        let worker_id = id.clone();
        let computer = self.computer.clone();
        let ledger = self.operations.clone();
        let filesystem = self.filesystem.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            let check_id = worker_id.clone();
            let worker_ledger = ledger.clone();
            let execution = tokio::task::spawn_blocking(move || {
                computer.reserve_clipboard(&filesystem)?;
                computer.write_clipboard(&params.text, &|| {
                    worker_ledger.interruption(&check_id).is_some()
                })
            })
            .await;
            let effect = match execution {
                Ok(Ok(effect)) => effect,
                Ok(Err(error)) => {
                    return Err(mutation_pre_dispatch_failure(operation, METHOD, error));
                }
                Err(_) => {
                    return Err(mutation_failure(
                        operation,
                        METHOD,
                        protocol_error(ErrorType::UnknownOutcome, "clipboard writer worker failed"),
                    ));
                }
            };
            let outcome = match ledger.interruption(&worker_id) {
                Some(OperationInterruption::Cancelled) => ReceiptOutcome::Cancelled,
                Some(OperationInterruption::TimedOut) => ReceiptOutcome::TimedOut,
                None if effect.effect == eip::ComputerEffect::Executed
                    && effect.cleanup_complete =>
                {
                    ReceiptOutcome::Succeeded
                }
                None => ReceiptOutcome::Failed,
            };
            let receipt = operation
                .receipt(METHOD, ReceiptStage::Completed, Some(outcome))
                .map_err(map_ledger_error)?;
            let result = eip::ComputerWriteClipboardResult {
                receipt: receipt.clone(),
                effect: effect.effect,
                cleanup_complete: effect.cleanup_complete,
            };
            operation
                .finish(&result, Some(receipt))
                .map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }

    pub(super) async fn describe_computer(
        &self,
        params: eip::ComputerDescribeParams,
    ) -> Result<eip::ComputerDescribeResult, EIPError> {
        self.ensure_ready()?;
        let (work, admitted) =
            self.admit_owned_record("computer.describe", &params.context, &params)?;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let computer = self.computer.clone();
        let id = params.context.operation_id.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            let result = tokio::task::spawn_blocking(move || computer.describe())
                .await
                .map_err(|_| {
                    protocol_error(
                        ErrorType::InternalError,
                        "computer description worker failed",
                    )
                })??;
            operation.finish(&result, None).map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }

    pub(super) async fn observe_computer(
        &self,
        params: eip::ComputerObserveParams,
    ) -> Result<eip::ComputerObserveResult, EIPError> {
        self.ensure_ready()?;
        let (work, admitted) =
            self.admit_owned_record("computer.observe", &params.context, &params)?;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let computer = self.computer.clone();
        let id = params.context.operation_id.clone();
        let operation_id = id.clone();
        let ledger = self.operations.clone();
        let transfers = self.transfers.clone();
        let filesystem = self.filesystem.clone();
        // Session-owned work retains operation capacity and joins native capture
        // after a transport waiter disappears; no screenshot is replayed as input.
        let owned = self.operations.spawn_owned(id.clone(), async move {
            // Reserve the bounded encoded image before native capture. On
            // publication the reader shrinks this to the retained byte length.
            transfers.expire().await;
            let staging = filesystem
                .reserve_staging(crate::computer::MAX_IMAGE_BYTES as u64)
                .map_err(|error| map_resource_error(map_path_error(error)))?;
            let (observation, bytes, staging) = tokio::task::spawn_blocking(move || {
                let (observation, bytes) = computer.observe(&params)?;
                Ok::<_, EIPError>((observation, bytes, staging))
            })
            .await
            .map_err(|_| {
                protocol_error(ErrorType::InternalError, "screen capture worker failed")
            })??;
            if let Some(interruption) = ledger.interruption(&operation_id) {
                return Err(protocol_error(
                    match interruption {
                        OperationInterruption::Cancelled => ErrorType::Cancelled,
                        OperationInterruption::TimedOut => ErrorType::Timeout,
                    },
                    "screen capture interrupted",
                ));
            }
            let size_bytes = bytes.len() as u64;
            let (reader, expires_at) = transfers
                .open_image_reader(bytes, staging)
                .await
                .map_err(map_transfer_error)?;
            let result = eip::ComputerObserveResult {
                observation,
                reader,
                size_bytes,
                expires_at,
            };
            operation.finish(&result, None).map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }

    pub(super) async fn zoom_computer(
        &self,
        params: eip::ComputerZoomParams,
    ) -> Result<eip::ComputerZoomResult, EIPError> {
        self.ensure_ready()?;
        let (work, admitted) =
            self.admit_owned_record("computer.zoom", &params.context, &params)?;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let computer = self.computer.clone();
        let id = params.context.operation_id.clone();
        let operation_id = id.clone();
        let ledger = self.operations.clone();
        let transfers = self.transfers.clone();
        let filesystem = self.filesystem.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            transfers.expire().await;
            let staging = filesystem
                .reserve_staging(crate::computer::MAX_IMAGE_BYTES as u64)
                .map_err(|error| map_resource_error(map_path_error(error)))?;
            let (image, bytes, staging) = tokio::task::spawn_blocking(move || {
                let (image, bytes) = computer.zoom(&params)?;
                Ok::<_, EIPError>((image, bytes, staging))
            })
            .await
            .map_err(|_| {
                protocol_error(ErrorType::InternalError, "screen capture worker failed")
            })??;
            if let Some(interruption) = ledger.interruption(&operation_id) {
                return Err(protocol_error(
                    match interruption {
                        OperationInterruption::Cancelled => ErrorType::Cancelled,
                        OperationInterruption::TimedOut => ErrorType::Timeout,
                    },
                    "screen capture interrupted",
                ));
            }
            let size_bytes = bytes.len() as u64;
            let (reader, expires_at) = transfers
                .open_image_reader(bytes, staging)
                .await
                .map_err(map_transfer_error)?;
            let result = eip::ComputerZoomResult {
                image,
                reader,
                size_bytes,
                expires_at,
            };
            operation.finish(&result, None).map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }

    pub(super) async fn close_computer_observation(
        &self,
        params: eip::FileReaderCloseParams,
    ) -> Result<eip::FileReaderCloseResult, EIPError> {
        self.ensure_ready()?;
        if !params.reader.0.starts_with("screen-") {
            return Err(protocol_error(
                ErrorType::InvalidHandle,
                "not a computer image reader",
            ));
        }
        let operation =
            match self.admit_record("computer.close_observation", &params.context, &params)? {
                BeginOutcome::New(operation) => operation,
                BeginOutcome::Replay(value) => return self.decode_replay(value),
                BeginOutcome::ReplayFailure(error) => return Err(*error),
            };
        let result = self
            .transfers
            .close_reader(&params.reader)
            .await
            .map_err(map_transfer_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    pub(super) async fn execute_computer<P: Serialize>(
        &self,
        method: &'static str,
        context: &eip::EIPCallContext,
        params: &P,
        action: Action,
    ) -> Result<eip::ComputerActionResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) = self.admit_owned_record(method, context, params)?;
        let operation = match operation {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let (operation, _) = mutation_receipt_at(operation, method, ReceiptStage::Dispatched)?;
        let id = context.operation_id.clone();
        let computer = self.computer.clone();
        let ledger = self.operations.clone();
        let worker_id = id.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            let worker_ledger = ledger.clone();
            let check_id = worker_id.clone();
            let execution = tokio::task::spawn_blocking(move || {
                computer.execute(&action, &|| worker_ledger.interruption(&check_id).is_some())
            })
            .await;
            let effect = match execution {
                Ok(Ok(effect)) => effect,
                // Native preflight errors prove no input was dispatched. After the
                // first event the backend returns an evidence-bearing Effect instead.
                Ok(Err(error)) => {
                    return Err(mutation_pre_dispatch_failure(operation, method, error));
                }
                Err(_) => {
                    return Err(mutation_failure(
                        operation,
                        method,
                        protocol_error(ErrorType::UnknownOutcome, "native input worker failed"),
                    ));
                }
            };
            let outcome = match ledger.interruption(&worker_id) {
                Some(OperationInterruption::Cancelled) => ReceiptOutcome::Cancelled,
                Some(OperationInterruption::TimedOut) => ReceiptOutcome::TimedOut,
                None if effect.effect == eip::ComputerEffect::Executed
                    && effect.cleanup_complete =>
                {
                    ReceiptOutcome::Succeeded
                }
                None => ReceiptOutcome::Failed,
            };
            let receipt = operation
                .receipt(method, ReceiptStage::Completed, Some(outcome))
                .map_err(map_ledger_error)?;
            let result = eip::ComputerActionResult {
                receipt: receipt.clone(),
                effect: effect.effect,
                input_cleanup_complete: effect.cleanup_complete,
            };
            operation
                .finish(&result, Some(receipt))
                .map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }
}
