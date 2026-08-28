use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    io::{Read, Seek, SeekFrom},
    sync::{
        Arc, Mutex as StdMutex, PoisonError,
        atomic::{AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};

use sha2::{Digest, Sha256};
use tokio::{
    io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt},
    sync::{Mutex, Notify, mpsc, watch},
};

use crate::{
    eip::{
        ContentDigest, DataFrame, DataFrameKind, DataResetStatus, EIPPath, FileInfo, FileKind,
        FileReadCompletion, FileReaderCloseResult, FileReaderHandle, FileReaderOpenParams,
        FileReaderOpenResult, FileWriteMode, FileWriterAbortStatus, FileWriterCommitParams,
        FileWriterHandle, FileWriterOpenParams, FileWriterOpenResult,
    },
    mount::{Mount, MountPathError, MountRegistry, StagedCandidate},
    operation::{LedgerError, OperationInterruption, OperationLedger, ShortIdAllocator},
};

#[derive(Clone)]
pub(crate) struct TransferRegistry {
    inner: Arc<TransferInner>,
}

struct TransferInner {
    state: StdMutex<TransferState>,
    outbound: StdMutex<Option<mpsc::Sender<DataFrame>>>,
    max_frame_bytes: usize,
    max_transfer_bytes: u64,
    max_records: usize,
    max_active: usize,
    terminal_ttl: Duration,
    idle_ttl: Duration,
    max_duration: Duration,
    max_operation_duration: Duration,
    selector_ids: ShortIdAllocator,
    active_producers: AtomicUsize,
    producers_idle: Notify,
}

#[derive(Default)]
struct TransferState {
    records: BTreeMap<String, TransferRecord>,
    detached_handles: BTreeSet<String>,
    terminal_order: VecDeque<(String, Instant)>,
    reservations: usize,
    session_closed: bool,
}

struct ProducerGuard {
    inner: Arc<TransferInner>,
}

impl Drop for ProducerGuard {
    fn drop(&mut self) {
        if self.inner.active_producers.fetch_sub(1, Ordering::AcqRel) == 1 {
            self.inner.producers_idle.notify_waiters();
        }
    }
}

struct TransferReservation {
    registry: TransferRegistry,
    active: bool,
}

#[derive(Clone)]
enum TransferRecord {
    Reader(Arc<Mutex<ReaderRecord>>),
    Writer(Arc<Mutex<WriterRecord>>),
}

struct ReaderRecord {
    handle: String,
    file: Option<std::fs::File>,
    start_offset: u64,
    max_bytes: u64,
    phase: ReaderPhase,
    produced: u64,
    digest: Option<ContentDigest>,
    expires_at: chrono::DateTime<chrono::Utc>,
    last_progress: Instant,
    cancellation: watch::Sender<bool>,
    close_result: Option<FileReaderCloseResult>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ReaderPhase {
    Open,
    Streaming,
    AwaitingAck,
    Reset,
    Closed,
}

struct WriterRecord {
    handle: String,
    path: EIPPath,
    mode: FileWriteMode,
    executable: Option<bool>,
    candidate: Option<StagedCandidate>,
    file: Option<tokio::fs::File>,
    phase: WriterPhase,
    transferred: u64,
    prefix_bytes: u64,
    prefix_digest: Option<ContentDigest>,
    hasher: Sha256,
    digest: Option<ContentDigest>,
    max_transfer_bytes: u64,
    expires_at: chrono::DateTime<chrono::Utc>,
    last_progress: Instant,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum WriterPhase {
    Open,
    Receiving,
    Sealed,
    Committing,
    Committed,
    UnknownOutcome,
    Aborted,
}

pub(crate) struct WriterCommit {
    registry: TransferRegistry,
    record: Arc<Mutex<WriterRecord>>,
    handle: String,
    path: EIPPath,
    mode: FileWriteMode,
    executable: Option<bool>,
    candidate: StagedCandidate,
    transferred: u64,
    prefix_bytes: u64,
    prefix_digest: Option<ContentDigest>,
    transfer_digest: ContentDigest,
}

pub(crate) struct WriterCommitOutput {
    pub(crate) info: FileInfo,
    pub(crate) transferred_bytes: u64,
    pub(crate) transfer_digest: ContentDigest,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TransferError {
    InvalidHandle,
    WrongKind,
    WrongState,
    Conflict,
    IntegrityMismatch,
    Expired,
    Busy,
    Quota,
    Unsupported,
    Denied,
    NotFound,
    Limit,
    Source,
    Protocol,
    Cancelled,
    Timeout,
    UnknownOutcome,
    CleanupFailed,
    SessionClosed,
    Internal,
}

pub(crate) fn reset_status(error: TransferError) -> DataResetStatus {
    match error {
        TransferError::Denied
        | TransferError::NotFound
        | TransferError::InvalidHandle
        | TransferError::WrongKind => DataResetStatus::Denied,
        TransferError::Expired | TransferError::Timeout => DataResetStatus::Expired,
        TransferError::Source => DataResetStatus::Source,
        TransferError::Busy | TransferError::Quota | TransferError::Limit => DataResetStatus::Limit,
        TransferError::Protocol | TransferError::WrongState | TransferError::Conflict => {
            DataResetStatus::Protocol
        }
        TransferError::Cancelled | TransferError::SessionClosed => DataResetStatus::Cancelled,
        TransferError::IntegrityMismatch
        | TransferError::Unsupported
        | TransferError::UnknownOutcome
        | TransferError::CleanupFailed
        | TransferError::Internal => DataResetStatus::Internal,
    }
}

impl TransferRegistry {
    pub(crate) fn new(
        config: &crate::config::Config,
        generation: u64,
    ) -> Result<Self, TransferError> {
        Ok(Self {
            inner: Arc::new(TransferInner {
                state: StdMutex::new(TransferState::default()),
                outbound: StdMutex::new(None),
                max_frame_bytes: usize::try_from(config.limits.max_transfer_frame_bytes)
                    .map_err(|_| TransferError::Internal)?,
                max_transfer_bytes: config.limits.max_staged_file_bytes,
                max_records: usize::try_from(config.limits.max_file_transfer_records)
                    .map_err(|_| TransferError::Internal)?,
                max_active: usize::try_from(config.limits.max_concurrent_file_transfers)
                    .map_err(|_| TransferError::Internal)?,
                terminal_ttl: Duration::from_millis(config.limits.file_transfer_record_ttl_ms),
                idle_ttl: Duration::from_millis(config.limits.file_transfer_idle_ttl_ms),
                max_duration: Duration::from_millis(config.limits.max_file_transfer_duration_ms),
                max_operation_duration: Duration::from_millis(
                    config.limits.max_operation_duration_ms,
                ),
                selector_ids: ShortIdAllocator::for_generation(generation),
                active_producers: AtomicUsize::new(0),
                producers_idle: Notify::new(),
            }),
        })
    }

    pub(crate) fn begin_session_close(&self) {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .session_closed = true;
    }

    pub(crate) fn begin_session(
        &self,
        sender: mpsc::Sender<DataFrame>,
    ) -> Result<(), TransferError> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let mut outbound = self
            .inner
            .outbound
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.reservations != 0 || outbound.is_some() {
            return Err(TransferError::Conflict);
        }
        state.session_closed = false;
        *outbound = Some(sender);
        Ok(())
    }

    pub(crate) async fn open_reader(
        &self,
        mounts: &MountRegistry,
        params: &FileReaderOpenParams,
    ) -> Result<FileReaderOpenResult, TransferError> {
        self.expire().await;
        let reservation = self.reserve_record()?;
        let mount = mounts
            .get(&params.path.mount_id)
            .ok_or(TransferError::Denied)?;
        if !mount.allows("open_reader") {
            return Err(TransferError::Denied);
        }
        let opened = mount.open_regular(&params.path).map_err(map_mount_error)?;
        let info = file_info(&params.path, &opened.metadata);
        let start_offset = params.byte_range.as_ref().map_or(0, |range| range.offset);
        let available = opened.metadata.len().saturating_sub(start_offset);
        let max_bytes = params
            .byte_range
            .as_ref()
            .and_then(|range| range.length)
            .unwrap_or(available);
        if max_bytes > self.inner.max_transfer_bytes
            || (params.byte_range.is_none() && available > self.inner.max_transfer_bytes)
        {
            return Err(TransferError::Limit);
        }
        let expires_at = self.transfer_expiry(params.transfer_timeout_ms)?;
        let handle = self
            .inner
            .selector_ids
            .next("reader")
            .map_err(map_ledger_error)?;
        let (cancellation, _) = watch::channel(false);
        let record = Arc::new(Mutex::new(ReaderRecord {
            handle: handle.clone(),
            file: Some(opened.file),
            start_offset,
            max_bytes,
            phase: ReaderPhase::Open,
            produced: 0,
            digest: None,
            expires_at,
            last_progress: Instant::now(),
            cancellation,
            close_result: None,
        }));
        reservation.insert(handle.clone(), TransferRecord::Reader(record))?;
        Ok(FileReaderOpenResult {
            reader: FileReaderHandle(handle),
            info,
            expires_at,
        })
    }

