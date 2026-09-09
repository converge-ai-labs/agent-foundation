//! Async Native Search Provider client and lossless Agent search configuration.
//! Other Service surfaces are not yet implemented by this SDK.
#![forbid(unsafe_code)]
mod client;
mod search;
pub use client::{ApiError, Client, Error};
pub use search::*;
