//! Clipboard admission and retained native resource accounting.
#![allow(
    clippy::result_large_err,
    reason = "generated EIP errors are the public contract"
)]
use super::*;
use crate::resource::map_path_error;

impl Session {
    pub(super) async fn read_clipboard(
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
        let computer = self.computer.clone();
        let filesystem = self.filesystem.clone();
        let ledger = self.operations.clone();
        let id = params.context.operation_id.clone();
        let check_id = id.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            // Native bytes, decoded/normalized text and bounded protocol metadata can overlap.
            let reservation = filesystem
                .reserve_staging(1024 * 1024)
                .map_err(|e| map_resource_error(map_path_error(e)))?;
            let worker_ledger = ledger.clone();
            let worker_id = check_id.clone();
            let result = tokio::task::spawn_blocking(move || {
                let _reservation = reservation;
                computer.read_clipboard(params.max_bytes, &|| {
                    worker_ledger.interruption(&worker_id).is_some()
                })
            })
            .await
            .map_err(|_| {
                protocol_error(ErrorType::InternalError, "clipboard read worker failed")
            })??;
            if let Some(interruption) = ledger.interruption(&check_id) {
                return Err(protocol_error(
                    match interruption {
                        OperationInterruption::Cancelled => ErrorType::Cancelled,
                        OperationInterruption::TimedOut => ErrorType::Timeout,
                    },
                    "clipboard read interrupted",
                ));
            }
            operation.finish(&result, None).map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }

    pub(super) async fn write_clipboard(
        &self,
        params: eip::ComputerWriteClipboardParams,
    ) -> Result<eip::ComputerWriteClipboardResult, EIPError> {
        self.ensure_ready()?;
        let method = "computer.write_clipboard";
        let (work, admitted) = self.admit_owned_record(method, &params.context, &params)?;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let (operation, _) = mutation_receipt_at(operation, method, ReceiptStage::Dispatched)?;
        let computer = self.computer.clone();
        let filesystem = self.filesystem.clone();
        let ledger = self.operations.clone();
        let id = params.context.operation_id.clone();
        let check_id = id.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            // A published payload retains this Session/Device charge until ownership loss or close.
            // Reserve overlap with normalization and an earlier published payload before mutation.
            let reservation = match filesystem.reserve_staging(32768) {
                Ok(reservation) => reservation,
                Err(error) => {
                    return Err(mutation_pre_dispatch_failure(
                        operation,
                        method,
                        map_resource_error(map_path_error(error)),
                    ));
                }
            };
            let worker_ledger = ledger.clone();
            let worker_id = check_id.clone();
            let effect = match tokio::task::spawn_blocking(move || {
                computer.write_clipboard(params.text, reservation, &|| {
                    worker_ledger.interruption(&worker_id).is_some()
                })
            })
            .await
            {
                Ok(Ok(effect)) => effect,
                Ok(Err(error)) => {
                    return Err(mutation_pre_dispatch_failure(operation, method, error));
                }
                Err(_) => {
                    return Err(mutation_failure(
                        operation,
                        method,
                        protocol_error(
                            ErrorType::UnknownOutcome,
                            "clipboard publication worker failed",
                        ),
                    ));
                }
            };
            let outcome = match ledger.interruption(&check_id) {
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
}