    pub(crate) async fn close_reader(
        &self,
        handle: &FileReaderHandle,
    ) -> Result<FileReaderCloseResult, TransferError> {
        let record = match self.record(&handle.0)? {
            TransferRecord::Reader(record) => record,
            TransferRecord::Writer(_) => return Err(TransferError::WrongKind),
        };
        let mut reader = record.lock().await;
        if let Some(result) = &reader.close_result {
            return Ok(result.clone());
        }
        if expired(reader.expires_at, reader.last_progress, self.inner.idle_ttl) {
            reader.cancellation.send_replace(true);
            reader.phase = ReaderPhase::Reset;
            let handle = reader.handle.clone();
            drop(reader);
            self.mark_terminal(&handle);
            return Err(TransferError::Expired);
        }
        if reader.phase != ReaderPhase::AwaitingAck {
            return Err(TransferError::WrongState);
        }
        let completion = FileReadCompletion {
            produced_bytes: reader.produced,
            digest: reader.digest.clone().ok_or(TransferError::Internal)?,
        };
        let result = FileReaderCloseResult { completion };
        reader.phase = ReaderPhase::Closed;
        reader.close_result = Some(result.clone());
        drop(reader);
        self.mark_terminal(&handle.0);
        Ok(result)
    }

    pub(crate) async fn open_writer(
        &self,
        mounts: &MountRegistry,
        params: &FileWriterOpenParams,
    ) -> Result<FileWriterOpenResult, TransferError> {
        self.expire().await;
        let reservation = self.reserve_record()?;
        let mount = match mounts.get(&params.path.mount_id) {
            Some(mount) if mount.writable && mount.allows("open_writer") => mount,
            _ => return Err(TransferError::Denied),
        };
        let destination = observe_destination(&mount, &params.path)?;
        validate_open_mode(params.mode, destination.as_ref())?;
        let mut candidate = mount
            .create_candidate(&params.path)
            .map_err(map_mount_error)?;
        if params.executable.is_none()
            && let Some(metadata) = &destination
        {
            set_permissions_from(&candidate.file, metadata).map_err(|_| TransferError::Source)?;
        }
        let mut prefix_bytes = 0_u64;
        let mut prefix_digest = None;
        if params.mode == FileWriteMode::Append {
            let opened = mount.open_regular(&params.path).map_err(map_mount_error)?;
            prefix_bytes = opened.metadata.len();
            candidate
                .reserve_bytes(prefix_bytes)
                .map_err(map_mount_error)?;
            let mut source = tokio::fs::File::from_std(opened.file).take(prefix_bytes);
            let destination = candidate
                .file
                .try_clone()
                .map(tokio::fs::File::from_std)
                .map_err(|_| TransferError::Internal)?;
            let mut destination = destination;
            let copy = async {
                let mut copied = 0_u64;
                let mut hasher = Sha256::new();
                let mut buffer = [0_u8; 64 * 1024];
                loop {
                    let read = source
                        .read(&mut buffer)
                        .await
                        .map_err(|_| TransferError::Source)?;
                    if read == 0 {
                        break;
                    }
                    destination
                        .write_all(&buffer[..read])
                        .await
                        .map_err(|_| TransferError::Source)?;
                    hasher.update(&buffer[..read]);
                    copied = copied
                        .checked_add(read as u64)
                        .ok_or(TransferError::Limit)?;
                }
                destination
                    .flush()
                    .await
                    .map_err(|_| TransferError::Source)?;
                Ok::<(u64, ContentDigest), TransferError>((
                    copied,
                    ContentDigest {
                        algorithm: "sha256".to_owned(),
                        value: format!("{:x}", hasher.finalize()),
                    },
                ))
            };
            let (copied, digest) =
                tokio::time::timeout(self.operation_timeout(params.context.timeout_ms)?, copy)
                    .await
                    .map_err(|_| TransferError::Timeout)??;
            if copied != prefix_bytes {
                return Err(TransferError::Conflict);
            }
            prefix_digest = Some(digest);
        }
        let max_transfer_bytes = mount.max_file_bytes.saturating_sub(prefix_bytes);
        let writer_file = candidate
            .file
            .try_clone()
            .map(tokio::fs::File::from_std)
            .map_err(|_| TransferError::Internal)?;
        let expires_at = self.transfer_expiry(params.transfer_timeout_ms)?;
        let handle = self
            .inner
            .selector_ids
            .next("writer")
            .map_err(map_ledger_error)?;
        let record = Arc::new(Mutex::new(WriterRecord {
            handle: handle.clone(),
            path: params.path.clone(),
            mode: params.mode,
            executable: params.executable,
            candidate: Some(candidate),
            file: Some(writer_file),
            phase: WriterPhase::Open,
            transferred: 0,
            prefix_bytes,
            prefix_digest,
            hasher: Sha256::new(),
            digest: None,
            max_transfer_bytes,
            expires_at,
            last_progress: Instant::now(),
        }));
        reservation.insert(handle.clone(), TransferRecord::Writer(record))?;
        Ok(FileWriterOpenResult {
            writer: FileWriterHandle(handle),
            max_transfer_bytes,
            expires_at,
        })
    }

    pub(crate) async fn abort_writer(
        &self,
        handle: &FileWriterHandle,
    ) -> Result<FileWriterAbortStatus, TransferError> {
        let record = match self.record(&handle.0)? {
            TransferRecord::Writer(record) => record,
            TransferRecord::Reader(_) => return Err(TransferError::WrongKind),
        };
        let mut writer = record.lock().await;
        let (status, cleanup) = match writer.phase {
            WriterPhase::Committed => (FileWriterAbortStatus::AlreadyCommitted, Ok(())),
            WriterPhase::Committing | WriterPhase::UnknownOutcome => {
                (FileWriterAbortStatus::CommitInProgress, Ok(()))
            }
            WriterPhase::Aborted => (FileWriterAbortStatus::AlreadyAborted, Ok(())),
            _ => {
                writer.phase = WriterPhase::Aborted;
                (
                    FileWriterAbortStatus::Aborted,
                    self.discard_writer_candidate(&mut writer),
                )
            }
        };
        let terminal = matches!(
            status,
            FileWriterAbortStatus::Aborted
                | FileWriterAbortStatus::AlreadyAborted
                | FileWriterAbortStatus::AlreadyCommitted
        );
        drop(writer);
        if terminal {
            self.mark_terminal(&handle.0);
        }
        cleanup?;
        Ok(status)
    }

    pub(crate) async fn prepare_commit(
        &self,
        params: &FileWriterCommitParams,
    ) -> Result<WriterCommit, TransferError> {
        let record = match self.record(&params.writer.0)? {
            TransferRecord::Writer(record) => record,
            TransferRecord::Reader(_) => return Err(TransferError::WrongKind),
        };
        let mut writer = record.lock().await;
        if expired(writer.expires_at, writer.last_progress, self.inner.idle_ttl) {
            writer.phase = WriterPhase::Aborted;
            let cleanup = self.discard_writer_candidate(&mut writer);
            drop(writer);
            self.mark_terminal(&params.writer.0);
            cleanup?;
            return Err(TransferError::Expired);
        }
        if writer.phase != WriterPhase::Sealed {
            return Err(TransferError::WrongState);
        }
        let digest = writer.digest.clone().ok_or(TransferError::WrongState)?;
        if params.transferred_bytes != writer.transferred || params.transfer_digest != digest {
            writer.phase = WriterPhase::Aborted;
            let cleanup = self.discard_writer_candidate(&mut writer);
            drop(writer);
            self.mark_terminal(&params.writer.0);
            cleanup?;
            return Err(TransferError::IntegrityMismatch);
        }
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.session_closed {
            return Err(TransferError::SessionClosed);
        }
        let candidate = writer.candidate.take().ok_or(TransferError::Internal)?;
        writer.file.take();
        writer.phase = WriterPhase::Committing;
        drop(state);
        Ok(WriterCommit {
            registry: self.clone(),
            record: Arc::clone(&record),
            handle: writer.handle.clone(),
            path: writer.path.clone(),
            mode: writer.mode,
            executable: writer.executable,
            candidate,
            transferred: writer.transferred,
            prefix_bytes: writer.prefix_bytes,
            prefix_digest: writer.prefix_digest.clone(),
            transfer_digest: digest,
        })
    }

