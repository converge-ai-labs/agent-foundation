//! Device observations share EIP authentication and outlive individual Sessions.
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tokio::{sync::watch, task::JoinHandle, time::MissedTickBehavior};

use crate::{
    config::{Config, ConfigError},
    eip::{DeviceMetricsResult, MetricsSample, SystemInfo},
};

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "macos")]
mod macos;
pub(crate) mod otlp;
mod projection;
pub(crate) mod prometheus;
mod sample;
#[cfg(target_os = "windows")]
mod windows;
#[cfg(target_os = "linux")]
use linux as platform;
#[cfg(target_os = "macos")]
use macos as platform;
#[cfg(target_os = "windows")]
use windows as platform;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Options {
    #[serde(default = "sample_interval_ms")]
    sample_interval_ms: u64,
    #[serde(default)]
    filesystems: BTreeMap<String, PathBuf>,
    cgroup_v2_directory: Option<PathBuf>,
}

fn sample_interval_ms() -> u64 {
    10_000
}

impl Options {
    pub(crate) fn validate(&mut self) -> Result<(), ConfigError> {
        if !(1_000..=60_000).contains(&self.sample_interval_ms) {
            return Err(ConfigError::new(
                "monitoring.sample_interval_ms must be between 1000 and 60000",
            ));
        }
        if self.cgroup_v2_directory.is_some() && !cfg!(target_os = "linux") {
            return Err(ConfigError::new(
                "monitoring.cgroup_v2_directory requires Linux",
            ));
        }
        if self.filesystems.len() > 16
            || self.filesystems.iter().any(|(name, path)| {
                name.is_empty()
                    || name.len() > 64
                    || !name
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
                    || !path.is_absolute()
            })
        {
            return Err(ConfigError::new(
                "monitoring.filesystems requires at most 16 named absolute paths (names: 1-64 ASCII letters, digits, '_' or '-')",
            ));
        }
        if self
            .cgroup_v2_directory
            .as_ref()
            .is_some_and(|p| !p.is_absolute())
        {
            return Err(ConfigError::new(
                "monitoring.cgroup_v2_directory must be absolute",
            ));
        }
        for path in self
            .filesystems
            .values_mut()
            .chain(self.cgroup_v2_directory.iter_mut())
        {
            if path
                .components()
                .any(|part| part == std::path::Component::ParentDir)
            {
                return Err(ConfigError::new(
                    "monitoring paths must not contain parent traversal",
                ));
            }
            if let Ok(canonical) = std::fs::canonicalize(&*path) {
                *path = canonical;
            }
        }
        Ok(())
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn relocate_sources(
        &mut self,
        root: &std::path::Path,
        runtime_parent: &std::path::Path,
        protected: &[PathBuf],
    ) -> Result<(), ConfigError> {
        for path in self
            .filesystems
            .values_mut()
            .chain(self.cgroup_v2_directory.iter_mut())
        {
            if let Ok(canonical) = std::fs::canonicalize(&*path) {
                *path = canonical;
            }
            if let Ok(relative) = path.strip_prefix(runtime_parent) {
                *path = std::path::Path::new("/state").join(relative);
            } else if protected.iter().any(|hidden| path.starts_with(hidden)) {
                return Err(ConfigError::new(
                    "monitoring targets must not select masked bootstrap paths",
                ));
            } else {
                *path = root.join(
                    path.strip_prefix("/")
                        .expect("validated absolute monitoring path"),
                );
            }
        }
        Ok(())
    }
}

/// Dropped by the outer serve invocation, never by a Session or EIP connection.
pub(crate) struct Runtime {
    sampling: JoinHandle<()>,
    exporter: Option<opentelemetry_sdk::metrics::SdkMeterProvider>,
}
impl Runtime {
    pub(crate) async fn shutdown(mut self) {
        self.sampling.abort();
        if let Some(provider) = self.exporter.take() {
            let deadline = Duration::from_secs(5);
            let result = tokio::time::timeout(
                deadline,
                tokio::task::spawn_blocking(move || provider.shutdown_with_timeout(deadline)),
            )
            .await;
            if !matches!(result, Ok(Ok(Ok(())))) {
                otlp::warning("a13n-envd.monitoring.shutdown_incomplete");
            }
        }
    }
}
impl Drop for Runtime {
    fn drop(&mut self) {
        self.sampling.abort();
    }
}

type Observation = Arc<(Instant, MetricsSample)>;

pub(crate) struct State {
    options: Options,
    latest: watch::Sender<Option<Observation>>,
}

impl State {
    pub(crate) fn new(options: &Options) -> Arc<Self> {
        let (latest, _) = watch::channel(None);
        Arc::new(Self {
            options: options.clone(),
            latest,
        })
    }

