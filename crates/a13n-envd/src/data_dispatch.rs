use std::{collections::BTreeMap, sync::Arc, time::Duration};

use tokio::{
    sync::{OwnedSemaphorePermit, Semaphore, mpsc},
    task::JoinSet,
    time::timeout,
};

use crate::{
    config::Config,
    daemon::{Carrier, Daemon},
    eip::{DataFrame, DataFrameKind, EIP_TRANSFER_WINDOW_CHUNKS},
    transfer::{TransferError, reset_status},
};

/// One ordered, bounded inbound lane per Session. Native I/O in one lane never
/// blocks the carrier reader or another Session's control/data dispatch.
pub(crate) struct DataDispatcher {
    daemon: Arc<Daemon>,
    carrier: Arc<Carrier>,
    outbound: mpsc::Sender<DataFrame>,
    lanes: BTreeMap<String, mpsc::Sender<(DataFrame, OwnedSemaphorePermit)>>,
    queued: Arc<Semaphore>,
    workers: JoinSet<()>,
    max_sessions: usize,
    lane_capacity: usize,
    frame_timeout: Duration,
}

impl DataDispatcher {
    pub(crate) fn new(
        daemon: Arc<Daemon>,
        carrier: Arc<Carrier>,
        outbound: mpsc::Sender<DataFrame>,
        config: &Config,
    ) -> Self {
        Self {
            daemon,
            carrier,
            outbound,
            lanes: BTreeMap::new(),
            queued: Arc::new(Semaphore::new(
                config.limits.max_device_file_transfers as usize * (EIP_TRANSFER_WINDOW_CHUNKS + 2),
            )),
            workers: JoinSet::new(),
            max_sessions: config.limits.max_sessions,
            lane_capacity: config.limits.max_concurrent_file_transfers as usize
                * (EIP_TRANSFER_WINDOW_CHUNKS + 2),
            frame_timeout: Duration::from_millis(config.limits.file_transfer_idle_ttl_ms),
        }
    }

    pub(crate) fn enqueue(&mut self, frame: DataFrame) -> Result<(), TransferError> {
        self.daemon
            .validate_data_session(&self.carrier, &frame.session_id)?;
        while self.workers.try_join_next().is_some() {}
        self.lanes.retain(|_, lane| !lane.is_closed());
        if !self.lanes.contains_key(&frame.session_id) {
            if self.lanes.len() >= self.max_sessions {
                return Err(TransferError::Busy);
            }
            let (tx, mut rx) =
                mpsc::channel::<(DataFrame, OwnedSemaphorePermit)>(self.lane_capacity);
            let daemon = self.daemon.clone();
            let carrier = self.carrier.clone();
            let outbound = self.outbound.clone();
            let session_id = frame.session_id.clone();
            let frame_timeout = self.frame_timeout;
            self.workers.spawn(async move {
                loop {
                    let (frame, _queued) = match timeout(Duration::from_secs(1), rx.recv()).await {
                        Ok(Some(frame)) => frame,
                        Ok(None) => break,
                        Err(_) => {
                            if daemon.validate_data_session(&carrier, &session_id).is_err() {
                                break;
                            }
                            continue;
                        }
                    };
                    let result = timeout(
                        frame_timeout,
                        daemon.handle_data_frame(&carrier, frame.clone()),
                    )
                    .await;
                    let error = match result {
                        Ok(Ok(())) => continue,
                        Ok(Err(error)) => error,
                        Err(_) => {
                            daemon.fail_data_session(&carrier, &session_id);
                            TransferError::Timeout
                        }
                    };
                    let reset = DataFrame {
                        kind: DataFrameKind::Reset,
                        session_id: frame.session_id,
                        handle: frame.handle,
                        offset: frame.offset,
                        payload: Vec::new(),
                        reset_status: Some(reset_status(error)),
                    };
                    if !matches!(
                        timeout(frame_timeout, outbound.send(reset)).await,
                        Ok(Ok(()))
                    ) {
                        daemon.fail_data_session(&carrier, &session_id);
                        break;
                    }
                }
            });
            self.lanes.insert(frame.session_id.clone(), tx);
        }
        let session_id = frame.session_id.clone();
        let permit = self.queued.clone().try_acquire_owned();
        let accepted =
            permit.is_ok_and(|permit| self.lanes[&session_id].try_send((frame, permit)).is_ok());
        if !accepted {
            // The byte sequence can no longer be delivered faithfully. Fence only
            // this Session; its existing cleanup owner settles in-flight writes.
            self.daemon.fail_data_session(&self.carrier, &session_id);
            self.lanes.remove(&session_id);
            return Err(TransferError::Busy);
        }
        Ok(())
    }
}