    pub(crate) async fn handle_frame(&self, frame: DataFrame) -> Result<(), TransferError> {
        let record = self.record(&frame.handle)?;
        let result = match record.clone() {
            TransferRecord::Reader(reader) => self.handle_reader_frame(reader, frame).await,
            TransferRecord::Writer(writer) => self.handle_writer_frame(writer, frame).await,
        };
        if result.is_err() {
            self.reset_record(&record).await;
        }
        result
    }

    pub(crate) async fn reconcile_committing(&self) {
        let records = {
            let state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.records.values().cloned().collect::<Vec<_>>()
        };
        let mut reconciled = Vec::new();
        for record in records {
            if let TransferRecord::Writer(writer) = record {
                let mut writer = writer.lock().await;
                if writer.phase == WriterPhase::Committing {
                    writer.phase = WriterPhase::UnknownOutcome;
                    reconciled.push(writer.handle.clone());
                }
            }
        }
        for handle in reconciled {
            self.mark_terminal(&handle);
        }
    }

    pub(crate) async fn close_session(&self) {
        let records = {
            let mut state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.session_closed = true;
            state.records.values().cloned().collect::<Vec<_>>()
        };
        let mut operation_owned = BTreeSet::new();
        for record in records {
            match record {
                TransferRecord::Reader(reader) => {
                    let mut reader = reader.lock().await;
                    if reader.phase != ReaderPhase::Closed {
                        reader.cancellation.send_replace(true);
                        reader.phase = ReaderPhase::Closed;
                    }
                }
                TransferRecord::Writer(writer) => {
                    let mut writer = writer.lock().await;
                    if writer.phase == WriterPhase::Committing {
                        operation_owned.insert(writer.handle.clone());
                    } else if !matches!(
                        writer.phase,
                        WriterPhase::Committed | WriterPhase::UnknownOutcome
                    ) {
                        writer.phase = WriterPhase::Aborted;
                        let _ = self.discard_writer_candidate(&mut writer);
                    }
                }
            }
        }
        {
            let mut state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state
                .records
                .retain(|handle, _| operation_owned.contains(handle));
            state.detached_handles.extend(operation_owned);
            let retained = state.records.keys().cloned().collect::<BTreeSet<_>>();
            state
                .terminal_order
                .retain(|(handle, _)| retained.contains(handle));
        }
        self.inner
            .outbound
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
        self.wait_for_producers().await;
    }

    async fn handle_reader_frame(
        &self,
        record: Arc<Mutex<ReaderRecord>>,
        frame: DataFrame,
    ) -> Result<(), TransferError> {
        match frame.kind {
            DataFrameKind::Attach => {
                let mut reader = record.lock().await;
                if reader.phase != ReaderPhase::Open || frame.offset != 0 {
                    return Err(TransferError::Protocol);
                }
                if expired(reader.expires_at, reader.last_progress, self.inner.idle_ttl) {
                    reader.phase = ReaderPhase::Reset;
                    return Err(TransferError::Expired);
                }
                let file = reader.file.take().ok_or(TransferError::Internal)?;
                reader.phase = ReaderPhase::Streaming;
                reader.last_progress = Instant::now();
                let handle = reader.handle.clone();
                let mut cancellation = reader.cancellation.subscribe();
                drop(reader);
                self.send(DataFrame {
                    kind: DataFrameKind::Attached,
                    handle: handle.clone(),
                    offset: 0,
                    payload: Vec::new(),
                    reset_status: None,
                })
                .await?;
                let registry = self.clone();
                let producer = self.track_producer();
                tokio::spawn(async move {
                    let _producer = producer;
                    registry
                        .produce_reader(record, file, handle, &mut cancellation)
                        .await;
                });
                Ok(())
            }
            DataFrameKind::EndAck => Err(TransferError::Protocol),
            DataFrameKind::Reset => {
                let mut reader = record.lock().await;
                if matches!(reader.phase, ReaderPhase::Reset | ReaderPhase::Closed) {
                    return Ok(());
                }
                reader.cancellation.send_replace(true);
                reader.phase = ReaderPhase::Reset;
                let handle = reader.handle.clone();
                let offset = reader.produced;
                drop(reader);
                self.mark_terminal(&handle);
                self.send(DataFrame {
                    kind: DataFrameKind::Reset,
                    handle,
                    offset,
                    payload: Vec::new(),
                    reset_status: frame.reset_status.or(Some(DataResetStatus::Cancelled)),
                })
                .await
            }
            _ => Err(TransferError::Protocol),
        }
    }

    async fn produce_reader(
        &self,
        record: Arc<Mutex<ReaderRecord>>,
        file: std::fs::File,
        handle: String,
        cancellation: &mut watch::Receiver<bool>,
    ) {
        let (start_offset, max_bytes) = {
            let reader = record.lock().await;
            (reader.start_offset, reader.max_bytes)
        };
        let mut file = tokio::fs::File::from_std(file);
        if file.seek(SeekFrom::Start(start_offset)).await.is_err() {
            self.reset_reader(&record, &handle, DataResetStatus::Source)
                .await;
            return;
        }
        let payload_limit = self
            .inner
            .max_frame_bytes
            .saturating_sub(crate::eip::EIP_DATA_FRAME_HEADER_BYTES + handle.len())
            .max(1);
        let mut buffer = vec![0_u8; payload_limit.min(64 * 1024)];
        let mut offset = 0_u64;
        let mut hasher = Sha256::new();
        while offset < max_bytes {
            let remaining = usize::try_from((max_bytes - offset).min(buffer.len() as u64))
                .unwrap_or(buffer.len());
            let read = match file.read(&mut buffer[..remaining]).await {
                Ok(0) => break,
                Err(_) => {
                    self.reset_reader(&record, &handle, DataResetStatus::Source)
                        .await;
                    return;
                }
                Ok(read) => read,
            };
            let frame = DataFrame {
                kind: DataFrameKind::Chunk,
                handle: handle.clone(),
                offset,
                payload: buffer[..read].to_vec(),
                reset_status: None,
            };
            tokio::select! {
                biased;
                changed = cancellation.changed() => {
                    let _ = changed;
                    if !self.session_closed() {
                        self.reset_reader(&record, &handle, DataResetStatus::Cancelled).await;
                    }
                    return;
                }
                result = self.send(frame) => {
                    if result.is_err() {
                        self.reset_reader(&record, &handle, DataResetStatus::Internal).await;
                        return;
                    }
                }
            }
            hasher.update(&buffer[..read]);
            offset += read as u64;
            let mut reader = record.lock().await;
            reader.produced = offset;
            reader.last_progress = Instant::now();
        }
        {
            let mut reader = record.lock().await;
            reader.digest = Some(ContentDigest {
                algorithm: "sha256".to_owned(),
                value: format!("{:x}", hasher.finalize()),
            });
            reader.phase = ReaderPhase::AwaitingAck;
            reader.last_progress = Instant::now();
        }
        let end = DataFrame {
            kind: DataFrameKind::End,
            handle: handle.clone(),
            offset,
            payload: Vec::new(),
            reset_status: None,
        };
        tokio::select! {
            biased;
            changed = cancellation.changed() => {
                let _ = changed;
                if !self.session_closed() {
                    self.reset_reader(&record, &handle, DataResetStatus::Cancelled).await;
                }
            }
            result = self.send(end) => {
                if result.is_err() {
                    record.lock().await.phase = ReaderPhase::Reset;
                }
            }
        }
    }

