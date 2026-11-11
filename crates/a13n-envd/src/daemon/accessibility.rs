//! Accessibility operations use the existing Session ledger and owned work.
#![allow(
    clippy::result_large_err,
    reason = "EIP errors retain operation evidence"
)]

use super::*;
use crate::eip::{EipExpandedJson, EipValidate};
use serde_json::Value;

use crate::accessibility::{NativeMutation, unknown_outcome};

impl Session {
    pub(super) async fn read_accessibility<P, R>(
        &self,
        method: &'static str,
        context: &eip::EIPCallContext,
        params: &P,
        timeout_ms: u32,
    ) -> Result<R, EIPError>
    where
        P: Serialize + EipValidate + EipExpandedJson,
        R: Serialize + DeserializeOwned + EipValidate + Send + 'static,
    {
        self.preflight(method)?;
        params.validate().map_err(|_| {
            protocol_error(ErrorType::InvalidParams, "invalid accessibility request")
        })?;
        let deadline = accessibility_deadline(context, timeout_ms);
        let mut payload = params.expanded_json().map_err(|_| {
            protocol_error(
                ErrorType::InternalError,
                "accessibility request encoding failed",
            )
        })?;
        if let Some(object) = payload.as_object_mut() {
            object.remove("context");
        }
        let (work, admitted) = self.admit_owned_record(method, context, params)?;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let id = context.operation_id.clone();
        let worker_id = id.clone();
        let supervisor = self.accessibility.clone();
        let computer = self.computer.clone();
        let ledger = self.operations.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            let projection = payload
                .get("projection_observation_id")
                .and_then(Value::as_str)
                .map(str::to_owned);
            let point_computer = computer.clone();
            let mut payload = tokio::task::spawn_blocking(move || {
                prepare_screenshot_point(&point_computer, &mut payload)?;
                Ok::<_, EIPError>(payload)
            })
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "screenshot mapping failed"))??;
            if let Some(object) = payload.as_object_mut() {
                object.remove("projection_observation_id");
            }
            if let Some(projection) = &projection {
                payload["_projection_bytes"] = serde_json::json!(
                    crate::accessibility::budget::projection_reserve(projection)?
                );
            }
            let max_bytes = payload["limits"]["max_result_bytes"]
                .as_u64()
                .map(|value| value as usize);
            let mut value = supervisor
                .call(method, payload, deadline, || {
                    ledger.interruption(&worker_id)
                })
                .await
                .map_err(|failure| failure.error)?;
            if let Some(projection) = projection {
                value = tokio::task::spawn_blocking(move || {
                    computer.project_accessibility(&projection, &mut value)?;
                    Ok::<_, EIPError>(value)
                })
                .await
                .map_err(|_| {
                    protocol_error(ErrorType::InternalError, "screenshot projection failed")
                })??;
            }
            if let Some(max) = max_bytes
                && crate::accessibility::budget::result_bytes(method, &value, 0)? > max
            {
                return Err(protocol_error(
                    ErrorType::InternalError,
                    "native accessibility result exceeded its byte budget",
                ));
            }
            let result: R = serde_json::from_value(value).map_err(|_| {
                protocol_error(
                    ErrorType::InternalError,
                    "invalid native accessibility result",
                )
            })?;
            result.validate().map_err(|_| {
                protocol_error(
                    ErrorType::InternalError,
                    "invalid native accessibility result",
                )
            })?;
            operation.finish(&result, None).map_err(map_ledger_error)?;
            Ok(result)
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }

    pub(super) async fn mutate_accessibility<P: Serialize + EipValidate + EipExpandedJson>(
        &self,
        method: &'static str,
        context: &eip::EIPCallContext,
        params: &P,
    ) -> Result<eip::AccessibilityMutationResult, EIPError> {
        self.preflight(method)?;
        params.validate().map_err(|_| {
            protocol_error(ErrorType::InvalidParams, "invalid accessibility mutation")
        })?;
        let deadline = accessibility_deadline(context, 5000);
        let mut payload = params.expanded_json().map_err(|_| {
            protocol_error(
                ErrorType::InternalError,
                "accessibility request encoding failed",
            )
        })?;
        if let Some(object) = payload.as_object_mut() {
            object.remove("context");
        }
        let admission = self.admit_owned_record(method, context, params)?;
        self.run_accessibility_mutation(method, context, payload, deadline, admission)
            .await
    }

    // All native mutations share one execution state machine after typed admission.
    // Keeping it non-generic avoids duplicating owned tasks for every parameter type.
    async fn run_accessibility_mutation(
        &self,
        method: &'static str,
        context: &eip::EIPCallContext,
        mut payload: Value,
        deadline: tokio::time::Instant,
        admission: (SessionWorkGuard<'_>, BeginOutcome),
    ) -> Result<eip::AccessibilityMutationResult, EIPError> {
        let (work, admitted) = admission;
        let operation = match admitted {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
        };
        let (operation, _) = mutation_receipt_at(operation, method, ReceiptStage::Accepted)?;
        let readback = payload["readback"].as_bool().unwrap_or(false);
        let target = payload["node"].clone();
        // Receive the native acknowledgement before doing optional observation.
        // A blocked observer must not turn a known native reply into unknown.
        payload["readback"] = Value::Bool(false);
        let id = context.operation_id.clone();
        let worker_id = id.clone();
        let supervisor = self.accessibility.clone();
        let ledger = self.operations.clone();
        let owned = self.operations.spawn_owned(id.clone(), async move {
            let reply = supervisor
                .call(method, payload, deadline, || {
                    ledger.interruption(&worker_id)
                })
                .await;
            let value = match reply {
                Ok(value) => value,
                Err(failure) if !failure.uncertain => {
                    return Err(mutation_pre_dispatch_failure(
                        operation,
                        method,
                        failure.error,
                    ));
                }
                Err(failure) => return Err(mutation_failure(operation, method, failure.error)),
            };
            let observation = async {
                supervisor
                    .call(
                        "computer.a11y_inspect",
                        serde_json::json!({"node":target,"limits":{"depth":0,"max_nodes":1},"view":"control"}),
                        deadline,
                        || ledger.interruption(&worker_id),
                    )
                    .await
                    .map_err(|failure| failure.error)
            };
            finish_native_mutation_with_readback(
                operation,
                method,
                value,
                readback,
                &target,
                observation,
            )
            .await
        });
        drop(work);
        self.await_owned_operation(&id, owned).await
    }
}

