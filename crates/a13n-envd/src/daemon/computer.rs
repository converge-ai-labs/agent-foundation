//! EIP integration reuses Session admission, mutation evidence and byte transfers.
#![allow(
    clippy::result_large_err,
    reason = "generated EIP errors are the public contract"
)]
use super::*;
use crate::computer::Action;

impl Session {
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
        // Session-owned work retains operation capacity and joins native capture
        // after a transport waiter disappears; no screenshot is replayed as input.
        let owned = self.operations.spawn_owned(id.clone(), async move {
            let (observation, bytes) =
                tokio::task::spawn_blocking(move || computer.observe(&params))
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
                .open_image_reader(bytes)
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