    async fn reset_reader(
        &self,
        record: &Arc<Mutex<ReaderRecord>>,
        handle: &str,
        status: DataResetStatus,
    ) {
        let offset = {
            let mut reader = record.lock().await;
            reader.phase = ReaderPhase::Reset;
            reader.produced
        };
        let _ = self
            .send(DataFrame {
                kind: DataFrameKind::Reset,
                handle: handle.to_owned(),
                offset,
                payload: Vec::new(),
                reset_status: Some(status),
            })
            .await;
        self.mark_terminal(handle);
    }

    async fn handle_writer_frame(
        &self,
        record: Arc<Mutex<WriterRecord>>,
        frame: DataFrame,
    ) -> Result<(), TransferError> {
        let mut writer = record.lock().await;
        let operation_owned = matches!(
            writer.phase,
            WriterPhase::Committing | WriterPhase::Committed | WriterPhase::UnknownOutcome
        );
        if !operation_owned && expired(writer.expires_at, writer.last_progress, self.inner.idle_ttl)
        {
            writer.phase = WriterPhase::Aborted;
            return Err(TransferError::Expired);
        }
        match frame.kind {
            DataFrameKind::Attach if writer.phase == WriterPhase::Open && frame.offset == 0 => {
                writer.phase = WriterPhase::Receiving;
                writer.last_progress = Instant::now();
                let handle = writer.handle.clone();
                drop(writer);
                self.send(DataFrame {
                    kind: DataFrameKind::Attached,
                    handle,
                    offset: 0,
                    payload: Vec::new(),
                    reset_status: None,
                })
                .await
            }
            DataFrameKind::Chunk
                if writer.phase == WriterPhase::Receiving && frame.offset == writer.transferred =>
            {
                let next = writer
                    .transferred
                    .checked_add(frame.payload.len() as u64)
                    .ok_or(TransferError::Limit)?;
                if next > writer.max_transfer_bytes {
                    writer.phase = WriterPhase::Aborted;
                    return Err(TransferError::Limit);
                }
                writer
                    .candidate
                    .as_mut()
                    .ok_or(TransferError::Internal)?
                    .reserve_bytes(frame.payload.len() as u64)
                    .map_err(map_mount_error)?;
                let Some(file) = writer.file.as_mut() else {
                    return Err(TransferError::Internal);
                };
                if file.write_all(&frame.payload).await.is_err() {
                    writer.phase = WriterPhase::Aborted;
                    return Err(TransferError::Source);
                }
                writer.hasher.update(&frame.payload);
                writer.transferred = next;
                writer.last_progress = Instant::now();
                Ok(())
            }
            DataFrameKind::End
                if writer.phase == WriterPhase::Receiving && frame.offset == writer.transferred =>
            {
                let Some(file) = writer.file.as_mut() else {
                    return Err(TransferError::Internal);
                };
                file.flush().await.map_err(|_| TransferError::Source)?;
                file.sync_all().await.map_err(|_| TransferError::Source)?;
                let digest = ContentDigest {
                    algorithm: "sha256".to_owned(),
                    value: format!("{:x}", writer.hasher.clone().finalize()),
                };
                writer.digest = Some(digest);
                writer.phase = WriterPhase::Sealed;
                writer.last_progress = Instant::now();
                let handle = writer.handle.clone();
                let offset = writer.transferred;
                drop(writer);
                self.send(DataFrame {
                    kind: DataFrameKind::EndAck,
                    handle,
                    offset,
                    payload: Vec::new(),
                    reset_status: None,
                })
                .await
            }
            DataFrameKind::Reset if operation_owned => Ok(()),
            DataFrameKind::Reset => {
                writer.phase = WriterPhase::Aborted;
                self.discard_writer_candidate(&mut writer)?;
                let handle = writer.handle.clone();
                let offset = writer.transferred;
                drop(writer);
                self.mark_terminal(&handle);
                self.send(DataFrame {
                    kind: DataFrameKind::Reset,
                    handle,
                    offset,
                    payload: Vec::new(),
                    reset_status: frame.reset_status.or(Some(DataResetStatus::Cancelled)),
                })
                .await
            }
            _ => Err(TransferError::Protocol),
        }
    }

    fn track_producer(&self) -> ProducerGuard {
        self.inner.active_producers.fetch_add(1, Ordering::AcqRel);
        ProducerGuard {
            inner: Arc::clone(&self.inner),
        }
    }

    async fn wait_for_producers(&self) {
        while self.inner.active_producers.load(Ordering::Acquire) != 0 {
            let notified = self.inner.producers_idle.notified();
            if self.inner.active_producers.load(Ordering::Acquire) == 0 {
                return;
            }
            notified.await;
        }
    }

    fn session_closed(&self) -> bool {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .session_closed
    }

    async fn send(&self, frame: DataFrame) -> Result<(), TransferError> {
        let sender = self
            .inner
            .outbound
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
            .ok_or(TransferError::Internal)?;
        sender
            .send(frame)
            .await
            .map_err(|_| TransferError::Internal)
    }

    async fn reset_record(&self, record: &TransferRecord) {
        match record {
            TransferRecord::Reader(reader) => {
                let handle = {
                    let mut reader = reader.lock().await;
                    if reader.phase != ReaderPhase::Closed {
                        reader.cancellation.send_replace(true);
                        reader.phase = ReaderPhase::Reset;
                    }
                    reader.handle.clone()
                };
                self.mark_terminal(&handle);
            }
            TransferRecord::Writer(writer) => {
                let handle = {
                    let mut writer = writer.lock().await;
                    if matches!(
                        writer.phase,
                        WriterPhase::Committing
                            | WriterPhase::Committed
                            | WriterPhase::UnknownOutcome
                    ) {
                        return;
                    }
                    writer.phase = WriterPhase::Aborted;
                    let _ = self.discard_writer_candidate(&mut writer);
                    writer.handle.clone()
                };
                self.mark_terminal(&handle);
            }
        }
    }

    pub(crate) async fn expire(&self) {
        let records = {
            let state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.records.values().cloned().collect::<Vec<_>>()
        };
        for record in records {
            match record {
                TransferRecord::Reader(reader) => {
                    let terminal = {
                        let mut reader = reader.lock().await;
                        if matches!(reader.phase, ReaderPhase::Closed | ReaderPhase::Reset)
                            || !expired(
                                reader.expires_at,
                                reader.last_progress,
                                self.inner.idle_ttl,
                            )
                        {
                            None
                        } else {
                            reader.cancellation.send_replace(true);
                            reader.phase = ReaderPhase::Reset;
                            Some(reader.handle.clone())
                        }
                    };
                    if let Some(handle) = terminal {
                        self.mark_terminal(&handle);
                    }
                }
                TransferRecord::Writer(writer) => {
                    let terminal = {
                        let mut writer = writer.lock().await;
                        if matches!(
                            writer.phase,
                            WriterPhase::Committing
                                | WriterPhase::Committed
                                | WriterPhase::UnknownOutcome
                                | WriterPhase::Aborted
                        ) || !expired(
                            writer.expires_at,
                            writer.last_progress,
                            self.inner.idle_ttl,
                        ) {
                            None
                        } else {
                            writer.phase = WriterPhase::Aborted;
                            let _ = self.discard_writer_candidate(&mut writer);
                            Some(writer.handle.clone())
                        }
                    };
                    if let Some(handle) = terminal {
                        self.mark_terminal(&handle);
                    }
                }
            }
        }
    }

    fn discard_writer_candidate(&self, writer: &mut WriterRecord) -> Result<(), TransferError> {
        writer.file.take();
        if let Some(candidate) = writer.candidate.take() {
            candidate
                .delete()
                .map_err(|_| TransferError::CleanupFailed)?;
        }
        Ok(())
    }

    fn operation_timeout(&self, requested_ms: Option<u64>) -> Result<Duration, TransferError> {
        let requested = requested_ms
            .map(Duration::from_millis)
            .unwrap_or(self.inner.max_operation_duration);
        if requested.is_zero() {
            return Err(TransferError::Timeout);
        }
        Ok(requested.min(self.inner.max_operation_duration))
    }

