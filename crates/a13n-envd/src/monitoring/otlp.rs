//! Native export uses SDK instruments/readers and the official OTLP transport.
//! Callbacks only project the daemon cache; they never perform native collection.
use std::{collections::HashSet, sync::Arc, time::Duration};

use opentelemetry::{
    Key, KeyValue,
    metrics::{AsyncInstrument, Meter, MeterProvider},
};
use opentelemetry_otlp::{
    MetricExporter, Protocol, WithExportConfig, WithHttpConfig, WithTonicConfig,
};
use opentelemetry_sdk::{
    Resource,
    error::OTelSdkResult,
    metrics::{
        PeriodicReader, SdkMeterProvider, Temporality, data::ResourceMetrics,
        exporter::PushMetricExporter,
    },
};

use super::{
    State,
    projection::{self, Number, Sink},
};
use crate::{config::ConfigError, eip::DeviceMetricsResult};

/// Envd sampling remains opt-in outside connect. Explicit OTLP selection or an
/// OTLP endpoint also opts in; `monitoring: null` still disables the sampler.
pub(crate) fn enabled() -> Result<bool, ConfigError> {
    selection(|name| std::env::var(name).ok())
}

fn selection(get: impl Fn(&str) -> Option<String>) -> Result<bool, ConfigError> {
    if get("OTEL_SDK_DISABLED").is_some_and(|v| v.eq_ignore_ascii_case("true")) {
        return Ok(false);
    }
    match get("OTEL_METRICS_EXPORTER")
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        Some("none") => Ok(false),
        Some("otlp") => Ok(true),
        None => Ok([
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
        ]
        .into_iter()
        .any(|key| get(key).is_some_and(|v| !v.trim().is_empty()))),
        _ => Err(ConfigError::new(
            "OTEL_METRICS_EXPORTER must be otlp or none",
        )),
    }
}

/// Settings owned by the daemon exporter, excluded from inherited command
/// environments even in Full Control. Payloads can configure their own OTEL
/// settings explicitly through the existing command environment surface.
pub(crate) fn environment_name(name: &str) -> bool {
    matches!(
        name,
        "OTEL_SDK_DISABLED"
            | "OTEL_METRICS_EXPORTER"
            | "OTEL_SERVICE_NAME"
            | "OTEL_RESOURCE_ATTRIBUTES"
            | "OTEL_METRIC_EXPORT_INTERVAL"
    ) || ["OTEL_EXPORTER_OTLP_METRICS_", "OTEL_EXPORTER_OTLP_"]
        .into_iter()
        .any(|prefix| {
            name.strip_prefix(prefix).is_some_and(|suffix| {
                matches!(
                    suffix,
                    "ENDPOINT"
                        | "PROTOCOL"
                        | "HEADERS"
                        | "TIMEOUT"
                        | "COMPRESSION"
                        | "INSECURE"
                        | "CERTIFICATE"
                        | "CLIENT_CERTIFICATE"
                        | "CLIENT_KEY"
                )
            })
        })
}

/// Only the metrics SDK settings cross the private management re-exec.
#[cfg(target_os = "linux")]
pub(crate) fn environment() -> std::collections::BTreeMap<String, std::ffi::OsString> {
    std::env::vars_os()
        .filter_map(|(name, value)| Some((name.into_string().ok()?, value)))
        .filter(|(name, _)| environment_name(name))
        .collect()
}

fn setting(suffix: &str) -> Option<String> {
    [
        format!("OTEL_EXPORTER_OTLP_METRICS_{suffix}"),
        format!("OTEL_EXPORTER_OTLP_{suffix}"),
    ]
    .into_iter()
    .find_map(|name| std::env::var(name).ok().filter(|v| !v.trim().is_empty()))
}

fn tls_file(suffix: &str) -> Result<Option<Vec<u8>>, ConfigError> {
    setting(suffix)
        .map(|path| {
            std::fs::read(path)
                .map_err(|_| ConfigError::new(format!("cannot read OTLP {suffix} file")))
        })
        .transpose()
}

