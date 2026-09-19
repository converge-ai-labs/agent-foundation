use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    future::Future,
    sync::{Arc, Mutex, PoisonError},
    time::{Duration, Instant},
};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tokio::sync::{Notify, OwnedSemaphorePermit, Semaphore, oneshot, watch};

use crate::eip::{
    EIPCallContext, EIPError, OperationCancelStatus, OperationReceipt, ReceiptOutcome, ReceiptStage,
};

// Ordinary operation evidence cannot consume this slot. Reconciliation operations
// may use both unused ordinary capacity and this reserve.
const RECONCILIATION_RECORD_RESERVE: usize = 1;

tokio::task_local! {
    static CARRIER_ATTEMPT: u64;
}

pub(crate) async fn scope_carrier_attempt<F: Future>(attempt: u64, future: F) -> F::Output {
    CARRIER_ATTEMPT.scope(attempt, future).await
}

#[derive(Clone)]
pub(crate) struct OperationLedger {
    inner: Arc<LedgerInner>,
}

#[derive(Clone)]
pub(crate) struct ShortIdAllocator {
    namespace: Arc<str>,
    counters: Arc<Mutex<BTreeMap<&'static str, u64>>>,
}

struct LedgerInner {
    state: Mutex<LedgerState>,
    pending_changed: Notify,
    owned_idle: Notify,
    #[cfg(test)]
    pending_wait_entered: Notify,
    device_id: String,
    session_id: String,
    generation: u64,
    ordinary_record_capacity: usize,
    device_capacity: Arc<Semaphore>,
    terminal_ttl: Duration,
    max_duration: Duration,
}

#[derive(Default)]
struct LedgerState {
    records: BTreeMap<String, OperationRecord>,
    terminal_order: VecDeque<String>,
    pending: BTreeMap<String, PendingOperationState>,
    next_attempt: u64,
    draining: bool,
}

#[derive(Clone, Copy)]
struct PendingOperationState {
    requests: usize,
    admitted: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ReplayClass {
    ActiveOnly,
    TerminalEvidence,
}

struct OperationRecord {
    _capacity: Option<OwnedSemaphorePermit>,
    last_access: Instant,
    method: String,
    request_digest: String,
    replay_class: ReplayClass,
    reconciliation: bool,
    attempt: u64,
    carrier_attempt: Option<u64>,
    status: RecordStatus,
    cancellation_requested: bool,
    deadline: Instant,
    receipt: Option<OperationReceipt>,
    result: Option<serde_json::Value>,
    failure: Option<EIPError>,
    pins: BTreeSet<String>,
    owned: bool,
}

enum RecordStatus {
    Active,
    Completing,
    Terminal { completed_at: Instant },
}

pub(crate) enum BeginOutcome {
    New(OperationLease),
    Replay(serde_json::Value),
    ReplayFailure(Box<EIPError>),
}

pub(crate) struct OperationLease {
    ledger: OperationLedger,
    operation_id: String,
    replay_class: ReplayClass,
    failure_on_drop: Option<Box<(OperationReceipt, EIPError)>>,
    finished: bool,
}

pub(crate) struct ActiveResponseHandoff {
    ledger: OperationLedger,
    operation_id: String,
    attempt: u64,
    completed: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PendingAdmissionWait {
    Admitted,
    Removed,
    TimedOut,
    Closed,
}

pub(crate) struct PendingOperationGuard {
    ledger: OperationLedger,
    operation_id: String,
}

struct OwnedOperationTaskGuard {
    ledger: OperationLedger,
    operation_id: String,
    attempt: u64,
}

pub(crate) type OwnedOperationResult<T> = oneshot::Receiver<Option<Result<T, EIPError>>>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LedgerError {
    Collision,
    DeadlineExpired,
    InProgress,
    TerminalFailure,
    Capacity,
    Encoding,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum OperationInterruption {
    Cancelled,
    TimedOut,
}

impl OperationLedger {
    pub(crate) fn new(
        device_id: String,
        generation: u64,
        session_id: String,
        ordinary_record_capacity: usize,
        terminal_ttl: Duration,
        max_duration: Duration,
        device_capacity: Arc<Semaphore>,
    ) -> Self {
        Self {
            inner: Arc::new(LedgerInner {
                state: Mutex::new(LedgerState::default()),
                pending_changed: Notify::new(),
                owned_idle: Notify::new(),
                #[cfg(test)]
                pending_wait_entered: Notify::new(),
                device_id,
                session_id,
                generation,
                ordinary_record_capacity,
                device_capacity,
                terminal_ttl,
                max_duration,
            }),
        }
    }

    pub(crate) fn track_pending(&self, operation_id: String) -> PendingOperationGuard {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let pending = state
            .pending
            .entry(operation_id.clone())
            .or_insert(PendingOperationState {
                requests: 0,
                admitted: false,
            });
        pending.requests = pending
            .requests
            .checked_add(1)
            .expect("pending request accounting overflow");
        PendingOperationGuard {
            ledger: self.clone(),
            operation_id,
        }
    }

    pub(crate) async fn wait_for_admission(
        &self,
        operation_id: &str,
        deadline: Instant,
        mut closed: watch::Receiver<bool>,
    ) -> PendingAdmissionWait {
        loop {
            let changed = self.inner.pending_changed.notified();
            let status = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .pending
                .get(operation_id)
                .map(|pending| pending.admitted);
            match status {
                Some(true) => return PendingAdmissionWait::Admitted,
                None => return PendingAdmissionWait::Removed,
                Some(false) if *closed.borrow() => return PendingAdmissionWait::Closed,
                Some(false) => {
                    #[cfg(test)]
                    self.inner.pending_wait_entered.notify_one();
                }
            }
            tokio::select! {
                _ = changed => {}
                changed = closed.changed() => {
                    if changed.is_err() || *closed.borrow() {
                        return PendingAdmissionWait::Closed;
                    }
                }
                _ = tokio::time::sleep_until(tokio::time::Instant::from_std(deadline)) => {
                    return PendingAdmissionWait::TimedOut;
                }
            }
        }
    }

    #[cfg(test)]
    pub(crate) async fn wait_until_admission_wait(&self) {
        self.inner.pending_wait_entered.notified().await;
    }

    pub(crate) fn spawn_owned<T, F>(
        &self,
        operation_id: String,
        future: F,
    ) -> OwnedOperationResult<T>
    where
        T: Send + 'static,
        F: Future<Output = Result<T, EIPError>> + Send + 'static,
    {
        let (result_tx, result_rx) = oneshot::channel();
        let attempt = {
            let mut state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            if state.draining {
                None
            } else {
                state.records.get_mut(&operation_id).and_then(|record| {
                    if record.owned || !matches!(record.status, RecordStatus::Active) {
                        None
                    } else {
                        record.owned = true;
                        Some(record.attempt)
                    }
                })
            }
        };
        let Some(attempt) = attempt else {
            drop(future);
            let _ = result_tx.send(None);
            return result_rx;
        };
        let guard = OwnedOperationTaskGuard {
            ledger: self.clone(),
            operation_id,
            attempt,
        };
        tokio::spawn(async move {
            let result = future.await;
            drop(guard);
            let _ = result_tx.send(Some(result));
        });
        result_rx
    }

    pub(crate) fn begin_drain(&self) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.draining = true;
        for record in state.records.values_mut().filter(|record| record.owned) {
            record.cancellation_requested = true;
        }
    }

    pub(crate) async fn wait_until_owned_idle(&self) {
        loop {
            let notified = self.inner.owned_idle.notified();
            let idle = !self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .records
                .values()
                .any(|record| record.owned);
            if idle {
                return;
            }
            notified.await;
        }
    }

    #[cfg(test)]
    pub(crate) fn active_owned_ids(&self) -> Vec<String> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .iter()
            .filter(|(_, record)| record.owned)
            .map(|(operation_id, _)| operation_id.clone())
            .collect()
    }