    fn transfer_expiry(
        &self,
        requested_ms: Option<u64>,
    ) -> Result<chrono::DateTime<chrono::Utc>, TransferError> {
        let requested = requested_ms
            .map(Duration::from_millis)
            .unwrap_or(self.inner.max_duration)
            .min(self.inner.max_duration);
        if requested.is_zero() {
            return Err(TransferError::Expired);
        }
        chrono::Duration::from_std(requested)
            .map(|duration| chrono::Utc::now() + duration)
            .map_err(|_| TransferError::Internal)
    }

    fn reserve_record(&self) -> Result<TransferReservation, TransferError> {
        let now = Instant::now();
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.prune(now, self.inner.terminal_ttl);
        if state.session_closed {
            return Err(TransferError::SessionClosed);
        }
        let terminal = state.terminal_order.len();
        let active = state
            .records
            .len()
            .saturating_sub(terminal)
            .saturating_add(state.reservations);
        if active >= self.inner.max_active {
            return Err(TransferError::Busy);
        }
        while state.records.len().saturating_add(state.reservations) >= self.inner.max_records {
            if !state.reclaim_terminal() {
                return Err(TransferError::Busy);
            }
        }
        state.reservations = state
            .reservations
            .checked_add(1)
            .ok_or(TransferError::Internal)?;
        Ok(TransferReservation {
            registry: self.clone(),
            active: true,
        })
    }

    fn record(&self, handle: &str) -> Result<TransferRecord, TransferError> {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.detached_handles.contains(handle) {
            return Err(TransferError::InvalidHandle);
        }
        state
            .records
            .get(handle)
            .cloned()
            .ok_or(TransferError::InvalidHandle)
    }

    pub(crate) fn has_active(&self) -> bool {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.records.len() > state.terminal_order.len() || state.reservations > 0
    }

    fn mark_terminal(&self, handle: &str) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.records.contains_key(handle)
            && !state
                .terminal_order
                .iter()
                .any(|(terminal, _)| terminal == handle)
        {
            state
                .terminal_order
                .push_back((handle.to_owned(), Instant::now()));
        }
    }
}

impl TransferReservation {
    fn insert(mut self, handle: String, record: TransferRecord) -> Result<(), TransferError> {
        let mut state = self
            .registry
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.session_closed {
            return Err(TransferError::SessionClosed);
        }
        if state.records.contains_key(&handle) {
            return Err(TransferError::Conflict);
        }
        state.reservations = state
            .reservations
            .checked_sub(1)
            .expect("active transfer reservation is consumed exactly once");
        state.records.insert(handle, record);
        self.active = false;
        Ok(())
    }
}

impl Drop for TransferReservation {
    fn drop(&mut self) {
        if !self.active {
            return;
        }
        let mut state = self
            .registry
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.reservations = state
            .reservations
            .checked_sub(1)
            .expect("active transfer reservation is released exactly once");
    }
}

impl WriterCommit {
    pub(crate) async fn execute(
        self,
        operations: OperationLedger,
        operation_id: String,
    ) -> Result<WriterCommitOutput, TransferError> {
        let registry = self.registry.clone();
        let record = Arc::clone(&self.record);
        let handle = self.handle.clone();
        match tokio::task::spawn_blocking(move || {
            let result = self.execute_sync(&operations, &operation_id);
            {
                let mut writer = record.blocking_lock();
                writer.phase = match &result {
                    Ok(_) => WriterPhase::Committed,
                    Err(TransferError::UnknownOutcome) => WriterPhase::UnknownOutcome,
                    Err(_) => WriterPhase::Aborted,
                };
            }
            registry.mark_terminal(&handle);
            result
        })
        .await
        {
            Ok(result) => result,
            Err(_) => Err(TransferError::Internal),
        }
    }

    fn execute_sync(
        mut self,
        operations: &OperationLedger,
        operation_id: &str,
    ) -> Result<WriterCommitOutput, TransferError> {
        check_operation(operations, operation_id)?;
        let expected_size = self
            .prefix_bytes
            .checked_add(self.transferred)
            .ok_or(TransferError::Limit)?;
        if let Some(executable) = self.executable {
            set_executable(&self.candidate.file, executable).map_err(|_| TransferError::Denied)?;
        }
        if !self
            .candidate
            .verify_complete(expected_size)
            .map_err(|_| TransferError::Source)?
        {
            return Err(TransferError::IntegrityMismatch);
        }
        self.candidate
            .file
            .seek(SeekFrom::Start(0))
            .map_err(|_| TransferError::Source)?;
        if self.mode == FileWriteMode::Append {
            let expected_prefix = self
                .prefix_digest
                .as_ref()
                .ok_or(TransferError::IntegrityMismatch)?;
            if expected_prefix.algorithm != "sha256"
                || hash_exact(
                    &mut self.candidate.file,
                    self.prefix_bytes,
                    operations,
                    operation_id,
                )? != expected_prefix.value
            {
                return Err(TransferError::IntegrityMismatch);
            }
        } else if self.prefix_bytes != 0 || self.prefix_digest.is_some() {
            return Err(TransferError::IntegrityMismatch);
        }
        if self.transfer_digest.algorithm != "sha256"
            || hash_exact(
                &mut self.candidate.file,
                self.transferred,
                operations,
                operation_id,
            )? != self.transfer_digest.value
        {
            return Err(TransferError::IntegrityMismatch);
        }
        check_operation(operations, operation_id)?;
        let mount = Arc::clone(self.candidate.mount());
        let current = observe_destination(&mount, &self.path)?;
        validate_commit_mode(self.mode, current.as_ref())?;
        check_operation(operations, operation_id)?;
        let replace = match self.mode {
            FileWriteMode::Create => false,
            FileWriteMode::Replace | FileWriteMode::Append | FileWriteMode::Upsert => true,
        };
        mount
            .publish_candidate(&mut self.candidate, &self.path, replace)
            .map_err(map_mount_error)?;
        let opened = mount
            .open_regular(&self.path)
            .map_err(|_| TransferError::UnknownOutcome)?;
        Ok(WriterCommitOutput {
            info: file_info(&self.path, &opened.metadata),
            transferred_bytes: self.transferred,
            transfer_digest: self.transfer_digest.clone(),
        })
    }
}

impl TransferState {
    fn prune(&mut self, now: Instant, ttl: Duration) {
        while self
            .terminal_order
            .front()
            .is_some_and(|(_, completed)| now.duration_since(*completed) >= ttl)
        {
            if let Some((handle, _)) = self.terminal_order.pop_front() {
                self.records.remove(&handle);
                self.detached_handles.remove(&handle);
            }
        }
    }