fn prepare_screenshot_point(
    computer: &crate::computer::Computer,
    payload: &mut Value,
) -> Result<(), EIPError> {
    let Some(scope) = payload.get_mut("scope") else {
        return Ok(());
    };
    if scope["kind"] != "screenshot_point" {
        return Ok(());
    }
    let id = scope["observation_id"].as_str().ok_or_else(|| {
        protocol_error(
            ErrorType::InvalidParams,
            "screenshot point requires an observation",
        )
    })?;
    let point: eip::ComputerPoint =
        serde_json::from_value(scope["image_point"].clone()).map_err(|_| {
            protocol_error(
                ErrorType::InvalidParams,
                "screenshot point requires image coordinates",
            )
        })?;
    let native = computer.accessibility_point(id, &point)?;
    *scope = serde_json::json!({"kind": "at_point", "point": native});
    Ok(())
}

fn accessibility_deadline(context: &eip::EIPCallContext, timeout_ms: u32) -> tokio::time::Instant {
    tokio::time::Instant::now()
        + Duration::from_millis(
            u64::from(timeout_ms)
                .min(context.timeout_ms.unwrap_or(15000))
                .min(15000),
        )
}

async fn finish_native_mutation_with_readback(
    operation: OperationLease,
    method: &str,
    mut value: Value,
    readback: bool,
    target: &Value,
    observation: impl std::future::Future<Output = Result<Value, EIPError>>,
) -> Result<eip::AccessibilityMutationResult, EIPError> {
    // Validate the acknowledgement independently, before polling the observer.
    if let Err(error) = decode_native_mutation(value.clone(), false, target) {
        return Err(mutation_failure(operation, method, error));
    }
    if readback
        && let Ok(tree) = observation.await
        && crate::accessibility::budget::result_bytes("computer.a11y_inspect", &tree, 0).is_ok_and(
            |bytes| bytes <= eip::AccessibilityLimits::default().max_result_bytes as usize,
        )
    {
        // The observer is an independent read. Validate its candidate before
        // attaching it to the already validated mutation acknowledgement.
        if let Some(node) = tree["nodes"]
            .as_array()
            .and_then(|nodes| nodes.iter().find(|node| &node["reference"] == target))
            && let Ok(observation) = serde_json::from_value::<eip::AccessibilityNode>(node.clone())
            && observation.validate().is_ok()
        {
            value["observation"] = node.clone();
        }
    }
    finish_native_mutation(operation, method, value, readback, target)
}

