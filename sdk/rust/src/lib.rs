//! Typed Native HTTP bindings and an async Search Provider convenience client.
#![forbid(unsafe_code)]
mod client;
/// Low-level Native bindings. Request fields retain concrete types.
/// ```compile_fail
/// use a13n::generated::models::UpdateAgentRequest;
/// let _ = UpdateAgentRequest { name: Some(Some(42)), ..Default::default() };
/// ```
/// ```compile_fail
/// use a13n::generated::models::UserMessage;
/// let _ = UserMessage { content: 42, ..Default::default() };
/// ```
pub mod generated;
mod search;
mod workspace;
pub use client::{ApiError, CallError, Client, Error};
pub use search::*;
pub use workspace::WorkspaceClient;