    fn reclaim_terminal(&mut self) -> bool {
        while let Some((handle, _)) = self.terminal_order.pop_front() {
            self.detached_handles.remove(&handle);
            if self.records.remove(&handle).is_some() {
                return true;
            }
        }
        false
    }
}

fn hash_exact(
    file: &mut std::fs::File,
    mut remaining: u64,
    operations: &OperationLedger,
    operation_id: &str,
) -> Result<String, TransferError> {
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    while remaining > 0 {
        check_operation(operations, operation_id)?;
        let limit = usize::try_from(remaining.min(buffer.len() as u64)).unwrap_or(buffer.len());
        let read = file
            .read(&mut buffer[..limit])
            .map_err(|_| TransferError::Source)?;
        if read == 0 {
            return Err(TransferError::IntegrityMismatch);
        }
        hasher.update(&buffer[..read]);
        remaining -= read as u64;
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn check_operation(operations: &OperationLedger, operation_id: &str) -> Result<(), TransferError> {
    match operations.interruption(operation_id) {
        Some(OperationInterruption::Cancelled) => Err(TransferError::Cancelled),
        Some(OperationInterruption::TimedOut) => Err(TransferError::Timeout),
        None => Ok(()),
    }
}

fn validate_open_mode(
    mode: FileWriteMode,
    current: Option<&std::fs::Metadata>,
) -> Result<(), TransferError> {
    match mode {
        FileWriteMode::Create if current.is_some() => Err(TransferError::Conflict),
        FileWriteMode::Replace | FileWriteMode::Append if current.is_none() => {
            Err(TransferError::NotFound)
        }
        _ => Ok(()),
    }
}

fn validate_commit_mode(
    mode: FileWriteMode,
    current: Option<&std::fs::Metadata>,
) -> Result<(), TransferError> {
    match mode {
        FileWriteMode::Create if current.is_some() => Err(TransferError::Conflict),
        FileWriteMode::Replace | FileWriteMode::Append if current.is_none() => {
            Err(TransferError::Conflict)
        }
        _ => Ok(()),
    }
}

fn observe_destination(
    mount: &Arc<Mount>,
    path: &EIPPath,
) -> Result<Option<std::fs::Metadata>, TransferError> {
    match mount.metadata(path, false) {
        Ok(metadata) if metadata.is_symlink() => return Err(TransferError::Denied),
        Ok(metadata) if !metadata.is_file() => return Err(TransferError::Denied),
        Ok(_) => {}
        Err(MountPathError::NotFound) => return Ok(None),
        Err(error) => return Err(map_mount_error(error)),
    }
    let opened = mount.open_regular(path).map_err(map_mount_error)?;
    Ok(Some(opened.metadata))
}

pub(crate) fn file_info(path: &EIPPath, metadata: &std::fs::Metadata) -> FileInfo {
    let kind = if metadata.is_file() {
        FileKind::File
    } else if metadata.is_dir() {
        FileKind::Directory
    } else if metadata.file_type().is_symlink() {
        FileKind::Symlink
    } else {
        FileKind::Other
    };
    FileInfo {
        path: path.clone(),
        kind,
        size_bytes: metadata.is_file().then_some(metadata.len()),
        modified_at: metadata.modified().ok().map(chrono::DateTime::from),
        executable: executable(metadata),
    }
}

#[cfg(unix)]
fn executable(metadata: &std::fs::Metadata) -> Option<bool> {
    use std::os::unix::fs::PermissionsExt;
    metadata
        .is_file()
        .then(|| metadata.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn executable(metadata: &std::fs::Metadata) -> Option<bool> {
    metadata.is_file().then_some(false)
}

#[cfg(unix)]
fn set_executable(file: &std::fs::File, executable: bool) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut permissions = file.metadata()?.permissions();
    let mut mode = permissions.mode();
    mode = if executable {
        mode | 0o100
    } else {
        mode & !0o111
    };
    permissions.set_mode(mode);
    file.set_permissions(permissions)
}

#[cfg(not(unix))]
fn set_executable(_file: &std::fs::File, executable: bool) -> std::io::Result<()> {
    if executable {
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "executable bits are unsupported",
        ))
    } else {
        Ok(())
    }
}

#[cfg(unix)]
fn set_permissions_from(file: &std::fs::File, metadata: &std::fs::Metadata) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    file.set_permissions(std::fs::Permissions::from_mode(
        metadata.permissions().mode() & 0o777,
    ))
}

#[cfg(not(unix))]
fn set_permissions_from(
    _file: &std::fs::File,
    _metadata: &std::fs::Metadata,
) -> std::io::Result<()> {
    Ok(())
}

fn expired(
    absolute: chrono::DateTime<chrono::Utc>,
    last_progress: Instant,
    idle_ttl: Duration,
) -> bool {
    absolute <= chrono::Utc::now() || last_progress.elapsed() >= idle_ttl
}

fn map_mount_error(error: MountPathError) -> TransferError {
    match error {
        MountPathError::Invalid => TransferError::Protocol,
        MountPathError::Denied | MountPathError::NotRegular => TransferError::Denied,
        MountPathError::NotFound => TransferError::NotFound,
        MountPathError::AlreadyExists => TransferError::Conflict,
        MountPathError::Quota => TransferError::Quota,
        MountPathError::Unsupported => TransferError::Unsupported,
        MountPathError::UnknownOutcome => TransferError::UnknownOutcome,
        MountPathError::Io => TransferError::Source,
        MountPathError::Internal => TransferError::Internal,
    }
}

fn map_ledger_error(_error: LedgerError) -> TransferError {
    TransferError::Internal
}

#[cfg(test)]
mod tests {
    use std::{fs, path::PathBuf, sync::atomic::Ordering, time::Duration};

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use std::io::{Seek, Write};

    use sha2::{Digest, Sha256};
    use tokio::sync::mpsc;

    use crate::{
        config::{Config, TrustedMountConfig},
        eip::{
            DataFrame, DataFrameKind, EIPCallContext, EIPPath, FileByteRange, FileReaderOpenParams,
        },
        mount::MountRegistry,
        operation::random_selector,
    };
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use crate::{
        eip::{FileWriteMode, FileWriterCommitParams, FileWriterOpenParams},
        operation::OperationLedger,
    };

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use super::{ContentDigest, FileWriterAbortStatus, TransferRecord, WriterPhase};
    use super::{TransferError, TransferRegistry};

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir().join(
                random_selector("agent-envd-transfer-test").expect("random temporary directory"),
            );
            fs::create_dir(&path).expect("creates temporary directory");
            Self(path)
        }

        fn child(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for TempTree {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn context(operation_id: &str) -> EIPCallContext {
        EIPCallContext {
            operation_id: operation_id.to_owned(),
            timeout_ms: None,
        }
    }

    fn path(value: &str) -> EIPPath {
        EIPPath {
            mount_id: "workspace".to_owned(),
            path: value.to_owned(),
        }
    }

    fn candidate_count(tree: &TempTree) -> usize {
        fs::read_dir(tree.child("native"))
            .expect("lists native root")
            .filter_map(Result::ok)
            .filter(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".eip-stage-")
            })
            .count()
    }