fn exporter() -> Result<MetricExporter, ConfigError> {
    // reqwest's provider-neutral TLS feature requires an installed provider.
    // Reuse Envd's ring provider, preserving an already selected process default.
    let _ = rustls::crypto::ring::default_provider().install_default();
    let protocol = match setting("PROTOCOL")
        .as_deref()
        .map(str::trim)
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        None | Some("http/protobuf") => Protocol::HttpBinary,
        Some("grpc") => Protocol::Grpc,
        _ => {
            return Err(ConfigError::new(
                "OTLP metrics protocol must be http/protobuf or grpc",
            ));
        }
    };
    let ca = tls_file("CERTIFICATE")?;
    let certificate = tls_file("CLIENT_CERTIFICATE")?;
    let key = tls_file("CLIENT_KEY")?;
    if certificate.is_some() != key.is_some() {
        return Err(ConfigError::new(
            "OTLP client certificate and key must be configured together",
        ));
    }
    let custom_tls = ca.is_some() || certificate.is_some();
    let builder = MetricExporter::builder().with_temporality(Temporality::Cumulative);
    let error = |_| ConfigError::new("cannot configure OTLP metrics exporter");
    match protocol {
        Protocol::Grpc => {
            use opentelemetry_otlp::tonic_types::transport::{
                Certificate, ClientTlsConfig, Identity,
            };
            let mut tls = ClientTlsConfig::new().with_enabled_roots();
            if let Some(ca) = ca {
                tls = tls.ca_certificate(Certificate::from_pem(ca));
            }
            if let (Some(certificate), Some(key)) = (certificate, key) {
                tls = tls.identity(Identity::from_pem(certificate, key));
            }
            builder
                .with_tonic()
                .with_tls_config(tls)
                .build()
                .map_err(error)
        }
        Protocol::HttpBinary => {
            let mut builder = builder.with_http().with_protocol(protocol);
            if custom_tls {
                // The SDK sets transport timeout only on its default client.
                // Preserve its milliseconds precedence for our TLS-aware client.
                let timeout = [
                    "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT",
                    "OTEL_EXPORTER_OTLP_TIMEOUT",
                ]
                .into_iter()
                .find_map(|name| std::env::var(name).ok()?.parse::<u64>().ok())
                .map(Duration::from_millis)
                .unwrap_or(opentelemetry_otlp::OTEL_EXPORTER_OTLP_TIMEOUT_DEFAULT);
                let client = std::thread::spawn(move || {
                    let invalid = |_| ConfigError::new("cannot configure OTLP HTTP TLS");
                    let mut client = reqwest::blocking::Client::builder().timeout(timeout);
                    if let Some(ca) = ca {
                        let certificates =
                            reqwest::Certificate::from_pem_bundle(&ca).map_err(invalid)?;
                        if certificates.is_empty() {
                            return Err(ConfigError::new("OTLP CA file contains no certificates"));
                        }
                        client = client.tls_certs_merge(certificates);
                    }
                    if let (Some(mut certificate), Some(key)) = (certificate, key) {
                        certificate.push(b'\n');
                        certificate.extend_from_slice(&key);
                        client = client
                            .identity(reqwest::Identity::from_pem(&certificate).map_err(invalid)?);
                    }
                    client.build().map_err(invalid)
                })
                .join()
                .map_err(|_| ConfigError::new("cannot initialize OTLP HTTP client"))??;
                builder = builder.with_http_client(client);
            }
            builder.build().map_err(error)
        }
        // No JSON transport is compiled into this distribution.
        _ => unreachable!("validated OTLP transport"),
    }
}

/// SDK errors may contain endpoint/header values. Only report a stable event.
#[derive(Debug)]
struct Exporter(MetricExporter);
impl PushMetricExporter for Exporter {
    async fn export(&self, metrics: &ResourceMetrics) -> OTelSdkResult {
        let result = self.0.export(metrics).await;
        if result.is_err() {
            warning("a13n-envd.monitoring.export_failed");
        }
        result
    }
    fn force_flush(&self) -> OTelSdkResult {
        self.0.force_flush()
    }
    fn shutdown_with_timeout(&self, timeout: Duration) -> OTelSdkResult {
        self.0.shutdown_with_timeout(timeout)
    }
    fn temporality(&self) -> Temporality {
        Temporality::Cumulative
    }
}

pub(super) fn warning(event: &str) {
    eprintln!("{}", serde_json::json!({"level":"warning", "event":event}));
}