    pub(crate) fn begin<P: Serialize>(
        &self,
        method: &str,
        context: &EIPCallContext,
        params: &P,
    ) -> Result<BeginOutcome, LedgerError> {
        self.begin_with_drain_snapshot(method, context, params)
            .map(|(outcome, _)| outcome)
    }

    pub(crate) fn begin_readiness<P: Serialize>(
        &self,
        context: &EIPCallContext,
        params: &P,
    ) -> Result<(BeginOutcome, bool), LedgerError> {
        self.begin_with_drain_snapshot("environment.readiness", context, params)
    }

    fn begin_with_drain_snapshot<P: Serialize>(
        &self,
        method: &str,
        context: &EIPCallContext,
        params: &P,
    ) -> Result<(BeginOutcome, bool), LedgerError> {
        let replay_class = replay_class(method)?;
        let reconciliation = is_reconciliation_method(method);
        let request_digest = canonical_request_digest(method, params)?;
        let now = Instant::now();
        let deadline = operation_deadline(context.timeout_ms, now, self.inner.max_duration)?;
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.prune(now, self.inner.terminal_ttl);
        if let Some(record) = state.records.get_mut(&context.operation_id) {
            record.last_access = now;
            let outcome = if record.method != method || record.request_digest != request_digest {
                Err(LedgerError::Collision)
            } else {
                match &record.status {
                    RecordStatus::Active | RecordStatus::Completing => Err(LedgerError::InProgress),
                    RecordStatus::Terminal { .. } => {
                        if record.replay_class != ReplayClass::TerminalEvidence {
                            Err(LedgerError::TerminalFailure)
                        } else if let Some(result) = &record.result {
                            Ok(BeginOutcome::Replay(result.clone()))
                        } else if let Some(failure) = &record.failure {
                            Ok(BeginOutcome::ReplayFailure(Box::new(failure.clone())))
                        } else {
                            Err(LedgerError::TerminalFailure)
                        }
                    }
                }
            };
            let ready = !state.draining;
            let admitted = outcome.is_ok() && state.mark_pending_admitted(&context.operation_id);
            drop(state);
            if admitted {
                self.inner.pending_changed.notify_waiters();
            }
            return outcome.map(|outcome| (outcome, ready));
        }
        let total_capacity = self
            .inner
            .ordinary_record_capacity
            .checked_add(RECONCILIATION_RECORD_RESERVE)
            .ok_or(LedgerError::Capacity)?;
        if !reconciliation {
            while state
                .records
                .values()
                .filter(|record| !record.reconciliation)
                .count()
                >= self.inner.ordinary_record_capacity
            {
                if !state.reclaim_oldest_terminal(false) {
                    return Err(LedgerError::Capacity);
                }
            }
        }
        while state.records.len() >= total_capacity {
            let borrowed_reconciliation = !reconciliation
                && state
                    .records
                    .values()
                    .filter(|record| record.reconciliation)
                    .count()
                    > RECONCILIATION_RECORD_RESERVE;
            let reclaimed = borrowed_reconciliation && state.reclaim_oldest_terminal(true)
                || state.reclaim_oldest_terminal(reconciliation);
            if !reclaimed {
                return Err(LedgerError::Capacity);
            }
        }
        let capacity = if reconciliation {
            // Per-Session reconciliation reserve remains available under global pressure.
            None
        } else {
            loop {
                if let Ok(permit) = self.inner.device_capacity.clone().try_acquire_owned() {
                    break Some(permit);
                }
                if !state.reclaim_oldest_terminal(false) {
                    return Err(LedgerError::Capacity);
                }
            }
        };
        state.next_attempt = state
            .next_attempt
            .checked_add(1)
            .ok_or(LedgerError::Capacity)?;
        let attempt = state.next_attempt;
        state.records.insert(
            context.operation_id.clone(),
            OperationRecord {
                _capacity: capacity,
                last_access: now,
                method: method.to_owned(),
                request_digest,
                replay_class,
                reconciliation,
                attempt,
                carrier_attempt: CARRIER_ATTEMPT.try_with(|attempt| *attempt).ok(),
                status: RecordStatus::Active,
                cancellation_requested: false,
                deadline,
                receipt: None,
                result: None,
                failure: None,
                pins: BTreeSet::new(),
                owned: false,
            },
        );
        let ready = !state.draining;
        let admitted = state.mark_pending_admitted(&context.operation_id);
        drop(state);
        if admitted {
            self.inner.pending_changed.notify_waiters();
        }
        Ok((
            BeginOutcome::New(OperationLease {
                ledger: self.clone(),
                operation_id: context.operation_id.clone(),
                replay_class,
                failure_on_drop: None,
                finished: false,
            }),
            ready,
        ))
    }