    fn setup(
        writable: bool,
        idle_ttl_ms: u64,
    ) -> (
        TempTree,
        Config,
        MountRegistry,
        TransferRegistry,
        mpsc::Receiver<DataFrame>,
    ) {
        let tree = TempTree::new();
        let native_root = tree.child("native");
        fs::create_dir(&native_root).expect("creates native root");
        let mut config = Config::for_test("env-transfer-test");
        config.limits.max_staged_file_objects = 1;
        config.limits.max_concurrent_file_transfers = 1;
        config.limits.file_transfer_idle_ttl_ms = idle_ttl_ms;
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root,
            writable,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: if writable {
                vec!["open_reader".to_owned(), "open_writer".to_owned()]
            } else {
                vec!["open_reader".to_owned()]
            },
        });
        let mounts = MountRegistry::initialize_scoped(&config).expect("initializes mount registry");
        let transfers = TransferRegistry::new(&config, 1).expect("initializes transfer registry");
        let (sender, receiver) = mpsc::channel(16);
        transfers
            .begin_session(sender)
            .expect("begins transfer session");
        (tree, config, mounts, transfers, receiver)
    }

    #[tokio::test]
    async fn transfer_reservation_is_atomic_and_rolls_back_on_drop() {
        let (_tree, _config, _mounts, transfers, _outbound) = setup(false, 60_000);
        let reservation = transfers.reserve_record().expect("reserves transfer slot");
        assert!(transfers.has_active());
        assert!(matches!(
            transfers.reserve_record(),
            Err(TransferError::Busy)
        ));

        drop(reservation);
        assert!(!transfers.has_active());
        let replacement = transfers
            .reserve_record()
            .expect("released reservation admits later work");
        drop(replacement);
        assert!(!transfers.has_active());
    }

    #[tokio::test]
    async fn reader_close_is_the_only_terminal_acceptance_action() {
        let (tree, _config, mounts, transfers, mut outbound) = setup(false, 60_000);
        let content = b"reader-content";
        fs::write(tree.child("native/source.bin"), content).expect("writes source");
        let opened = transfers
            .open_reader(
                &mounts,
                &FileReaderOpenParams {
                    context: context("open-reader"),
                    path: path("/source.bin"),
                    byte_range: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens reader");

        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Attach,
                handle: opened.reader.0.clone(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("attaches reader");
        assert_eq!(
            outbound.recv().await.expect("attached frame").kind,
            DataFrameKind::Attached
        );
        let mut received = Vec::new();
        loop {
            let frame = outbound.recv().await.expect("reader frame");
            match frame.kind {
                DataFrameKind::Chunk => {
                    assert_eq!(frame.offset, received.len() as u64);
                    received.extend(frame.payload);
                }
                DataFrameKind::End => {
                    assert_eq!(frame.offset, received.len() as u64);
                    break;
                }
                other => panic!("unexpected reader frame: {other:?}"),
            }
        }
        assert_eq!(received, content);
        let completion = transfers
            .close_reader(&opened.reader)
            .await
            .expect("closes complete reader")
            .completion;
        assert_eq!(completion.produced_bytes, content.len() as u64);
        assert_eq!(
            completion.digest.value,
            format!("{:x}", Sha256::digest(content))
        );
    }

    #[tokio::test]
    async fn session_close_waits_for_detached_reader_producers() {
        let (tree, _config, mounts, transfers, mut outbound) = setup(false, 60_000);
        fs::write(tree.child("native/source.bin"), vec![b'x'; 1024 * 1024])
            .expect("writes a source that fills the outbound queue");
        let opened = transfers
            .open_reader(
                &mounts,
                &FileReaderOpenParams {
                    context: context("open-reader-for-close"),
                    path: path("/source.bin"),
                    byte_range: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens reader");
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Attach,
                handle: opened.reader.0,
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("attaches reader");
        tokio::time::timeout(Duration::from_secs(1), async {
            while transfers.inner.active_producers.load(Ordering::Acquire) == 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("reader producer starts");

        tokio::time::timeout(Duration::from_secs(1), transfers.close_session())
            .await
            .expect("session close cancels and joins the detached producer");
        assert_eq!(transfers.inner.active_producers.load(Ordering::Acquire), 0);
        while outbound.recv().await.is_some() {}
    }

    #[tokio::test]
    async fn reader_accepts_bounded_ranges_from_files_larger_than_mount_mutation_limit() {
        let (tree, _config, mounts, transfers, _outbound) = setup(false, 60_000);
        let source = fs::File::create(tree.child("native/large.bin")).expect("large source");
        source
            .set_len(2 * 1024 * 1024)
            .expect("extends source beyond the mount mutation limit");

        let opened = transfers
            .open_reader(
                &mounts,
                &FileReaderOpenParams {
                    context: context("open-large-range"),
                    path: path("/large.bin"),
                    byte_range: Some(FileByteRange {
                        offset: 1024 * 1024,
                        length: Some(16),
                    }),
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("explicit bounded range uses the transfer ceiling");
        assert!(!opened.reader.0.is_empty());
    }

    #[tokio::test]
    async fn reader_treats_eof_before_requested_length_as_success() {
        let (tree, _config, mounts, transfers, mut outbound) = setup(false, 60_000);
        let source = tree.child("native/source.bin");
        fs::write(&source, b"abcdefgh").expect("writes source");
        let opened = transfers
            .open_reader(
                &mounts,
                &FileReaderOpenParams {
                    context: context("open-short-reader"),
                    path: path("/source.bin"),
                    byte_range: Some(FileByteRange {
                        offset: 0,
                        length: Some(8),
                    }),
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens bounded reader");
        fs::write(&source, b"abc").expect("shortens source after open");

        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Attach,
                handle: opened.reader.0.clone(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("attaches reader");
        assert_eq!(
            outbound.recv().await.expect("attached frame").kind,
            DataFrameKind::Attached
        );
        let mut received = Vec::new();
        loop {
            let frame = outbound.recv().await.expect("reader frame");
            match frame.kind {
                DataFrameKind::Chunk => received.extend(frame.payload),
                DataFrameKind::End => break,
                other => panic!("unexpected reader frame: {other:?}"),
            }
        }
        assert_eq!(received, b"abc");
        let completion = transfers
            .close_reader(&opened.reader)
            .await
            .expect("short read closes successfully")
            .completion;
        assert_eq!(completion.produced_bytes, 3);
        assert_eq!(
            completion.digest.value,
            format!("{:x}", Sha256::digest(b"abc"))
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn writer_commit_is_atomic_and_reset_releases_staging_quota() {
        let (tree, _config, mounts, transfers, mut outbound) = setup(true, 60_000);
        let missing_mount = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("missing-mount"),
                    path: EIPPath {
                        mount_id: "missing".to_owned(),
                        path: "/denied.bin".to_owned(),
                    },
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await;
        assert_eq!(missing_mount, Err(TransferError::Denied));

        let opened = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("open-writer"),
                    path: path("/target.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens writer after denied open released quota");
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Attach,
                handle: opened.writer.0.clone(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("attaches writer");
        assert_eq!(
            outbound.recv().await.expect("attached frame").kind,
            DataFrameKind::Attached
        );
        let content = b"writer-content";
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Chunk,
                handle: opened.writer.0.clone(),
                offset: 0,
                payload: content.to_vec(),
                reset_status: None,
            })
            .await
            .expect("uploads chunk");
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::End,
                handle: opened.writer.0.clone(),
                offset: content.len() as u64,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("seals writer");
        assert_eq!(
            outbound.recv().await.expect("end ack").kind,
            DataFrameKind::EndAck
        );
        let digest = ContentDigest {
            algorithm: "sha256".to_owned(),
            value: format!("{:x}", Sha256::digest(content)),
        };
        let operations = OperationLedger::new(
            "env-test".to_owned(),
            7,
            16,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let writer = opened.writer;
        let commit = transfers
            .prepare_commit(&FileWriterCommitParams {
                context: context("commit-writer"),
                writer: writer.clone(),
                transferred_bytes: content.len() as u64,
                transfer_digest: digest.clone(),
            })
            .await
            .expect("prepares commit");
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Reset,
                handle: writer.0.clone(),
                offset: content.len() as u64,
                payload: Vec::new(),
                reset_status: Some(crate::eip::DataResetStatus::Cancelled),
            })
            .await
            .expect("operation-owned writer ignores session reset");
        assert_eq!(
            transfers
                .abort_writer(&writer)
                .await
                .expect("observes commit handoff"),
            FileWriterAbortStatus::CommitInProgress
        );
        let commit_result = commit.execute(operations, "commit-writer".to_owned()).await;
        let commit = commit_result.unwrap_or_else(|error| {
            let native = fs::read_dir(tree.child("native"))
                .expect("lists native root")
                .map(|entry| entry.expect("native entry").file_name())
                .collect::<Vec<_>>();
            panic!("commits writer: {error:?}; native={native:?}")
        });
        assert_eq!(commit.transfer_digest, digest);
        assert_eq!(
            fs::read(tree.child("native/target.bin")).expect("reads target"),
            content
        );

        let reset = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("reset-writer"),
                    path: path("/reset.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens writer after commit released quota");
        let protocol_error = transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Chunk,
                handle: reset.writer.0.clone(),
                offset: 0,
                payload: b"invalid-before-attach".to_vec(),
                reset_status: None,
            })
            .await;
        assert_eq!(protocol_error, Err(TransferError::Protocol));
        assert_eq!(
            transfers
                .abort_writer(&reset.writer)
                .await
                .expect("observes reset writer"),
            FileWriterAbortStatus::AlreadyAborted
        );
        let after_reset = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("after-reset"),
                    path: path("/after-reset.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens writer after reset released quota");
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Reset,
                handle: after_reset.writer.0.clone(),
                offset: 0,
                payload: Vec::new(),
                reset_status: Some(crate::eip::DataResetStatus::Cancelled),
            })
            .await
            .expect("accepts client reset");
        let reset_ack = outbound.recv().await.expect("reset acknowledgement");
        assert_eq!(reset_ack.kind, DataFrameKind::Reset);
        assert_eq!(reset_ack.handle, after_reset.writer.0);
        assert_eq!(
            transfers
                .abort_writer(&after_reset.writer)
                .await
                .expect("observes reset writer"),
            FileWriterAbortStatus::AlreadyAborted
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn session_close_retains_commit_record_until_native_owner_finishes() {
        let (tree, _config, mounts, transfers, _outbound) = setup(true, 60_000);
        let opened = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("close-during-commit-open"),
                    path: path("/close-during-commit.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens writer");
        let digest = ContentDigest {
            algorithm: "sha256".to_owned(),
            value: format!("{:x}", Sha256::digest(b"")),
        };
        let record = match transfers
            .record(&opened.writer.0)
            .expect("writer record exists")
        {
            TransferRecord::Writer(record) => record,
            TransferRecord::Reader(_) => panic!("writer record expected"),
        };
        {
            let mut writer = record.lock().await;
            writer.phase = WriterPhase::Sealed;
            writer.digest = Some(digest.clone());
        }
        let commit = transfers
            .prepare_commit(&FileWriterCommitParams {
                context: context("close-during-commit"),
                writer: opened.writer.clone(),
                transferred_bytes: 0,
                transfer_digest: digest,
            })
            .await
            .expect("hands candidate to commit owner");
        transfers.close_session().await;
        assert!(transfers.has_active());
        transfers.reconcile_committing().await;
        assert!(!transfers.has_active());
        assert_eq!(
            transfers.abort_writer(&opened.writer).await,
            Err(TransferError::InvalidHandle),
            "a new session cannot address the old session's writer handle"
        );

        let operations = OperationLedger::new(
            "env-test".to_owned(),
            7,
            16,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        commit
            .execute(operations, "close-during-commit".to_owned())
            .await
            .expect("native commit finishes after session close");
        assert!(!transfers.has_active());
        assert_eq!(
            fs::read(tree.child("native/close-during-commit.bin")).expect("reads committed file"),
            b""
        );
        assert_eq!(
            transfers
                .open_writer(
                    &mounts,
                    &FileWriterOpenParams {
                        context: context("late-open"),
                        path: path("/late-open.bin"),
                        mode: FileWriteMode::Create,
                        executable: None,
                        transfer_timeout_ms: None,
                    },
                )
                .await,
            Err(TransferError::SessionClosed)
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn session_close_wins_candidate_handoff_when_it_locks_the_writer_first() {
        let (tree, _config, mounts, transfers, _outbound) = setup(true, 60_000);
        let opened = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("close-wins-open"),
                    path: path("/close-wins.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens writer");
        let digest = ContentDigest {
            algorithm: "sha256".to_owned(),
            value: format!("{:x}", Sha256::digest(b"")),
        };
        let record = match transfers
            .record(&opened.writer.0)
            .expect("writer record exists")
        {
            TransferRecord::Writer(record) => record,
            TransferRecord::Reader(_) => panic!("writer record expected"),
        };
        let mut held = record.lock().await;
        held.phase = WriterPhase::Sealed;
        held.digest = Some(digest.clone());

        let close_registry = transfers.clone();
        let closing = tokio::spawn(async move {
            close_registry.close_session().await;
        });
        tokio::task::yield_now().await;
        assert!(!closing.is_finished());

        let prepare_registry = transfers.clone();
        let writer = opened.writer.clone();
        let preparing = tokio::spawn(async move {
            prepare_registry
                .prepare_commit(&FileWriterCommitParams {
                    context: context("close-wins-commit"),
                    writer,
                    transferred_bytes: 0,
                    transfer_digest: digest,
                })
                .await
        });
        tokio::task::yield_now().await;
        drop(held);

        closing.await.expect("session close finishes");
        assert!(matches!(
            preparing.await.expect("prepare task finishes"),
            Err(TransferError::WrongState)
        ));
        assert!(!transfers.has_active());
        assert!(!tree.child("native/close-wins.bin").exists());
        assert_eq!(
            fs::read_dir(tree.child("native"))
                .expect("native directory")
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".eip-stage-"))
                .count(),
            0
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn append_commit_rehashes_the_staged_prefix() {
        let (tree, _config, mounts, transfers, mut outbound) = setup(true, 60_000);
        let target = tree.child("native/append.bin");
        fs::write(&target, b"prefix").expect("writes append target");
        let opened = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("append-open"),
                    path: path("/append.bin"),
                    mode: FileWriteMode::Append,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens append writer");
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Attach,
                handle: opened.writer.0.clone(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("attaches append writer");
        assert_eq!(
            outbound.recv().await.expect("attached frame").kind,
            DataFrameKind::Attached
        );
        let suffix = b"-suffix";
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::Chunk,
                handle: opened.writer.0.clone(),
                offset: 0,
                payload: suffix.to_vec(),
                reset_status: None,
            })
            .await
            .expect("uploads append suffix");
        transfers
            .handle_frame(DataFrame {
                kind: DataFrameKind::End,
                handle: opened.writer.0.clone(),
                offset: suffix.len() as u64,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("seals append writer");
        assert_eq!(
            outbound.recv().await.expect("end ack").kind,
            DataFrameKind::EndAck
        );

        let record = match transfers
            .record(&opened.writer.0)
            .expect("append writer record")
        {
            TransferRecord::Writer(record) => record,
            TransferRecord::Reader(_) => panic!("writer record expected"),
        };
        let mut candidate = {
            let writer = record.lock().await;
            writer
                .candidate
                .as_ref()
                .expect("staged candidate")
                .file
                .try_clone()
                .expect("clones staged candidate")
        };
        candidate
            .seek(std::io::SeekFrom::Start(0))
            .expect("seeks staged prefix");
        candidate.write_all(b"X").expect("corrupts staged prefix");
        candidate.sync_all().expect("syncs corruption");

        let digest = ContentDigest {
            algorithm: "sha256".to_owned(),
            value: format!("{:x}", Sha256::digest(suffix)),
        };
        let operations = OperationLedger::new(
            "env-test".to_owned(),
            7,
            16,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let result = transfers
            .prepare_commit(&FileWriterCommitParams {
                context: context("append-commit"),
                writer: opened.writer,
                transferred_bytes: suffix.len() as u64,
                transfer_digest: digest,
            })
            .await
            .expect("hands off append commit")
            .execute(operations, "append-commit".to_owned())
            .await;
        assert!(matches!(result, Err(TransferError::IntegrityMismatch)));
        assert_eq!(fs::read(target).expect("reads unchanged target"), b"prefix");
    }

    #[tokio::test]
    async fn expired_reader_complete_close_becomes_terminal() {
        let (tree, _config, mounts, transfers, _outbound) = setup(false, 1);
        fs::write(tree.child("native/expired.bin"), b"expired").expect("writes reader source");
        let opened = transfers
            .open_reader(
                &mounts,
                &FileReaderOpenParams {
                    context: context("expiring-reader"),
                    path: path("/expired.bin"),
                    byte_range: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens reader");
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        assert_eq!(
            transfers.close_reader(&opened.reader).await,
            Err(TransferError::Expired)
        );
        assert_eq!(
            transfers.close_reader(&opened.reader).await,
            Err(TransferError::Expired)
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn abort_reports_candidate_cleanup_failure() {
        let (tree, _config, mounts, transfers, _outbound) = setup(true, 60_000);
        let opened = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("cleanup-failure-open"),
                    path: path("/cleanup-failure.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("opens writer");
        let record = match transfers
            .record(&opened.writer.0)
            .expect("writer record exists")
        {
            TransferRecord::Writer(record) => record,
            TransferRecord::Reader(_) => panic!("writer record expected"),
        };
        let candidate_name = record
            .lock()
            .await
            .candidate
            .as_ref()
            .expect("candidate exists")
            .name
            .clone();
        let candidate_path = tree.child("native").join(&candidate_name);
        fs::remove_file(&candidate_path).expect("external actor removes candidate name");
        fs::create_dir(&candidate_path).expect("external actor replaces candidate with directory");

        assert_eq!(
            transfers.abort_writer(&opened.writer).await,
            Err(TransferError::CleanupFailed)
        );
        fs::remove_dir(candidate_path).expect("remove replacement directory");
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn expired_writer_cleanup_returns_candidate_capacity() {
        let (tree, _config, mounts, transfers, _outbound) = setup(true, 1);
        let first = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("expiring-writer"),
                    path: path("/first.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("first writer opens");
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        let second = transfers
            .open_writer(
                &mounts,
                &FileWriterOpenParams {
                    context: context("replacement-writer"),
                    path: path("/second.bin"),
                    mode: FileWriteMode::Create,
                    executable: None,
                    transfer_timeout_ms: None,
                },
            )
            .await
            .expect("expired writer released the single candidate slot");
        assert_eq!(candidate_count(&tree), 1);
        assert_eq!(
            transfers
                .abort_writer(&first.writer)
                .await
                .expect("expired writer remains terminal"),
            FileWriterAbortStatus::AlreadyAborted
        );
        assert_eq!(
            transfers
                .abort_writer(&second.writer)
                .await
                .expect("second writer aborts"),
            FileWriterAbortStatus::Aborted
        );
        assert_eq!(candidate_count(&tree), 0);
    }
}
