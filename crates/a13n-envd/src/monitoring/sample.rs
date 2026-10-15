use super::platform;
use crate::eip::{FilesystemObservation, MetricsSample};
use std::{collections::BTreeMap, path::PathBuf, time::Instant};

pub(super) struct Collector {
    filesystems: BTreeMap<String, PathBuf>,
    #[cfg(target_os = "linux")]
    cgroup: Option<PathBuf>,
    sequence: u64,
    system: platform::SystemCollector,
}

impl Collector {
    pub(super) fn new(filesystems: BTreeMap<String, PathBuf>, cgroup: Option<PathBuf>) -> Self {
        #[cfg(not(target_os = "linux"))]
        let _ = cgroup;
        Self {
            filesystems,
            #[cfg(target_os = "linux")]
            cgroup,
            sequence: 0,
            system: platform::SystemCollector::new(),
        }
    }

    pub(super) fn collect(&mut self) -> MetricsSample {
        let started = Instant::now();
        let sampled_at = chrono::Utc::now();
        let system = self.system.collect();
        #[cfg(target_os = "linux")]
        let cgroup_v2 = self.cgroup.as_deref().map(platform::cgroup);
        #[cfg(not(target_os = "linux"))]
        let cgroup_v2 = None;
        let filesystems = self
            .filesystems
            .iter()
            .map(|(name, path)| {
                (
                    name.clone(),
                    FilesystemObservation {
                        metrics: platform::filesystem(path),
                    },
                )
            })
            .collect();
        self.sequence += 1;
        MetricsSample {
            sequence: self.sequence,
            sampled_at,
            sample_age_ms: 0,
            collection_duration_ms: super::elapsed_ms(started),
            system,
            cgroup_v2,
            filesystems,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn missing_targets_remain_named_unavailable_observations() {
        let missing = std::env::temp_dir()
            .join("a13n-monitoring-missing")
            .join("absent");
        let mut collector = Collector::new(
            BTreeMap::from([("workspace".into(), missing.clone())]),
            None,
        );
        let first = collector.collect();
        assert_eq!(first.sequence, 1);
        assert!(first.system.cpu_busy_percent.is_none());
        assert!(first.filesystems["workspace"].metrics.is_none());
        assert!(
            !serde_json::to_string(&first)
                .unwrap()
                .contains("a13n-monitoring-missing")
        );
        assert_eq!(collector.collect().sequence, 2);
    }
}