    pub(crate) fn cancel(&self, target_operation_id: &str) -> OperationCancelStatus {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let Some(record) = state.records.get_mut(target_operation_id) else {
            return OperationCancelStatus::NotFound;
        };
        match record.status {
            RecordStatus::Completing | RecordStatus::Terminal { .. } => {
                OperationCancelStatus::AlreadyTerminal
            }
            RecordStatus::Active => {
                record.cancellation_requested = true;
                OperationCancelStatus::CancellationRequested
            }
        }
    }

    pub(crate) fn interruption(&self, operation_id: &str) -> Option<OperationInterruption> {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let record = state.records.get(operation_id)?;
        if record.cancellation_requested {
            Some(OperationInterruption::Cancelled)
        } else if matches!(record.status, RecordStatus::Active) && Instant::now() >= record.deadline
        {
            Some(OperationInterruption::TimedOut)
        } else {
            None
        }
    }

    pub(crate) fn cancellation_requested(&self, operation_id: &str) -> bool {
        self.interruption(operation_id) == Some(OperationInterruption::Cancelled)
    }

    pub(crate) fn receipt_by_operation(&self, operation_id: &str) -> Option<OperationReceipt> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .get_mut(operation_id)
            .and_then(|record| {
                record.last_access = Instant::now();
                record.receipt.clone()
            })
    }

    pub(crate) fn failure_by_operation(&self, operation_id: &str) -> Option<EIPError> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .get(operation_id)
            .and_then(|record| record.failure.clone())
    }

    pub(crate) fn finish_dispatched_failure(
        &self,
        method: &str,
        params: &serde_json::Value,
        failure: EIPError,
    ) {
        let Some(operation_id) = params
            .as_object()
            .and_then(|params| params.get("context"))
            .and_then(serde_json::Value::as_object)
            .and_then(|context| context.get("operation_id"))
            .and_then(serde_json::Value::as_str)
        else {
            return;
        };
        let Ok(request_digest) = canonical_request_digest(method, params) else {
            return;
        };
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let mut pins = selector_pins_from_failure(method, &failure);
        let published = if let Some(record) = state.records.get_mut(operation_id) {
            if record.method == method
                && record.request_digest == request_digest
                && matches!(record.status, RecordStatus::Completing)
                && record.result.is_none()
                && record.failure.is_none()
            {
                if record.replay_class == ReplayClass::TerminalEvidence {
                    record.status = RecordStatus::Terminal {
                        completed_at: Instant::now(),
                    };
                    record.receipt = failure.data.receipt.clone();
                    record.failure = Some(failure);
                    record.pins = std::mem::take(&mut pins);
                    true
                } else {
                    false
                }
            } else {
                false
            }
        } else {
            false
        };
        if published {
            state.terminal_order.push_back(operation_id.to_owned());
        }
    }

    pub(crate) fn active_response_handoff<P: Serialize>(
        &self,
        method: &str,
        params: &P,
    ) -> Option<ActiveResponseHandoff> {
        let params_value = serde_json::to_value(params).ok()?;
        let operation_id = operation_id_from_params(&params_value)?;
        let request_digest = canonical_request_digest(method, params).ok()?;
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let record = state.records.get(operation_id)?;
        if record.method != method
            || record.request_digest != request_digest
            || record.replay_class != ReplayClass::ActiveOnly
            || record.carrier_attempt != CARRIER_ATTEMPT.try_with(|attempt| *attempt).ok()
            || !matches!(record.status, RecordStatus::Completing)
        {
            return None;
        }
        Some(ActiveResponseHandoff {
            ledger: self.clone(),
            operation_id: operation_id.to_owned(),
            attempt: record.attempt,
            completed: false,
        })
    }

    pub(crate) fn collect(&self, pressure: bool) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.prune(Instant::now(), self.inner.terminal_ttl);
        if pressure {
            while state.reclaim_oldest_terminal(false) {}
            while state.reclaim_oldest_terminal(true) {}
        }
    }

    pub(crate) fn under_pressure(&self) -> bool {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.records.len() >= self.inner.ordinary_record_capacity
            || self.inner.device_capacity.available_permits() == 0
    }

    pub(crate) fn release_selector(&self, kind: &str, value: &str) {
        let selector = format!("{kind}:{value}");
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        for record in state.records.values_mut() {
            record.pins.remove(&selector);
        }
    }

    fn complete_active_handoff(&self, operation_id: &str, attempt: u64) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let should_remove = state.records.get(operation_id).is_some_and(|record| {
            record.attempt == attempt
                && record.replay_class == ReplayClass::ActiveOnly
                && matches!(record.status, RecordStatus::Completing)
        });
        if should_remove {
            state.remove_record(operation_id);
        }
    }

    #[cfg(test)]
    pub(crate) fn complete_active_for_test(&self, operation_id: &str) {
        let attempt = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .get(operation_id)
            .map(|record| record.attempt);
        if let Some(attempt) = attempt {
            self.complete_active_handoff(operation_id, attempt);
        }
    }

    fn release_pending(&self, operation_id: &str) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if let Some(pending) = state.pending.get_mut(operation_id) {
            pending.requests = pending
                .requests
                .checked_sub(1)
                .expect("pending request guard released exactly once");
            if pending.requests == 0 {
                state.pending.remove(operation_id);
            }
        }
        drop(state);
        self.inner.pending_changed.notify_waiters();
    }

    fn complete_owned(&self, operation_id: &str, attempt: u64) {
        let idle = {
            let mut state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            if let Some(record) = state.records.get_mut(operation_id)
                && record.attempt == attempt
            {
                record.owned = false;
            }
            !state.records.values().any(|record| record.owned)
        };
        if idle {
            self.inner.owned_idle.notify_waiters();
        }
    }

    #[cfg(test)]
    pub(crate) fn record_stats(&self) -> (usize, usize, usize) {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let active = state
            .records
            .values()
            .filter(|record| {
                matches!(
                    record.status,
                    RecordStatus::Active | RecordStatus::Completing
                )
            })
            .count();
        let identifier_bytes = state.records.keys().map(String::len).sum();
        (active, state.records.len(), identifier_bytes)
    }
}

