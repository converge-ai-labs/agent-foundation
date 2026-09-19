use std::sync::Arc;

use tokio::sync::{OwnedSemaphorePermit, Semaphore};

use crate::config::DaemonLimits;

/// Device totals supplement each Session's own admission limits.
#[derive(Clone)]
pub(crate) struct DeviceCapacity {
    pub(crate) operations: Arc<Semaphore>,
    pub(crate) processes: ResourceCapacity,
    pub(crate) transfers: ResourceCapacity,
}

#[derive(Clone)]
pub(crate) struct ResourceCapacity {
    active: Arc<Semaphore>,
    records: Arc<Semaphore>,
}

pub(crate) struct ResourcePermit {
    pub(crate) active: Option<OwnedSemaphorePermit>,
    _record: OwnedSemaphorePermit,
}

impl DeviceCapacity {
    pub(crate) fn new(limits: &DaemonLimits) -> Self {
        Self {
            operations: Arc::new(Semaphore::new(limits.max_device_operation_records as usize)),
            processes: ResourceCapacity::new(
                limits.max_device_processes,
                limits.max_device_process_records,
            ),
            transfers: ResourceCapacity::new(
                limits.max_device_file_transfers,
                limits.max_device_file_transfer_records,
            ),
        }
    }
}

impl ResourceCapacity {
    fn new(active: u64, records: u64) -> Self {
        Self {
            active: Arc::new(Semaphore::new(active as usize)),
            records: Arc::new(Semaphore::new(records as usize)),
        }
    }

    pub(crate) fn reserve(&self) -> Option<ResourcePermit> {
        Some(ResourcePermit {
            active: Some(self.active.clone().try_acquire_owned().ok()?),
            _record: self.records.clone().try_acquire_owned().ok()?,
        })
    }

    pub(crate) fn available(&self) -> (usize, usize) {
        (
            self.active.available_permits(),
            self.records.available_permits(),
        )
    }

    pub(crate) fn under_pressure(&self) -> bool {
        self.active.available_permits() == 0 || self.records.available_permits() == 0
    }
}