fn finish_native_mutation(
    operation: OperationLease,
    method: &str,
    value: Value,
    readback: bool,
    target: &Value,
) -> Result<eip::AccessibilityMutationResult, EIPError> {
    let native = match decode_native_mutation(value, readback, target) {
        Ok(native) => native,
        Err(error) => return Err(mutation_failure(operation, method, error)),
    };
    let outcome = if native.effect == eip::ComputerEffect::Executed {
        ReceiptOutcome::Succeeded
    } else {
        ReceiptOutcome::Failed
    };
    let receipt = operation
        .receipt(method, ReceiptStage::Completed, Some(outcome))
        .map_err(map_ledger_error)?;
    let result = eip::AccessibilityMutationResult {
        receipt: receipt.clone(),
        effect: native.effect,
        native_status: native.native_status,
        dispatch_complete: native.dispatch_complete,
        observation: native.observation,
        application_outcome: "unverified".to_owned(),
    };
    operation
        .finish(&result, Some(receipt))
        .map_err(map_ledger_error)?;
    Ok(result)
}

fn decode_native_mutation(
    value: Value,
    readback: bool,
    target: &Value,
) -> Result<NativeMutation, EIPError> {
    let native: NativeMutation = serde_json::from_value(value)
        .map_err(|_| unknown_outcome("native mutation returned invalid evidence"))?;
    if !native.dispatch_complete
        || native.effect == eip::ComputerEffect::Unknown
        || native.native_status == eip::AccessibilityMutationResultNativeStatus::NoReply
    {
        return Err(unknown_outcome(
            "native mutation completion is unknown; reconcile before retrying",
        ));
    }
    if (native.effect == eip::ComputerEffect::Executed)
        != (native.native_status == eip::AccessibilityMutationResultNativeStatus::ReturnedSuccess)
    {
        return Err(unknown_outcome(
            "native mutation returned inconsistent acknowledgement evidence",
        ));
    }
    if let Some(observation) = &native.observation
        && (!readback
            || observation.validate().is_err()
            || !serde_json::to_value(&observation.reference)
                .is_ok_and(|reference| &reference == target))
    {
        return Err(unknown_outcome(
            "native mutation returned invalid observation evidence",
        ));
    }
    Ok(native)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const METHOD: &str = "computer.atspi_focus";

    fn native() -> Value {
        json!({"effect": "executed", "native_status": "returned_success", "dispatch_complete": true})
    }

    fn target() -> Value {
        json!({"backend": "atspi", "backend_epoch": "aep-test", "node_handle": "node-test"})
    }

    fn observation() -> Value {
        json!({"reference": target(), "native_role": "entry", "sampled_at": "2026-11-09T00:00:00Z"})
    }

    fn lease() -> (OperationLedger, OperationLease) {
        let ledger = OperationLedger::new(
            "dev-test".to_owned(),
            1,
            "ses-test".to_owned(),
            8,
            Duration::from_secs(60),
            Duration::from_secs(15),
            Arc::new(Semaphore::new(8)),
        );
        let context = eip::EIPCallContext {
            operation_id: "op-test".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(operation) = ledger.begin(METHOD, &context, &json!({})).unwrap()
        else {
            panic!("new operation");
        };
        let (operation, _) =
            mutation_receipt_at(operation, METHOD, ReceiptStage::Accepted).unwrap();
        (ledger, operation)
    }

    #[test]
    fn native_acknowledgement_has_session_receipt_and_unverified_task_outcome() {
        let (ledger, operation) = lease();
        let result = finish_native_mutation(operation, METHOD, native(), false, &target()).unwrap();
        assert_eq!(result.application_outcome, "unverified");
        assert_eq!(result.receipt.stage, ReceiptStage::Completed);
        assert_eq!(result.receipt.outcome, Some(ReceiptOutcome::Succeeded));
        assert_eq!(result.receipt.method, METHOD);
        assert_eq!(result.receipt.session_id, "ses-test");
        assert!(ledger.resolve(METHOD, "op-test").unwrap().is_ok());
    }

    #[tokio::test]
    async fn known_native_replies_survive_a_readback_that_never_replies() {
        for (effect, status, outcome) in [
            ("executed", "returned_success", ReceiptOutcome::Succeeded),
            ("not_executed", "returned_false", ReceiptOutcome::Failed),
        ] {
            let (ledger, operation) = lease();
            let observation = async {
                tokio::time::timeout(Duration::from_millis(10), std::future::pending::<()>())
                    .await
                    .unwrap_err();
                Err(protocol_error(ErrorType::Timeout, "readback timed out"))
            };
            let result = finish_native_mutation_with_readback(
                operation,
                METHOD,
                json!({"effect":effect,"native_status":status,"dispatch_complete":true}),
                true,
                &target(),
                observation,
            )
            .await
            .unwrap();
            assert_eq!(result.receipt.stage, ReceiptStage::Completed);
            assert_eq!(result.receipt.outcome, Some(outcome));
            assert!(result.observation.is_none());
            assert!(ledger.resolve(METHOD, "op-test").unwrap().is_ok());
        }
    }

    #[tokio::test]
    async fn invalid_independent_readback_preserves_known_native_reply() {
        for invalid in [
            json!({"reference": target()}),
            json!({"reference": target(), "native_role":"entry", "sampled_at":"2026-11-09T00:00:00Z", "child_count":-1}),
        ] {
            for (effect, status) in [
                ("executed", "returned_success"),
                ("not_executed", "returned_false"),
            ] {
                let (ledger, operation) = lease();
                let result = finish_native_mutation_with_readback(
                    operation,
                    METHOD,
                    json!({"effect":effect,"native_status":status,"dispatch_complete":true}),
                    true,
                    &target(),
                    async { Ok(json!({"nodes":[invalid]})) },
                )
                .await
                .unwrap();
                assert_eq!(result.receipt.stage, ReceiptStage::Completed);
                assert!(result.observation.is_none());
                assert!(result.dispatch_complete);
                assert_eq!(serde_json::to_value(result.effect).unwrap(), effect);
                assert_eq!(serde_json::to_value(result.native_status).unwrap(), status);
                assert!(ledger.resolve(METHOD, "op-test").unwrap().is_ok());
            }
        }
    }

    #[tokio::test]
    async fn requested_post_acknowledgement_observation_is_retained() {
        let (_, operation) = lease();
        let result = finish_native_mutation_with_readback(
            operation,
            METHOD,
            native(),
            true,
            &target(),
            async { Ok(json!({"backend":"atspi","sampled_at":"2026-11-09T00:00:00Z","nodes":[observation()],"completeness":{"complete":true}})) },
        )
        .await
        .unwrap();
        assert!(result.observation.is_some());
    }

    #[test]
    fn invalid_native_evidence_retains_reconcile_first_terminal_failure() {
        let mut forged_receipt = native();
        forged_receipt["receipt"] = json!({});
        let mut invalid_observation = native();
        invalid_observation["observation"] = json!({"native_role": "entry"});
        let mut inconsistent = native();
        inconsistent["native_status"] = json!("returned_false");
        let mut incomplete = native();
        incomplete["dispatch_complete"] = json!(false);
        let mut unknown = native();
        unknown["effect"] = json!("unknown");
        for value in [
            forged_receipt,
            invalid_observation,
            inconsistent,
            incomplete,
            unknown,
        ] {
            let (ledger, operation) = lease();
            let error =
                finish_native_mutation(operation, METHOD, value, true, &target()).unwrap_err();
            assert_eq!(error.data.error_type, ErrorType::UnknownOutcome);
            assert_eq!(error.data.retry_hint, RetryHint::ReconcileFirst);
            assert_eq!(
                error.data.receipt.as_ref().unwrap().outcome,
                Some(ReceiptOutcome::Unknown)
            );
            let retained = ledger.resolve(METHOD, "op-test").unwrap().unwrap_err();
            assert_eq!(retained.data.retry_hint, RetryHint::ReconcileFirst);
            assert_eq!(retained.data.dispatch_stage, DispatchStage::Unknown);
        }
    }

    #[test]
    fn readback_requires_requested_target_and_valid_native_observation() {
        let mut value = native();
        value["observation"] = observation();
        assert!(decode_native_mutation(value.clone(), false, &target()).is_err());
        assert!(decode_native_mutation(value.clone(), true, &target()).is_ok());
        value["observation"]["reference"]["node_handle"] = json!("node-other");
        assert!(decode_native_mutation(value, true, &target()).is_err());
    }

    #[test]
    fn failed_native_acknowledgement_is_not_reported_as_success() {
        let (ledger, operation) = lease();
        let result = finish_native_mutation(operation, METHOD,
            json!({"effect": "not_executed", "native_status": "returned_false", "dispatch_complete": true}),
            false, &target()).unwrap();
        assert_eq!(result.effect, eip::ComputerEffect::NotExecuted);
        assert_eq!(result.receipt.outcome, Some(ReceiptOutcome::Failed));
        assert!(ledger.resolve(METHOD, "op-test").unwrap().is_ok());
    }
}