pub(super) fn start(
    state: Arc<State>,
    device_id: String,
    generation: u64,
) -> Result<Option<(SdkMeterProvider, Instruments)>, ConfigError> {
    if !enabled()? {
        return Ok(None);
    }
    // Native counters are already cumulative. Delta aggregation would invent
    // a baseline for counters whose native start time is unknown.
    let exporter = exporter()?;
    let detected = Resource::builder().build();
    let service_name = detected
        .get(&Key::from_static_str("service.name"))
        .filter(|name| !name.as_str().starts_with("unknown_service"))
        .unwrap_or_else(|| "a13n-envd".into());
    let resource = Resource::builder()
        .with_attribute(KeyValue::new("service.name", service_name))
        .with_attribute(KeyValue::new("service.version", env!("CARGO_PKG_VERSION")))
        .with_attribute(KeyValue::new("service.instance.id", device_id.clone()))
        .build();
    let provider = SdkMeterProvider::builder()
        .with_resource(resource)
        .with_reader(PeriodicReader::builder(Exporter(exporter)).build())
        .build();
    let mut instruments = Instruments {
        meter: provider.meter("a13n-envd.monitoring"),
        state,
        device_id,
        generation,
        names: HashSet::new(),
        description: String::new(),
        counter: false,
    };
    instruments.refresh();
    Ok(Some((provider, instruments)))
}

/// Registration happens once per catalog metric, including metrics first made
/// available by a later sample. SDK callbacks read the current cache each time,
/// so absent/stale values are omitted rather than retained as current gauges.
pub(super) struct Instruments {
    meter: Meter,
    state: Arc<State>,
    device_id: String,
    generation: u64,
    names: HashSet<String>,
    description: String,
    counter: bool,
}
impl Instruments {
    pub(super) fn refresh(&mut self) {
        let report = self.state.report(&self.device_id, self.generation);
        projection::visit(&report, self);
    }
}
impl Sink for Instruments {
    fn family(&mut self, _name: &str, help: &str, kind: &str) {
        self.description = help.to_owned();
        self.counter = kind == "counter";
    }
    fn sample(&mut self, name: &str, _value: impl Into<Number>, _labels: &[(&str, &str)]) {
        if !self.names.insert(name.to_owned()) {
            return;
        }
        let state = self.state.clone();
        let device_id = self.device_id.clone();
        let generation = self.generation;
        let selected = name.to_owned();
        let callback = move |observer: &dyn AsyncInstrument<f64>| {
            let report = state.report(&device_id, generation);
            observe(&report, &selected, observer);
        };
        // OTLP doubles represent the full unsigned native range without signed
        // overflow. The EIP and scrape paths retain exact unsigned integers.
        if self.counter {
            self.meter
                .f64_observable_counter(format!("envd_{name}"))
                .with_description(self.description.clone())
                .with_callback(callback)
                .build();
        } else {
            self.meter
                .f64_observable_gauge(format!("envd_{name}"))
                .with_description(self.description.clone())
                .with_callback(callback)
                .build();
        }
    }
}