impl OperationLease {
    pub(crate) fn request_digest(&self) -> String {
        let state = self
            .ledger
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state
            .records
            .get(&self.operation_id)
            .expect("operation lease owns a live record")
            .request_digest
            .clone()
    }

    pub(crate) fn receipt(
        &self,
        method: &str,
        stage: ReceiptStage,
        outcome: Option<ReceiptOutcome>,
    ) -> Result<OperationReceipt, LedgerError> {
        Ok(OperationReceipt {
            operation_id: self.operation_id.clone(),
            method: method.to_owned(),
            device_id: self.ledger.inner.device_id.clone(),
            session_id: self.ledger.inner.session_id.clone(),
            generation: self.ledger.inner.generation,
            request_digest: self.request_digest(),
            stage,
            outcome,
            observed_at: chrono::Utc::now(),
        })
    }

    pub(crate) fn preserve_failure_on_drop(
        &mut self,
        receipt: OperationReceipt,
        failure: EIPError,
    ) {
        self.failure_on_drop = Some(Box::new((receipt, failure)));
    }

    pub(crate) fn finish<T: Serialize>(
        mut self,
        result: &T,
        receipt: Option<OperationReceipt>,
    ) -> Result<(), LedgerError> {
        if self.replay_class == ReplayClass::ActiveOnly {
            self.finish_active();
            return Ok(());
        }
        let result = serde_json::to_value(result).map_err(|_| LedgerError::Encoding)?;
        self.finish_value(Some(result), None, receipt);
        Ok(())
    }

    pub(crate) fn finish_failure(mut self, receipt: OperationReceipt, failure: EIPError) {
        if self.replay_class == ReplayClass::ActiveOnly {
            self.finish_active();
        } else {
            self.finish_value(None, Some(failure), Some(receipt));
        }
    }

    fn finish_active(&mut self) {
        let mut state = self
            .ledger
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if let Some(record) = state.records.get_mut(&self.operation_id) {
            record.status = RecordStatus::Completing;
            record.receipt = None;
            record.result = None;
            record.failure = None;
        }
        self.finished = true;
    }

    fn finish_value(
        &mut self,
        result: Option<serde_json::Value>,
        failure: Option<EIPError>,
        receipt: Option<OperationReceipt>,
    ) {
        let now = Instant::now();
        let mut state = self
            .ledger
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let method = state
            .records
            .get(&self.operation_id)
            .map(|record| record.method.clone());
        let pins = method.as_deref().map_or_else(BTreeSet::new, |method| {
            selector_pins(method, result.as_ref(), failure.as_ref())
        });
        if let Some(record) = state.records.get_mut(&self.operation_id) {
            record.status = RecordStatus::Terminal { completed_at: now };
            record.last_access = now;
            record.result = result;
            record.failure = failure;
            record.receipt = receipt;
            record.pins = pins;
            state.terminal_order.push_back(self.operation_id.clone());
        }
        self.finished = true;
    }
}

impl Drop for PendingOperationGuard {
    fn drop(&mut self) {
        self.ledger.release_pending(&self.operation_id);
    }
}

impl Drop for OwnedOperationTaskGuard {
    fn drop(&mut self) {
        self.ledger.complete_owned(&self.operation_id, self.attempt);
    }
}

impl ActiveResponseHandoff {
    pub(crate) fn complete(mut self) {
        self.ledger
            .complete_active_handoff(&self.operation_id, self.attempt);
        self.completed = true;
    }
}

impl Drop for ActiveResponseHandoff {
    fn drop(&mut self) {
        if !self.completed {
            self.ledger
                .complete_active_handoff(&self.operation_id, self.attempt);
            self.completed = true;
        }
    }
}

impl Drop for OperationLease {
    fn drop(&mut self) {
        if self.finished {
            return;
        }
        if let Some(failure) = self.failure_on_drop.take() {
            let (receipt, failure) = *failure;
            self.finish_value(None, Some(failure), Some(receipt));
            return;
        }
        let mut state = self
            .ledger
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if let Some(record) = state.records.get_mut(&self.operation_id)
            && matches!(record.status, RecordStatus::Active)
        {
            record.status = RecordStatus::Completing;
        }
        self.finished = true;
    }
}

impl LedgerState {
    fn mark_pending_admitted(&mut self, operation_id: &str) -> bool {
        match self.pending.get_mut(operation_id) {
            Some(pending) if !pending.admitted => {
                pending.admitted = true;
                true
            }
            _ => false,
        }
    }

    fn prune(&mut self, now: Instant, ttl: Duration) {
        let mut retained = VecDeque::with_capacity(self.terminal_order.len());
        while let Some(operation_id) = self.terminal_order.pop_front() {
            let expired = self.records.get(&operation_id).is_some_and(|record| {
                !record.owned
                    && record.pins.is_empty()
                    && matches!(
                        record.status,
                        RecordStatus::Terminal { completed_at }
                            if now.duration_since(record.last_access.max(completed_at)) >= ttl
                    )
            });
            if expired {
                self.remove_record(&operation_id);
            } else if self.records.contains_key(&operation_id) {
                retained.push_back(operation_id);
            }
        }
        self.terminal_order = retained;
    }

