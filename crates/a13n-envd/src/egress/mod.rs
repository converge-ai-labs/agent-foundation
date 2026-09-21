//! Session-owned network policy and credential substitution.
//!
//! The policy never grants filesystem or process authority. A caller must establish
//! the execution boundary before admitting payloads or exposing its proxy sockets.
mod address;
mod dns;
mod namespace;
pub(crate) mod policy;
mod proxy;
mod redact;
mod tls;
#[cfg(target_os = "linux")]
pub(crate) mod worker;

#[cfg(target_os = "linux")]
pub(crate) mod runtime;
