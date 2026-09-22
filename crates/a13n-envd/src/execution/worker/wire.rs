//! Private commands on the bootstrap socket; never accepted by public EIP carriers.
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum Command {
    Request {
        ticket: u64,
        payload: Value,
        environment: std::collections::BTreeMap<String, String>,
    },
    Close {
        ticket: u64,
    },
    Detach {
        ticket: u64,
    },
    Finish {
        ticket: u64,
        delivered: bool,
    },
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Reply {
    pub ticket: u64,
    pub payload: Value,
}

/// Envelope overhead is bounded separately from the public request/response limit.
pub(super) const ENVELOPE_BYTES: usize = 32768;