    fn reclaim_oldest_terminal(&mut self, reconciliation: bool) -> bool {
        let oldest = self
            .records
            .iter()
            .filter(|(_, record)| {
                record.reconciliation == reconciliation
                    && !record.owned
                    && record.pins.is_empty()
                    && matches!(record.status, RecordStatus::Terminal { .. })
            })
            .min_by_key(|(_, record)| record.last_access)
            .map(|(id, _)| id.clone());
        if let Some(id) = oldest {
            self.remove_record(&id);
            self.terminal_order.retain(|retained| retained != &id);
            true
        } else {
            false
        }
    }

    fn remove_record(&mut self, operation_id: &str) -> Option<OperationRecord> {
        self.records.remove(operation_id)
    }
}

fn replay_class(method: &str) -> Result<ReplayClass, LedgerError> {
    let metadata = crate::eip::METHODS
        .iter()
        .find(|metadata| metadata.name == method)
        .ok_or(LedgerError::Encoding)?;
    match metadata.replay_class {
        "active_only" => Ok(ReplayClass::ActiveOnly),
        "terminal_evidence" => Ok(ReplayClass::TerminalEvidence),
        _ => Err(LedgerError::Encoding),
    }
}

fn is_reconciliation_method(method: &str) -> bool {
    matches!(
        method,
        "operation.cancel"
            | "receipt.get"
            | "process.inspect"
            | "process.kill"
            | "process.release"
            | "output.read"
            | "output.release"
    )
}

fn operation_id_from_params(params: &serde_json::Value) -> Option<&str> {
    params
        .as_object()?
        .get("context")?
        .as_object()?
        .get("operation_id")?
        .as_str()
}

fn selector_pins(
    method: &str,
    result: Option<&serde_json::Value>,
    failure: Option<&EIPError>,
) -> BTreeSet<String> {
    if !matches!(method, "process.start" | "shell.exec") {
        return BTreeSet::new();
    }
    let mut pins = BTreeSet::new();
    if let Some(result) = result {
        collect_command_pins(method, result, &mut pins);
    }
    if let Some(failure) = failure
        && let Ok(value) = serde_json::to_value(failure)
    {
        let data = &value["data"];
        if let Some(process) = data.get("process") {
            collect_process_pins(process, &mut pins);
        }
        if let Some(output) = data.get("output") {
            collect_output_pins(output, &mut pins);
        }
    }
    pins
}

fn selector_pins_from_failure(method: &str, failure: &EIPError) -> BTreeSet<String> {
    selector_pins(method, None, Some(failure))
}

fn collect_command_pins(method: &str, value: &serde_json::Value, pins: &mut BTreeSet<String>) {
    if method == "process.start" {
        collect_process_pins(&value["process"], pins);
    } else {
        collect_output_pins(&value["output"], pins);
    }
}

fn collect_process_pins(value: &serde_json::Value, pins: &mut BTreeSet<String>) {
    if let Some(handle) = value.get("handle").and_then(serde_json::Value::as_str) {
        pins.insert(format!("process:{handle}"));
    }
    if let Some(output) = value.get("output") {
        collect_output_pins(output, pins);
    }
}

fn collect_output_pins(value: &serde_json::Value, pins: &mut BTreeSet<String>) {
    for stream in ["stdout", "stderr"] {
        if let Some(reference) = value
            .get(stream)
            .and_then(|stream| stream.get("reference"))
            .and_then(serde_json::Value::as_str)
        {
            pins.insert(format!("output:{reference}"));
        }
    }
}

fn operation_deadline(
    requested_ms: Option<u64>,
    now: Instant,
    max_duration: Duration,
) -> Result<Instant, LedgerError> {
    let hard = now + max_duration;
    let Some(requested_ms) = requested_ms else {
        return Ok(hard);
    };
    let requested = Duration::from_millis(requested_ms);
    if requested.is_zero() {
        return Err(LedgerError::DeadlineExpired);
    }
    Ok(hard.min(now + requested))
}

pub(crate) fn canonical_request_digest<P: Serialize>(
    method: &str,
    params: &P,
) -> Result<String, LedgerError> {
    let mut value = serde_json::to_value(params).map_err(|_| LedgerError::Encoding)?;
    let object = value.as_object_mut().ok_or(LedgerError::Encoding)?;
    if let Some(context) = object
        .get_mut("context")
        .and_then(serde_json::Value::as_object_mut)
    {
        context.remove("operation_id");
        context.remove("timeout_ms");
    }
    let canonical = serde_json::to_vec(&value).map_err(|_| LedgerError::Encoding)?;
    let mut hasher = Sha256::new();
    hasher.update(crate::eip::EIP_PROTOCOL_VERSION.as_bytes());
    hasher.update([0]);
    hasher.update(method.as_bytes());
    hasher.update([0]);
    hasher.update(canonical);
    Ok(format!("{:x}", hasher.finalize()))
}

impl ShortIdAllocator {
    pub(crate) fn for_generation(generation: u64) -> Self {
        Self {
            namespace: encode_base36(generation).into(),
            counters: Arc::new(Mutex::new(BTreeMap::new())),
        }
    }

    pub(crate) fn next(&self, prefix: &'static str) -> Result<String, LedgerError> {
        let mut counters = self.counters.lock().unwrap_or_else(PoisonError::into_inner);
        let counter = counters.entry(prefix).or_default();
        *counter = counter.checked_add(1).ok_or(LedgerError::Encoding)?;
        Ok(format!("{prefix}-{}-{counter}", self.namespace))
    }
}

fn encode_base36(mut value: u64) -> String {
    const DIGITS: &[u8; 36] = b"0123456789abcdefghijklmnopqrstuvwxyz";
    let mut buffer = [0_u8; 13];
    let mut cursor = buffer.len();
    loop {
        cursor -= 1;
        buffer[cursor] = DIGITS[(value % 36) as usize];
        value /= 36;
        if value == 0 {
            break;
        }
    }
    String::from_utf8(buffer[cursor..].to_vec()).expect("base36 digits are UTF-8")
}

