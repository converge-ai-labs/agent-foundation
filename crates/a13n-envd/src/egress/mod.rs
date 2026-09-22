//! Session-owned network policy and credential substitution.
//!
//! The policy never grants filesystem or process authority. A caller must establish
//! the execution boundary before admitting payloads or exposing its proxy sockets.
mod address;
pub(crate) mod dns;
pub(crate) mod namespace;
pub(crate) mod policy;
pub(crate) mod proxy;
mod redact;
pub(crate) mod tls;