    pub(crate) fn start(
        self: &Arc<Self>,
        device_id: &str,
        generation: u64,
        daemon: std::sync::Weak<crate::daemon::Daemon>,
    ) -> Result<Runtime, ConfigError> {
        let (exporter, mut instruments) =
            match otlp::start(self.clone(), device_id.to_owned(), generation)? {
                Some((provider, instruments)) => (Some(provider), Some(instruments)),
                None => (None, None),
            };
        let state = self.clone();
        let sampling = tokio::spawn(async move {
            let mut collector = sample::Collector::new(
                state.options.filesystems.clone(),
                state.options.cgroup_v2_directory.clone(),
            );
            let mut interval =
                tokio::time::interval(Duration::from_millis(state.options.sample_interval_ms));
            interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
            loop {
                interval.tick().await;
                // One native observation at a time. EIP reads only the previous cache.
                let result = tokio::task::spawn_blocking(move || {
                    let observed_at = Instant::now();
                    let snapshot = collector.collect();
                    (collector, observed_at, snapshot)
                })
                .await;
                match result {
                    Ok((next, observed_at, mut snapshot)) => {
                        collector = next;
                        let Some(daemon) = daemon.upgrade() else {
                            break;
                        };
                        snapshot.daemon = daemon.resource_metrics().await;
                        snapshot.collection_duration_ms = elapsed_ms(observed_at);
                        state
                            .latest
                            .send_replace(Some(Arc::new((observed_at, snapshot))));
                        if let Some(instruments) = &mut instruments {
                            instruments.refresh();
                        }
                    }
                    Err(_) => {
                        eprintln!(
                            "{}",
                            serde_json::json!({"level":"warning", "event":"a13n-envd.monitoring.stopped"})
                        );
                        break;
                    }
                }
            }
        });
        Ok(Runtime { sampling, exporter })
    }

    pub(crate) fn report(&self, device_id: &str, generation: u64) -> DeviceMetricsResult {
        let sample = self.latest.borrow().as_ref().map(|observed| {
            let mut sample = observed.1.clone();
            sample.sample_age_ms = elapsed_ms(observed.0);
            sample
        });
        DeviceMetricsResult {
            device_id: device_id.to_owned(),
            generation,
            sample_interval_ms: self.options.sample_interval_ms,
            sample,
        }
    }
}

fn elapsed_ms(start: Instant) -> u64 {
    start.elapsed().as_millis() as u64
}

pub(crate) fn system_info(config: &Config) -> SystemInfo {
    #[cfg(target_os = "linux")]
    let root = if config.managed {
        std::path::Path::new("/original")
    } else {
        std::path::Path::new("/")
    };
    #[cfg(not(target_os = "linux"))]
    let _ = config;
    #[cfg(target_os = "linux")]
    {
        platform::system_info(root)
    }
    #[cfg(not(target_os = "linux"))]
    {
        platform::system_info()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn options_enable_native_monitoring_without_a_listener() {
        let mut options: Options = serde_json::from_value(serde_json::json!({})).unwrap();
        options.validate().unwrap();
        assert_eq!(options.sample_interval_ms, 10_000);
        assert!(
            State::new(&options)
                .report("device-test", 1)
                .sample
                .is_none()
        );
        assert!(
            serde_json::from_value::<Options>(serde_json::json!({"bind":"127.0.0.1:9090"}))
                .is_err()
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn managed_sources_preserve_original_and_runtime_targets() {
        let mut options: Options = serde_json::from_value(serde_json::json!({
            "filesystems":{"workspace":"/workspace", "root":"/", "runtime":"/envd-runtime", "spool":"/envd-runtime/generation-test/spool"}, "cgroup_v2_directory":"/sys/fs/cgroup"
        })).unwrap();
        options
            .relocate_sources(
                std::path::Path::new("/original"),
                std::path::Path::new("/envd-runtime"),
                &[PathBuf::from("/envd-runtime")],
            )
            .unwrap();
        assert_eq!(options.filesystems["runtime"], PathBuf::from("/state"));
        assert_eq!(
            options.filesystems["spool"],
            PathBuf::from("/state/generation-test/spool")
        );
        assert_eq!(
            options.filesystems["workspace"],
            PathBuf::from("/original/workspace")
        );
        assert_eq!(options.filesystems["root"], PathBuf::from("/original"));
        assert_eq!(
            options.cgroup_v2_directory,
            Some(PathBuf::from("/original/sys/fs/cgroup"))
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn managed_sources_reject_masked_bootstrap_paths() {
        let mut options: Options =
            serde_json::from_value(serde_json::json!({"filesystems":{"target":"/private-config"}}))
                .unwrap();
        assert!(
            options
                .relocate_sources(
                    std::path::Path::new("/original"),
                    std::path::Path::new("/runtime"),
                    &[PathBuf::from("/private-config")]
                )
                .is_err()
        );
    }

    #[cfg(not(target_os = "linux"))]
    #[test]
    fn cgroup_configuration_requires_linux() {
        let mut options: Options =
            serde_json::from_value(serde_json::json!({"cgroup_v2_directory":"/sys/fs/cgroup"}))
                .unwrap();
        assert!(
            options
                .validate()
                .unwrap_err()
                .to_string()
                .contains("requires Linux")
        );
    }
}