#[cfg(test)]
pub(crate) fn random_selector(prefix: &str) -> Result<String, LedgerError> {
    let mut bytes = [0_u8; 18];
    getrandom::fill(&mut bytes).map_err(|_| LedgerError::Encoding)?;
    use base64::Engine as _;
    Ok(format!(
        "{prefix}-{}",
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    ))
}

#[cfg(test)]
mod tests {
    use crate::eip::{EIPCallContext, EIPError, ErrorType};

    use super::{
        BeginOutcome, LedgerError, OperationInterruption, OperationLedger, ShortIdAllocator,
        canonical_request_digest,
    };

    #[test]
    fn short_ids_are_kind_prefixed_and_concurrently_unique() {
        let ids = ShortIdAllocator::for_generation(42);
        assert_eq!(ids.next("reader").expect("reader ID"), "reader-16-1");
        assert_eq!(ids.next("writer").expect("writer ID"), "writer-16-1");

        let workers = (0..8)
            .map(|_| {
                let ids = ids.clone();
                std::thread::spawn(move || {
                    (0..64)
                        .map(|_| ids.next("receipt").expect("receipt ID"))
                        .collect::<Vec<_>>()
                })
            })
            .collect::<Vec<_>>();
        let mut generated = workers
            .into_iter()
            .flat_map(|worker| worker.join().expect("worker succeeds"))
            .collect::<Vec<_>>();
        generated.sort();
        generated.dedup();
        assert_eq!(generated.len(), 512);
        assert!(generated.iter().all(|selector| selector.len() <= 32));
    }

    #[test]
    fn canonical_digest_omits_context_correlation_fields() {
        let first = serde_json::json!({
            "context": {"operation_id": "one", "timeout_ms": 1_000},
            "path": {"path": "/file"}
        });
        let second = serde_json::json!({
            "path": {"path": "/file"},
            "context": {"operation_id": "two"}
        });
        assert_eq!(
            canonical_request_digest("file.stat", &first).expect("digest"),
            canonical_request_digest("file.stat", &second).expect("digest")
        );
    }

