use std::{
    collections::BTreeMap,
    io,
    path::PathBuf,
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use base64::Engine as _;
use tokio::{
    fs::{self, File, OpenOptions},
    io::{AsyncReadExt as _, AsyncSeekExt as _, AsyncWriteExt as _},
    sync::{Mutex as AsyncMutex, Notify},
};

use crate::{
    eip::{EncodedBytes, OutputInfo, OutputReadParams, OutputReadResult, OutputReference},
    operation::ShortIdAllocator,
};

const RESPONSE_RESERVE_BYTES: u64 = 4096;

#[derive(Clone)]
pub(crate) struct RetentionQuota {
    inner: Arc<Mutex<QuotaState>>,
    max_bytes: u64,
    max_objects: usize,
    parent: Option<Arc<RetentionQuota>>,
    cleanup_failures: Arc<AtomicU64>,
    collected_objects: Arc<AtomicU64>,
}

#[derive(Default)]
struct QuotaState {
    bytes: u64,
    objects: usize,
}

#[derive(Clone)]
pub(crate) struct RetentionStore {
    inner: Arc<RetentionInner>,
}

struct RetentionInner {
    objects: Mutex<BTreeMap<String, Arc<OutputObject>>>,
    quota: RetentionQuota,
    spool: Option<PathBuf>,
    max_output_bytes_per_stream: u64,
    max_preview_bytes: usize,
    max_response_bytes: u64,
    selector_ids: ShortIdAllocator,
}

struct OutputObject {
    reference: OutputReference,
    path: PathBuf,
    state: Mutex<OutputState>,
    writer: AsyncMutex<Option<File>>,
    io: AsyncMutex<()>,
    changed: Notify,
    reservation: Arc<PairReservation>,
    reservation_index: usize,
    max_preview_bytes: usize,
}

struct OutputState {
    last_access: Instant,
    producer_complete: bool,
    content_complete: bool,
    produced_bytes: u64,
    retained_bytes: u64,
    quota_bytes: u64,
    charge_full_allowance: bool,
    preview: Vec<u8>,
    attached: bool,
    released: bool,
}

struct PairReservation {
    quota: RetentionQuota,
    limit: u64,
    state: Mutex<PairReservationState>,
}

struct PairReservationState {
    complete: [bool; 2],
    charged: [u64; 2],
    settled: bool,
}

#[derive(Clone)]
pub(crate) struct LiveOutput {
    object: Arc<OutputObject>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AppendOutcome {
    Complete,
    LimitCrossed,
    WriteFailed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RetentionError {
    InvalidSelector,
    InvalidOffset,
    Busy,
    Conflict,
    CleanupFailed,
    Internal,
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
pub(crate) struct SessionSpoolReservation {
    quota: RetentionQuota,
    bytes: u64,
    objects: usize,
}
#[cfg(any(target_os = "linux", target_os = "macos"))]
impl Drop for SessionSpoolReservation {
    fn drop(&mut self) {
        self.quota.release(self.bytes, self.objects);
    }
}

impl RetentionQuota {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    pub(crate) fn reserve_session(
        &self,
        bytes: u64,
        objects: usize,
    ) -> Option<SessionSpoolReservation> {
        self.reserve(bytes, objects)
            .then(|| SessionSpoolReservation {
                quota: self.clone(),
                bytes,
                objects,
            })
    }

    pub(crate) fn new(config: &crate::config::Config) -> Result<Self, RetentionError> {
        Ok(Self {
            inner: Arc::new(Mutex::new(QuotaState::default())),
            max_bytes: config.limits.max_device_spool_bytes,
            max_objects: usize::try_from(config.limits.max_device_spool_objects)
                .map_err(|_| RetentionError::Internal)?,
            parent: None,
            cleanup_failures: Arc::new(AtomicU64::new(0)),
            collected_objects: Arc::new(AtomicU64::new(0)),
        })
    }

    fn for_session(&self, config: &crate::config::Config) -> Result<Self, RetentionError> {
        Ok(Self {
            inner: Arc::new(Mutex::new(QuotaState::default())),
            max_bytes: config.limits.max_spool_bytes,
            max_objects: usize::try_from(config.limits.max_spool_objects)
                .map_err(|_| RetentionError::Internal)?,
            parent: Some(Arc::new(self.clone())),
            cleanup_failures: self.cleanup_failures.clone(),
            collected_objects: self.collected_objects.clone(),
        })
    }

    fn under_pressure(&self, bytes: u64, objects: usize) -> bool {
        let state = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        state.bytes.saturating_add(bytes) > self.max_bytes
            || state.objects.saturating_add(objects) > self.max_objects
            || self
                .parent
                .as_ref()
                .is_some_and(|parent| parent.under_pressure(bytes, objects))
    }

    fn reserve(&self, bytes: u64, objects: usize) -> bool {
        let mut state = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        let Some(next_bytes) = state.bytes.checked_add(bytes) else {
            return false;
        };
        let Some(next_objects) = state.objects.checked_add(objects) else {
            return false;
        };
        if next_bytes > self.max_bytes || next_objects > self.max_objects {
            return false;
        }
        if self
            .parent
            .as_ref()
            .is_some_and(|parent| !parent.reserve(bytes, objects))
        {
            return false;
        }
        state.bytes = next_bytes;
        state.objects = next_objects;
        true
    }

    fn release(&self, bytes: u64, objects: usize) {
        let mut state = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        state.bytes = state.bytes.saturating_sub(bytes);
        state.objects = state.objects.saturating_sub(objects);
        if let Some(parent) = &self.parent {
            parent.release(bytes, objects);
        }
    }

    pub(crate) fn diagnostics(&self) -> (u64, u64) {
        (
            self.cleanup_failures.load(Ordering::Relaxed),
            self.collected_objects.load(Ordering::Relaxed),
        )
    }

    pub(crate) fn usage(&self) -> (u64, usize) {
        let state = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        (state.bytes, state.objects)
    }
}

impl RetentionStore {
    pub(crate) fn new(
        config: &crate::config::Config,
        selector_ids: ShortIdAllocator,
        quota: RetentionQuota,
    ) -> Result<Self, RetentionError> {
        Ok(Self {
            inner: Arc::new(RetentionInner {
                objects: Mutex::new(BTreeMap::new()),
                quota: quota.for_session(config)?,
                spool: config
                    .runtime
                    .as_ref()
                    .map(|runtime| runtime.spool().to_path_buf()),
                max_output_bytes_per_stream: config.limits.max_output_bytes_per_stream,
                max_preview_bytes: usize::try_from(config.limits.max_output_preview_bytes)
                    .map_err(|_| RetentionError::Internal)?,
                max_response_bytes: config.limits.max_response_bytes,
                selector_ids,
            }),
        })
    }

    pub(crate) async fn create_live_pair(
        &self,
    ) -> Result<(LiveOutput, LiveOutput), RetentionError> {
        let spool = self.inner.spool.as_ref().ok_or(RetentionError::Internal)?;
        let limit = self.inner.max_output_bytes_per_stream;
        let reserved = limit.checked_mul(2).ok_or(RetentionError::Internal)?;
        if !self.inner.quota.reserve(reserved, 2) {
            return Err(RetentionError::Busy);
        }
        let reservation = Arc::new(PairReservation {
            quota: self.inner.quota.clone(),
            limit,
            state: Mutex::new(PairReservationState {
                complete: [false; 2],
                charged: [0; 2],
                settled: false,
            }),
        });
        let mut created: Vec<Arc<OutputObject>> = Vec::with_capacity(2);
        for index in 0..2 {
            let selector = match self.inner.selector_ids.next("output") {
                Ok(selector) => selector,
                Err(_) => {
                    self.rollback_creation(&created, &reservation).await;
                    return Err(RetentionError::Internal);
                }
            };
            let path = spool.join(format!("{selector}.spool"));
            let file = match OpenOptions::new()
                .create_new(true)
                .write(true)
                .read(false)
                .open(&path)
                .await
            {
                Ok(file) => file,
                Err(_) => {
                    self.rollback_creation(&created, &reservation).await;
                    return Err(RetentionError::Internal);
                }
            };
            let object = Arc::new(OutputObject {
                reference: OutputReference(selector),
                path,
                state: Mutex::new(OutputState {
                    last_access: Instant::now(),
                    producer_complete: false,
                    content_complete: true,
                    produced_bytes: 0,
                    retained_bytes: 0,
                    quota_bytes: 0,
                    charge_full_allowance: false,
                    preview: Vec::new(),
                    attached: true,
                    released: false,
                }),
                writer: AsyncMutex::new(Some(file)),
                io: AsyncMutex::new(()),
                changed: Notify::new(),
                reservation: Arc::clone(&reservation),
                reservation_index: index,
                max_preview_bytes: self.inner.max_preview_bytes,
            });
            created.push(object);
        }
        let collision = {
            let mut objects = self.objects();
            if created
                .iter()
                .any(|object| objects.contains_key(&object.reference.0))
            {
                true
            } else {
                for object in &created {
                    objects.insert(object.reference.0.clone(), Arc::clone(object));
                }
                false
            }
        };
        if collision {
            self.rollback_creation(&created, &reservation).await;
            return Err(RetentionError::Internal);
        }
        Ok((
            LiveOutput {
                object: Arc::clone(&created[0]),
            },
            LiveOutput {
                object: Arc::clone(&created[1]),
            },
        ))
    }

    async fn rollback_creation(
        &self,
        created: &[Arc<OutputObject>],
        reservation: &PairReservation,
    ) {
        // Even a partial pair retains a registry owner until native deletion succeeds.
        // The uncreated half has no native resource and can release its object charge now.
        for index in created.len()..2 {
            reservation.complete(index, 0);
            self.inner.quota.release(0, 1);
        }
        for object in created {
            self.objects()
                .entry(object.reference.0.clone())
                .or_insert_with(|| Arc::clone(object));
            self.abort_output(&LiveOutput {
                object: Arc::clone(object),
            })
            .await;
        }
    }

    pub(crate) async fn read(
        &self,
        params: &OutputReadParams,
        deadline: Instant,
    ) -> Result<OutputReadResult, RetentionError> {
        let object = self
            .objects()
            .get(&params.reference.0)
            .cloned()
            .ok_or(RetentionError::InvalidSelector)?;
        object
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .last_access = Instant::now();
        let initial = object.snapshot()?;
        if params.start_offset > initial.retained_bytes {
            return Err(RetentionError::InvalidOffset);
        }
        if params.start_offset == initial.retained_bytes
            && !initial.producer_complete
            && params.wait_ms > 0
        {
            let wait_until = deadline.min(Instant::now() + Duration::from_millis(params.wait_ms));
            loop {
                let notified = object.changed.notified();
                let current = object.snapshot()?;
                if current.retained_bytes > params.start_offset || current.producer_complete {
                    break;
                }
                let remaining = wait_until.saturating_duration_since(Instant::now());
                if remaining.is_zero() || tokio::time::timeout(remaining, notified).await.is_err() {
                    break;
                }
            }
        }
        let _io = object.io.lock().await;
        let before = object.snapshot()?;
        if params.start_offset > before.retained_bytes {
            return Err(RetentionError::InvalidOffset);
        }
        let maximum = self
            .inner
            .max_response_bytes
            .saturating_sub(RESPONSE_RESERVE_BYTES)
            / 4
            * 3;
        let available = before.retained_bytes.saturating_sub(params.start_offset);
        let length =
            usize::try_from(available.min(maximum)).map_err(|_| RetentionError::Internal)?;
        let mut data = vec![0_u8; length];
        if length > 0 {
            let mut file = File::open(&object.path)
                .await
                .map_err(|_| RetentionError::Internal)?;
            file.seek(io::SeekFrom::Start(params.start_offset))
                .await
                .map_err(|_| RetentionError::Internal)?;
            file.read_exact(&mut data)
                .await
                .map_err(|_| RetentionError::Internal)?;
        }
        let output = object.snapshot()?;
        let next_offset = params
            .start_offset
            .checked_add(data.len() as u64)
            .ok_or(RetentionError::Internal)?;
        Ok(OutputReadResult {
            next_offset,
            start_offset: params.start_offset,
            data: encoded(&data),
            output,
        })
    }

    pub(crate) async fn release_reference(
        &self,
        reference: &OutputReference,
    ) -> Result<bool, RetentionError> {
        let object = self
            .objects()
            .get(&reference.0)
            .cloned()
            .ok_or(RetentionError::InvalidSelector)?;
        let _io = object.io.lock().await;
        {
            let state = object.state.lock().unwrap_or_else(PoisonError::into_inner);
            if !state.producer_complete || state.attached {
                return Err(RetentionError::Conflict);
            }
            if state.released {
                return Err(RetentionError::InvalidSelector);
            }
        }
        match fs::remove_file(&object.path).await {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(_) => {
                self.inner
                    .quota
                    .cleanup_failures
                    .fetch_add(1, Ordering::Relaxed);
                return Err(RetentionError::CleanupFailed);
            }
        }
        let charged = {
            let mut state = object.state.lock().unwrap_or_else(PoisonError::into_inner);
            if state.released {
                return Err(RetentionError::InvalidSelector);
            }
            state.released = true;
            state.quota_bytes
        };
        let removed = self.objects().remove(&reference.0);
        if removed.is_none() {
            return Err(RetentionError::Internal);
        }
        self.inner.quota.release(charged, 1);
        self.inner
            .quota
            .collected_objects
            .fetch_add(1, Ordering::Relaxed);
        Ok(true)
    }

    pub(crate) fn under_pressure(&self) -> bool {
        self.inner
            .quota
            .under_pressure(self.inner.max_output_bytes_per_stream.saturating_mul(2), 2)
    }

    /// The Session history gate excludes admitted reads and response handoffs.
    pub(crate) async fn collect(&self, ttl: Duration, pressure: bool) -> Vec<String> {
        let mut candidates: Vec<_> = self
            .objects()
            .values()
            .filter_map(|object| {
                let state = object.state.lock().unwrap_or_else(PoisonError::into_inner);
                (state.producer_complete
                    && !state.attached
                    && (pressure || state.last_access.elapsed() >= ttl))
                    .then(|| (state.last_access, object.reference.clone()))
            })
            .collect();
        candidates.sort_by_key(|(access, _)| *access);
        let mut released = Vec::new();
        for (_, reference) in candidates {
            if self.release_reference(&reference).await.is_ok() {
                released.push(reference.0);
            }
        }
        released
    }

    pub(crate) async fn close(&self) -> bool {
        let objects: Vec<_> = self.objects().values().cloned().collect();
        let mut complete = true;
        for object in objects {
            let terminal = {
                let mut state = object.state.lock().unwrap_or_else(PoisonError::into_inner);
                if state.producer_complete {
                    state.attached = false;
                    true
                } else {
                    false
                }
            };
            if !terminal || self.release_reference(&object.reference).await.is_err() {
                complete = false;
            }
        }
        complete
    }

    pub(crate) async fn abort_pair(&self, outputs: [&LiveOutput; 2]) {
        for output in outputs {
            self.abort_output(output).await;
        }
    }

    async fn abort_output(&self, output: &LiveOutput) {
        output.mark_write_failed(true);
        output.complete().await;
        output.detach();
        // Failed deletion remains terminal and detached in the ordinary registry;
        // maintenance and Session close retry it without losing quota ownership.
        let _ = self.release_reference(output.reference()).await;
    }

    #[cfg(test)]
    pub(crate) fn quota(&self) -> (u64, usize) {
        self.inner.quota.usage()
    }

    fn objects(&self) -> std::sync::MutexGuard<'_, BTreeMap<String, Arc<OutputObject>>> {
        self.inner
            .objects
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }
}

impl LiveOutput {
    pub(crate) fn last_access(&self) -> Instant {
        self.object
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .last_access
    }

    pub(crate) fn reference(&self) -> &OutputReference {
        &self.object.reference
    }
    pub(crate) async fn append(&self, bytes: &[u8]) -> AppendOutcome {
        let _io = self.object.io.lock().await;
        let (prior_retained, write_len, crossed) = {
            let mut state = self
                .object
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            if state.producer_complete {
                state.content_complete = false;
                return AppendOutcome::WriteFailed;
            }
            let Some(produced) = state.produced_bytes.checked_add(bytes.len() as u64) else {
                state.content_complete = false;
                return AppendOutcome::WriteFailed;
            };
            state.produced_bytes = produced;
            let remaining = self
                .object
                .reservation
                .limit
                .saturating_sub(state.retained_bytes);
            let write_len = usize::try_from(remaining.min(bytes.len() as u64)).unwrap_or(0);
            let crossed = write_len < bytes.len();
            if crossed {
                state.content_complete = false;
            }
            (state.retained_bytes, write_len, crossed)
        };
        if write_len > 0 {
            let mut writer = self.object.writer.lock().await;
            let Some(file) = writer.as_mut() else {
                self.mark_write_failed(true);
                return AppendOutcome::WriteFailed;
            };
            if file.write_all(&bytes[..write_len]).await.is_err() || file.flush().await.is_err() {
                let restored = file.set_len(prior_retained).await.is_ok()
                    && file.seek(io::SeekFrom::Start(prior_retained)).await.is_ok();
                *writer = None;
                self.mark_write_failed(!restored);
                return AppendOutcome::WriteFailed;
            }
            let mut state = self
                .object
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.retained_bytes = state.retained_bytes.saturating_add(write_len as u64);
            if state.preview.len() < self.object.max_preview_bytes {
                let count = (self.object.max_preview_bytes - state.preview.len()).min(write_len);
                state.preview.extend_from_slice(&bytes[..count]);
            }
        }
        self.object.changed.notify_waiters();
        if crossed {
            AppendOutcome::LimitCrossed
        } else {
            AppendOutcome::Complete
        }
    }

    fn mark_write_failed(&self, charge_full_allowance: bool) {
        let mut state = self
            .object
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.content_complete = false;
        state.charge_full_allowance |= charge_full_allowance;
        self.object.changed.notify_waiters();
    }

    pub(crate) fn mark_incomplete(&self) {
        self.object
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .content_complete = false;
        self.object.changed.notify_waiters();
    }

    pub(crate) async fn complete(&self) {
        let _io = self.object.io.lock().await;
        {
            let mut writer = self.object.writer.lock().await;
            if let Some(file) = writer.as_mut()
                && file.flush().await.is_err()
            {
                self.mark_write_failed(true);
            }
            *writer = None;
        }
        let charged = {
            let mut state = self
                .object
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            if state.producer_complete {
                return;
            }
            state.producer_complete = true;
            state.last_access = Instant::now();
            state.quota_bytes = if state.charge_full_allowance {
                self.object.reservation.limit
            } else {
                state.retained_bytes
            };
            state.quota_bytes
        };
        self.object
            .reservation
            .complete(self.object.reservation_index, charged);
        self.object.changed.notify_waiters();
    }

    pub(crate) fn snapshot(&self) -> Result<OutputInfo, RetentionError> {
        self.object.snapshot()
    }

    pub(crate) fn detach(&self) {
        self.object
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .attached = false;
    }
}

impl OutputObject {
    fn snapshot(&self) -> Result<OutputInfo, RetentionError> {
        let state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        if state.released {
            return Err(RetentionError::InvalidSelector);
        }
        Ok(OutputInfo {
            reference: self.reference.clone(),
            producer_complete: state.producer_complete,
            content_complete: state.producer_complete && state.content_complete,
            produced_bytes: state.produced_bytes,
            retained_bytes: state.retained_bytes,
            preview: encoded(&state.preview),
        })
    }
}

impl PairReservation {
    fn complete(&self, index: usize, charged: u64) {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        if state.complete[index] {
            return;
        }
        state.complete[index] = true;
        state.charged[index] = charged;
        if state.complete == [true, true] && !state.settled {
            state.settled = true;
            let reserved = self.limit.saturating_mul(2);
            let actual = state.charged[0].saturating_add(state.charged[1]);
            self.quota.release(reserved.saturating_sub(actual), 0);
        }
    }
}

fn encoded(bytes: &[u8]) -> EncodedBytes {
    EncodedBytes {
        encoding: "base64".to_owned(),
        data: base64::engine::general_purpose::STANDARD_NO_PAD.encode(bytes),
    }
}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        path::PathBuf,
        sync::atomic::{AtomicU64, Ordering},
        time::{Duration, Instant},
    };

    use base64::Engine as _;

    use crate::{
        config::Config,
        eip::{EIPCallContext, OutputReadParams},
        runtime::RuntimeState,
    };

    use super::{AppendOutcome, RetentionError, RetentionQuota, RetentionStore};

    static NEXT_TEST: AtomicU64 = AtomicU64::new(1);

    #[test]
    fn scoped_quota_preserves_sibling_capacity_and_rolls_back_device_rejection() {
        let mut config = Config::for_test("device-test");
        config.limits.max_spool_bytes = 8;
        config.limits.max_spool_objects = 2;
        config.limits.max_device_spool_bytes = 12;
        config.limits.max_device_spool_objects = 4;
        let device = RetentionQuota::new(&config).unwrap();
        let first = device.for_session(&config).unwrap();
        let sibling = device.for_session(&config).unwrap();
        assert!(first.reserve(8, 2));
        assert!(!first.reserve(1, 0));
        assert!(!first.reserve(0, 1));
        assert_eq!(device.usage(), (8, 2));
        assert!(!sibling.reserve(8, 2));
        assert_eq!(sibling.usage(), (0, 0));
        assert!(sibling.reserve(4, 2));
        assert_eq!(device.usage(), (12, 4));
        first.release(8, 2);
        assert_eq!(device.usage(), (4, 2));
        assert_eq!(sibling.usage(), (4, 2));
        sibling.release(4, 2);
        assert_eq!(device.usage(), (0, 0));
    }

    fn setup(
        stream_limit: u64,
        spool_limit: u64,
        object_limit: u64,
    ) -> (PathBuf, Config, RetentionStore) {
        let parent = std::env::temp_dir().join(format!(
            "a13n-envd-retention-test-{}-{}",
            std::process::id(),
            NEXT_TEST.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&parent);
        let runtime = RuntimeState::prepare(&parent).expect("runtime");
        let mut config = Config::for_test("env-test");
        config.runtime = Some(runtime);
        config.limits.max_output_preview_bytes = stream_limit.clamp(1, 3);
        config.limits.max_output_bytes_per_stream = stream_limit;
        config.limits.max_spool_bytes = spool_limit;
        config.limits.max_spool_objects = object_limit;
        let quota = RetentionQuota::new(&config).expect("quota");
        let store = RetentionStore::new(
            &config,
            crate::operation::ShortIdAllocator::for_generation(1),
            quota,
        )
        .expect("store");
        (parent, config, store)
    }

    fn cleanup(parent: PathBuf, config: Config, store: RetentionStore) {
        drop(store);
        drop(config);
        fs::remove_file(parent.join(".a13n-envd.lock")).expect("lock file");
        fs::remove_dir(parent).expect("runtime parent");
    }

    fn read_params(reference: crate::eip::OutputReference, start_offset: u64) -> OutputReadParams {
        OutputReadParams {
            context: EIPCallContext {
                operation_id: format!("read-{start_offset}"),
                timeout_ms: None,
            },
            reference,
            start_offset,
            wait_ms: 0,
        }
    }

    #[tokio::test]
    async fn partial_creation_rolls_back_without_deleting_the_conflicting_path() {
        let (parent, config, store) = setup(4, 8, 2);
        let conflict = config
            .runtime
            .as_ref()
            .unwrap()
            .spool()
            .join("output-1-2.spool");
        fs::create_dir(&conflict).unwrap();
        assert!(matches!(
            store.create_live_pair().await,
            Err(RetentionError::Internal)
        ));
        assert_eq!(store.quota(), (0, 0));
        assert!(store.objects().is_empty());
        assert!(conflict.is_dir());
        fs::remove_dir(conflict).unwrap();
        cleanup(parent, config, store);
    }

    #[tokio::test]
    async fn failed_partial_creation_cleanup_keeps_an_owner_and_charge_until_retry() {
        let (parent, config, store) = setup(4, 8, 2);
        let (stdout, stderr) = store.create_live_pair().await.unwrap();
        // Reproduce rollback after the first native object was created, before registry publication.
        stderr.object.writer.lock().await.take();
        fs::remove_file(&stderr.object.path).unwrap();
        store.objects().clear();
        let displaced = stdout.object.path.with_extension("held");
        fs::rename(&stdout.object.path, &displaced).unwrap();
        fs::create_dir(&stdout.object.path).unwrap();
        store
            .rollback_creation(
                std::slice::from_ref(&stdout.object),
                &stdout.object.reservation,
            )
            .await;
        assert_eq!(store.quota(), (4, 1));
        assert_eq!(store.objects().len(), 1);
        assert!(!store.close().await);
        assert_eq!(store.quota(), (4, 1));
        fs::remove_dir(&stdout.object.path).unwrap();
        fs::rename(displaced, &stdout.object.path).unwrap();
        assert!(store.close().await);
        assert_eq!(store.quota(), (0, 0));
        drop((stdout, stderr));
        cleanup(parent, config, store);
    }

    #[tokio::test]
    async fn failed_abort_deletion_is_retried_without_losing_pair_accounting() {
        let (parent, config, store) = setup(4, 8, 2);
        let (stdout, stderr) = store.create_live_pair().await.unwrap();
        stdout.append(b"data").await;
        let displaced = stdout.object.path.with_extension("held");
        fs::rename(&stdout.object.path, &displaced).unwrap();
        fs::create_dir(&stdout.object.path).unwrap();
        store.abort_pair([&stdout, &stderr]).await;
        assert_eq!(store.quota(), (4, 1));
        assert_eq!(store.objects().len(), 1);
        assert!(stdout.snapshot().unwrap().producer_complete);
        assert_eq!(stdout.append(b"late").await, AppendOutcome::WriteFailed);
        assert!(!store.close().await);
        fs::remove_dir(&stdout.object.path).unwrap();
        fs::rename(displaced, &stdout.object.path).unwrap();
        assert_eq!(
            store.collect(Duration::ZERO, true).await,
            vec![stdout.reference().0.clone()]
        );
        assert_eq!(store.quota(), (0, 0));
        store.abort_pair([&stdout, &stderr]).await;
        assert_eq!(store.quota(), (0, 0));
        drop((stdout, stderr));
        cleanup(parent, config, store);
    }

    #[tokio::test]
    async fn pair_reservation_is_atomic_independent_and_settles_once() {
        let (parent, config, store) = setup(4, 8, 2);
        let (stdout, stderr) = store.create_live_pair().await.expect("pair");
        assert_eq!(store.quota(), (8, 2));
        assert!(matches!(
            store.create_live_pair().await,
            Err(RetentionError::Busy)
        ));

        assert_eq!(stdout.append(b"abcdef").await, AppendOutcome::LimitCrossed);
        stdout.complete().await;
        assert_eq!(store.quota(), (8, 2), "one stream cannot settle the pair");
        assert_eq!(stderr.append(b"z").await, AppendOutcome::Complete);
        stderr.complete().await;
        stderr.complete().await;
        assert_eq!(store.quota(), (5, 2), "pair settles exactly once");

        let stdout_info = stdout.snapshot().expect("stdout snapshot");
        assert_eq!(stdout_info.produced_bytes, 6);
        assert_eq!(stdout_info.retained_bytes, 4);
        assert!(!stdout_info.content_complete);
        let result = store
            .read(
                &read_params(stdout_info.reference.clone(), 0),
                Instant::now() + Duration::from_secs(1),
            )
            .await
            .expect("read retained prefix");
        assert_eq!(result.next_offset, 4);
        assert_eq!(
            base64::engine::general_purpose::STANDARD_NO_PAD
                .decode(result.data.data)
                .expect("base64"),
            b"abcd"
        );

        assert_eq!(
            store.release_reference(&stdout_info.reference).await,
            Err(RetentionError::Conflict)
        );
        stdout.detach();
        stderr.detach();
        assert!(
            store
                .release_reference(&stdout_info.reference)
                .await
                .expect("release stdout")
        );
        assert_eq!(store.quota(), (1, 1));
        assert!(
            store
                .release_reference(&stderr.snapshot().expect("stderr snapshot").reference)
                .await
                .expect("release stderr")
        );
        assert_eq!(store.quota(), (0, 0));

        drop(stdout);
        drop(stderr);
        cleanup(parent, config, store);
    }

    #[tokio::test]
    async fn contiguous_wait_observes_new_bytes_and_terminal_eof() {
        let (parent, config, store) = setup(16, 32, 2);
        let (stdout, stderr) = store.create_live_pair().await.expect("pair");
        let reference = stdout.snapshot().expect("stdout snapshot").reference;
        let waiting_store = store.clone();
        let waiting_reference = reference.clone();
        let waiter = tokio::spawn(async move {
            let mut params = read_params(waiting_reference, 0);
            params.wait_ms = 1_000;
            waiting_store
                .read(&params, Instant::now() + Duration::from_secs(2))
                .await
        });
        tokio::time::sleep(Duration::from_millis(10)).await;
        stdout.append(b"page").await;
        let page = waiter.await.expect("waiter").expect("page");
        assert_eq!(page.start_offset, 0);
        assert_eq!(page.next_offset, 4);

        stdout.complete().await;
        stderr.complete().await;
        let eof = store
            .read(
                &read_params(reference, 4),
                Instant::now() + Duration::from_secs(1),
            )
            .await
            .expect("terminal eof");
        assert_eq!(eof.next_offset, 4);
        assert!(eof.output.producer_complete);

        stdout.detach();
        stderr.detach();
        store
            .release_reference(&stdout.snapshot().expect("stdout snapshot").reference)
            .await
            .expect("release stdout");
        store
            .release_reference(&stderr.snapshot().expect("stderr snapshot").reference)
            .await
            .expect("release stderr");
        drop(stdout);
        drop(stderr);
        cleanup(parent, config, store);
    }
}