fn observe(report: &DeviceMetricsResult, selected: &str, observer: &dyn AsyncInstrument<f64>) {
    struct Observation<'a> {
        selected: &'a str,
        observer: &'a dyn AsyncInstrument<f64>,
        identity: [KeyValue; 2],
    }
    impl Sink for Observation<'_> {
        fn family(&mut self, _: &str, _: &str, _: &str) {}
        fn sample(&mut self, name: &str, value: impl Into<Number>, labels: &[(&str, &str)]) {
            if name != self.selected {
                return;
            }
            let value = match value.into() {
                Number::Unsigned(v) => v as f64,
                Number::Float(v) => v,
            };
            let mut attributes = self.identity.to_vec();
            attributes.extend(
                labels
                    .iter()
                    .map(|(k, v)| KeyValue::new((*k).to_owned(), (*v).to_owned())),
            );
            self.observer.observe(value, &attributes);
        }
    }
    projection::visit(
        report,
        &mut Observation {
            selected,
            observer,
            identity: [
                KeyValue::new("device_id", report.device_id.clone()),
                KeyValue::new("generation", report.generation.to_string()),
            ],
        },
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sdk_omits_missing_and_stale_series_and_restores_absolute_counters() {
        use opentelemetry_sdk::metrics::{
            InMemoryMetricExporterBuilder,
            data::{AggregatedMetrics, MetricData},
        };
        use serde_json::json;
        use std::{collections::BTreeMap, time::Instant};
        let options = serde_json::from_value(json!({"sample_interval_ms":1000})).unwrap();
        let state = State::new(&options);
        let output = InMemoryMetricExporterBuilder::new()
            .with_temporality(Temporality::Cumulative)
            .build();
        let provider = SdkMeterProvider::builder()
            .with_reader(
                PeriodicReader::builder(output.clone())
                    .with_interval(Duration::from_secs(3600))
                    .build(),
            )
            .build();
        let mut instruments = Instruments {
            meter: provider.meter("test"),
            state: state.clone(),
            device_id: "device-test".into(),
            generation: 42,
            names: HashSet::new(),
            description: String::new(),
            counter: false,
        };
        // Both single-series and additionally labeled gauges/counters disappear
        // between exports. A lower native total is a reset, never an increment.
        for (value, stale) in [
            (Some(10), false),
            (None, false),
            (Some(3), false),
            (Some(3), true),
            (Some(4), false),
        ] {
            let mut sample = json!({
                "sequence":1,"sampled_at":"2026-01-01T00:00:00Z","sample_age_ms":0,
                "collection_duration_ms":1,"system":{},"filesystems":{}
            });
            if let Some(value) = value {
                sample["system"] =
                    json!({"memory_total_bytes":value,"cpu_time_seconds":{"user":value}});
                sample["cgroup_v2"] =
                    json!({"cpu_usage_usec":value * 1_000_000, "memory_events":{}, "pressure":{}});
                sample["filesystems"] = json!({"workspace":{"metrics":{"total_bytes":value,"free_bytes":0,"available_bytes":0}}});
            }
            let observed = Instant::now()
                - if stale {
                    Duration::from_secs(10)
                } else {
                    Duration::ZERO
                };
            state.latest.send_replace(Some(Arc::new((
                observed,
                serde_json::from_value(sample).unwrap(),
            ))));
            instruments.refresh();
            provider.force_flush().unwrap();
            let mut values = BTreeMap::<String, Vec<f64>>::new();
            for batch in output.get_finished_metrics().unwrap() {
                for scope in batch.scope_metrics() {
                    for metric in scope.metrics() {
                        let points = match metric.data() {
                            AggregatedMetrics::F64(MetricData::Gauge(data)) => {
                                data.data_points().map(|p| p.value()).collect()
                            }
                            AggregatedMetrics::F64(MetricData::Sum(data)) => {
                                data.data_points().map(|p| p.value()).collect()
                            }
                            _ => panic!("unexpected aggregation"),
                        };
                        values.insert(metric.name().into(), points);
                    }
                }
            }
            for name in [
                "envd_system_memory_total_bytes",
                "envd_system_cpu_time_seconds_total",
                "envd_cgroup_cpu_usage_seconds_total",
                "envd_filesystem_total_bytes",
            ] {
                let actual = values.get(name).cloned().unwrap_or_default();
                let expected = value
                    .filter(|_| !stale)
                    .map(|v| vec![f64::from(v)])
                    .unwrap_or_default();
                assert_eq!(actual, expected, "{name}, stale={stale}");
            }
            output.reset();
        }
        provider.shutdown().unwrap();
    }

    #[test]
    fn export_selection_is_explicit_and_none_wins_over_endpoints() {
        let check = |entries: &[(&str, &str)]| {
            selection(|key| {
                entries
                    .iter()
                    .find(|(k, _)| *k == key)
                    .map(|(_, v)| (*v).to_owned())
            })
        };
        assert!(!check(&[]).unwrap());
        assert!(check(&[("OTEL_METRICS_EXPORTER", "otlp")]).unwrap());
        assert!(
            check(&[(
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
                "http://collector:4318/v1/metrics"
            )])
            .unwrap()
        );
        assert!(
            !check(&[
                ("OTEL_METRICS_EXPORTER", "none"),
                ("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
            ])
            .unwrap()
        );
        assert!(
            !check(&[
                ("OTEL_SDK_DISABLED", "TRUE"),
                ("OTEL_METRICS_EXPORTER", "otlp")
            ])
            .unwrap()
        );
        assert!(check(&[("OTEL_METRICS_EXPORTER", "unsupported")]).is_err());
    }
}