    #[test]
    fn ledger_replays_only_the_same_operation_identity_and_request() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "one"},
            "value": 1
        });
        let context = EIPCallContext {
            operation_id: "one".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = ledger
            .begin("file.write_text", &context, &params)
            .expect("accepted")
        else {
            panic!("new operation expected")
        };
        lease
            .finish(&serde_json::json!({"ok": true}), None)
            .expect("finishes");

        assert!(matches!(
            ledger
                .begin("file.write_text", &context, &params)
                .expect("same operation replays"),
            BeginOutcome::Replay(_)
        ));
        let mismatched = serde_json::json!({
            "context": {"operation_id": "one"},
            "value": 2
        });
        assert!(matches!(
            ledger.begin("file.write_text", &context, &mismatched),
            Err(LedgerError::Collision)
        ));
        assert!(matches!(
            ledger.begin("file.mkdir", &context, &params),
            Err(LedgerError::Collision)
        ));
    }

    #[test]
    fn dispatched_failure_is_published_atomically_after_lease_completion() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "failed"},
            "path": {"path": "/missing"}
        });
        let context = EIPCallContext {
            operation_id: "failed".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = ledger
            .begin("file.write_text", &context, &params)
            .expect("operation begins")
        else {
            panic!("new operation expected")
        };
        drop(lease);

        assert!(matches!(
            ledger.begin("file.write_text", &context, &params),
            Err(LedgerError::InProgress)
        ));

        let failure = serde_json::from_value::<EIPError>(serde_json::json!({
            "code": -32011,
            "message": "missing",
            "data": {
                "error_type": "not_found_or_denied",
                "retry_hint": "never",
                "dispatch_stage": "completed"
            }
        }))
        .expect("typed failure");
        ledger.finish_dispatched_failure("file.write_text", &params, failure);

        let BeginOutcome::ReplayFailure(replayed) = ledger
            .begin("file.write_text", &context, &params)
            .expect("failure replays")
        else {
            panic!("failure replay expected")
        };
        assert_eq!(replayed.data.error_type, ErrorType::NotFoundOrDenied);
        assert_eq!(replayed.message, "missing");
    }

    #[test]
    fn active_only_records_live_until_the_exact_response_handoff() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "page"},
            "reference": "output-1",
            "start_offset": 0
        });
        let context = EIPCallContext {
            operation_id: "page".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = ledger
            .begin("output.read", &context, &params)
            .expect("active-only operation begins")
        else {
            panic!("new operation expected")
        };
        lease
            .finish(&serde_json::json!({"next_offset": 0}), None)
            .expect("active-only operation completes handler work");
        assert!(matches!(
            ledger.begin("output.read", &context, &params),
            Err(LedgerError::InProgress)
        ));

        let first = ledger
            .active_response_handoff("output.read", &params)
            .expect("handoff token exists");
        let stale = ledger
            .active_response_handoff("output.read", &params)
            .expect("duplicate waiter observes the same admission attempt");
        first.complete();

        let BeginOutcome::New(second) = ledger
            .begin("output.read", &context, &params)
            .expect("operation ID is reusable after handoff")
        else {
            panic!("new operation expected")
        };
        second
            .finish(&serde_json::json!({"next_offset": 0}), None)
            .expect("second attempt completes handler work");
        drop(stale);
        assert!(matches!(
            ledger.begin("output.read", &context, &params),
            Err(LedgerError::InProgress)
        ));
        ledger
            .active_response_handoff("output.read", &params)
            .expect("second handoff token exists")
            .complete();
        assert_eq!(ledger.record_stats().1, 0);
    }

    #[test]
    fn reconciliation_requests_share_unused_ordinary_capacity_and_one_reserve() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            3,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let mut leases = Vec::new();
        for index in 0..4 {
            let operation_id = format!("inspect-{index}");
            let params = serde_json::json!({
                "context": {"operation_id": operation_id},
                "handle": format!("process-{index}")
            });
            let context = EIPCallContext {
                operation_id,
                timeout_ms: None,
            };
            let BeginOutcome::New(lease) = ledger
                .begin("process.inspect", &context, &params)
                .expect("unused ordinary capacity and the reserve admit reconciliation")
            else {
                panic!("new operation expected")
            };
            leases.push(lease);
        }

        let overflow_params = serde_json::json!({
            "context": {"operation_id": "inspect-overflow"},
            "handle": "process-overflow"
        });
        let overflow_context = EIPCallContext {
            operation_id: "inspect-overflow".to_owned(),
            timeout_ms: None,
        };
        assert!(matches!(
            ledger.begin("process.inspect", &overflow_context, &overflow_params),
            Err(LedgerError::Capacity)
        ));
        assert_eq!(ledger.record_stats().1, 4);
        drop(leases);
    }

    #[test]
    fn ordinary_admission_reclaims_completed_reconciliation_borrowers() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        for index in 0..3 {
            let operation_id = format!("release-{index}");
            let params = serde_json::json!({
                "context": {"operation_id": operation_id},
                "reference": format!("output-{index}")
            });
            let context = EIPCallContext {
                operation_id,
                timeout_ms: None,
            };
            let BeginOutcome::New(release) = ledger
                .begin("output.release", &context, &params)
                .expect("reconciliation operation uses shared capacity")
            else {
                panic!("new operation expected")
            };
            release
                .finish(&serde_json::json!({"released": true}), None)
                .expect("reconciliation operation completes");
        }

        let ordinary_params = serde_json::json!({
            "context": {"operation_id": "write"},
            "path": {"path": "/file"},
            "text": "value"
        });
        let ordinary_context = EIPCallContext {
            operation_id: "write".to_owned(),
            timeout_ms: None,
        };
        assert!(matches!(
            ledger
                .begin("file.write_text", &ordinary_context, &ordinary_params)
                .expect("ordinary admission reclaims a completed reconciliation borrower"),
            BeginOutcome::New(_)
        ));
        assert_eq!(ledger.record_stats().1, 3);
    }

    #[test]
    fn next_reconciliation_request_overlaps_response_handoff_without_capacity_failure() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            1,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let first_params = serde_json::json!({
            "context": {"operation_id": "first-page"},
            "reference": "output-1",
            "start_offset": 0
        });
        let first_context = EIPCallContext {
            operation_id: "first-page".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(first) = ledger
            .begin("output.read", &first_context, &first_params)
            .expect("first reconciliation operation begins")
        else {
            panic!("new operation expected")
        };
        first
            .finish(&serde_json::json!({"next_offset": 1}), None)
            .expect("first reconciliation operation completes handler work");
        let first_handoff = ledger
            .active_response_handoff("output.read", &first_params)
            .expect("first response has a handoff");

        let second_params = serde_json::json!({
            "context": {"operation_id": "second-page"},
            "reference": "output-1",
            "start_offset": 1
        });
        let second_context = EIPCallContext {
            operation_id: "second-page".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(second) = ledger
            .begin("output.read", &second_context, &second_params)
            .expect("next sequential reconciliation request uses the handoff overlap")
        else {
            panic!("new operation expected")
        };

        let third_params = serde_json::json!({
            "context": {"operation_id": "third-page"},
            "reference": "output-1",
            "start_offset": 2
        });
        let third_context = EIPCallContext {
            operation_id: "third-page".to_owned(),
            timeout_ms: None,
        };
        assert!(matches!(
            ledger.begin("output.read", &third_context, &third_params),
            Err(LedgerError::Capacity)
        ));

        first_handoff.complete();
        second
            .finish(&serde_json::json!({"next_offset": 2}), None)
            .expect("second reconciliation operation completes handler work");
        ledger
            .active_response_handoff("output.read", &second_params)
            .expect("second response has a handoff")
            .complete();
        assert_eq!(ledger.record_stats().1, 0);
    }

    #[test]
    fn session_resource_creation_stays_active_until_response_handoff() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "open-reader"},
            "path": {"path": "/file"}
        });
        let context = EIPCallContext {
            operation_id: "open-reader".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = ledger
            .begin("file.open_reader", &context, &params)
            .expect("session resource operation begins")
        else {
            panic!("new operation expected")
        };
        lease
            .finish(&serde_json::json!({"reader": "reader-1"}), None)
            .expect("session resource handler completes");
        assert!(matches!(
            ledger.begin("file.open_reader", &context, &params),
            Err(LedgerError::InProgress)
        ));
        ledger
            .active_response_handoff("file.open_reader", &params)
            .expect("session resource response has a handoff")
            .complete();
        assert_eq!(ledger.record_stats().1, 0);
    }

    #[test]
    fn pinned_command_origin_cannot_consume_the_reconciliation_reserve() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            1,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let start_params = serde_json::json!({
            "context": {"operation_id": "start"},
            "request": {"command": {"kind": "argv"}}
        });
        let start_context = EIPCallContext {
            operation_id: "start".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(start) = ledger
            .begin("process.start", &start_context, &start_params)
            .expect("command origin begins")
        else {
            panic!("new operation expected")
        };
        start
            .finish(
                &serde_json::json!({
                    "process": {
                        "handle": "process-1",
                        "output": {
                            "stdout": {"reference": "output-1"},
                            "stderr": {"reference": "output-2"}
                        }
                    }
                }),
                None,
            )
            .expect("command origin publishes");

        let ordinary_params = serde_json::json!({
            "context": {"operation_id": "ordinary"},
            "path": {"path": "/file"},
            "text": "value"
        });
        let ordinary_context = EIPCallContext {
            operation_id: "ordinary".to_owned(),
            timeout_ms: None,
        };
        assert!(matches!(
            ledger.begin("file.write_text", &ordinary_context, &ordinary_params),
            Err(LedgerError::Capacity)
        ));

        let reconcile_params = serde_json::json!({
            "context": {"operation_id": "inspect"},
            "handle": "process-1"
        });
        let reconcile_context = EIPCallContext {
            operation_id: "inspect".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(inspect) = ledger
            .begin("process.inspect", &reconcile_context, &reconcile_params)
            .expect("reconciliation reserve remains available")
        else {
            panic!("new reconciliation operation expected")
        };
        inspect
            .finish(&serde_json::json!({"process": {}}), None)
            .expect("reconciliation handler completes");
        ledger
            .active_response_handoff("process.inspect", &reconcile_params)
            .expect("reconciliation response has a handoff")
            .complete();

        ledger.release_selector("process", "process-1");
        ledger.release_selector("output", "output-1");
        ledger.release_selector("output", "output-2");
        assert!(matches!(
            ledger
                .begin("file.write_text", &ordinary_context, &ordinary_params)
                .expect("unpinned origin becomes reclaimable"),
            BeginOutcome::New(_)
        ));
    }

    #[test]
    fn lost_cancel_response_keeps_target_cancellation_for_both_replay_classes() {
        for (target_method, target_id) in [
            ("environment.describe", "active-target"),
            ("file.write_text", "terminal-target"),
        ] {
            let ledger = OperationLedger::new(
                "env".to_owned(),
                7,
                "session-test".to_owned(),
                4,
                std::time::Duration::from_secs(60),
                std::time::Duration::from_secs(60),
                std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
            );
            let target_context = EIPCallContext {
                operation_id: target_id.to_owned(),
                timeout_ms: None,
            };
            let target_params = serde_json::json!({
                "context": {"operation_id": target_id},
                "value": 1
            });
            let BeginOutcome::New(_target) = ledger
                .begin(target_method, &target_context, &target_params)
                .expect("target begins")
            else {
                panic!("new target expected")
            };

            let cancel_context = EIPCallContext {
                operation_id: "cancel-request".to_owned(),
                timeout_ms: None,
            };
            let cancel_params = serde_json::json!({
                "context": {"operation_id": "cancel-request"},
                "target_operation_id": target_id
            });
            let BeginOutcome::New(cancel) = ledger
                .begin("operation.cancel", &cancel_context, &cancel_params)
                .expect("cancel begins")
            else {
                panic!("new cancel expected")
            };
            assert_eq!(
                ledger.cancel(target_id),
                crate::eip::OperationCancelStatus::CancellationRequested
            );
            cancel
                .finish(
                    &serde_json::json!({"status": "cancellation_requested"}),
                    None,
                )
                .expect("cancel handler finishes");
            drop(
                ledger
                    .active_response_handoff("operation.cancel", &cancel_params)
                    .expect("cancel response has handoff"),
            );

            assert_eq!(
                ledger.interruption(target_id),
                Some(OperationInterruption::Cancelled)
            );
            assert!(matches!(
                ledger
                    .begin("operation.cancel", &cancel_context, &cancel_params)
                    .expect("lost cancel response frees active-only identity"),
                BeginOutcome::New(_)
            ));
        }
    }

    #[tokio::test]
    async fn terminal_record_is_not_reclaimed_before_owned_native_completion() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            1,
            std::time::Duration::from_millis(1),
            std::time::Duration::from_secs(60),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let context = EIPCallContext {
            operation_id: "owned".to_owned(),
            timeout_ms: None,
        };
        let params = serde_json::json!({
            "context": {"operation_id": "owned"},
            "value": 1
        });
        let BeginOutcome::New(operation) = ledger
            .begin("file.write_text", &context, &params)
            .expect("owned mutation begins")
        else {
            panic!("new operation expected")
        };
        let (terminal_tx, terminal_rx) = tokio::sync::oneshot::channel();
        let (release_tx, release_rx) = tokio::sync::oneshot::channel();
        let owned = ledger.spawn_owned("owned".to_owned(), async move {
            operation
                .finish(&serde_json::json!({"completed": true}), None)
                .expect("terminal evidence publishes");
            let _ = terminal_tx.send(());
            let _ = release_rx.await;
            Ok(())
        });
        terminal_rx.await.expect("terminal evidence is visible");
        std::thread::sleep(std::time::Duration::from_millis(5));

        let next_context = EIPCallContext {
            operation_id: "next".to_owned(),
            timeout_ms: None,
        };
        let next_params = serde_json::json!({
            "context": {"operation_id": "next"},
            "value": 2
        });
        assert!(matches!(
            ledger.begin("file.write_text", &next_context, &next_params),
            Err(LedgerError::Capacity)
        ));

        release_tx.send(()).expect("release owned mutation");
        assert!(matches!(owned.await, Ok(Some(Ok(())))));
        ledger.wait_until_owned_idle().await;
        assert!(matches!(
            ledger
                .begin("file.write_text", &next_context, &next_params)
                .expect("completed unpinned evidence becomes reclaimable"),
            BeginOutcome::New(_)
        ));
    }

    #[test]
    fn operation_deadlines_are_finite_and_observable_by_workers() {
        let ledger = OperationLedger::new(
            "env".to_owned(),
            7,
            "session-test".to_owned(),
            4,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_millis(1),
            std::sync::Arc::new(tokio::sync::Semaphore::new(4096)),
        );
        let context = EIPCallContext {
            operation_id: "timed".to_owned(),
            timeout_ms: None,
        };
        let params = serde_json::json!({"context": {"operation_id": "timed"}});
        let _lease = match ledger
            .begin("environment.describe", &context, &params)
            .expect("operation begins")
        {
            BeginOutcome::New(lease) => lease,
            BeginOutcome::Replay(_) | BeginOutcome::ReplayFailure(_) => {
                panic!("new operation expected")
            }
        };
        std::thread::sleep(std::time::Duration::from_millis(5));
        assert_eq!(
            ledger.interruption("timed"),
            Some(OperationInterruption::TimedOut)
        );

        let expired = EIPCallContext {
            operation_id: "expired".to_owned(),
            timeout_ms: Some(0),
        };
        assert!(matches!(
            ledger.begin("environment.describe", &expired, &params),
            Err(LedgerError::DeadlineExpired)
        ));
    }
}
