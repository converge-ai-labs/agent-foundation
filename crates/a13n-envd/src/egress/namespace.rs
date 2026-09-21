//! Linux bootstrap runs before constructing Tokio or executing any payload.
//! The Session worker owns PID 1; exiting it kills even detached descendants.
#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub(super) use linux::*;
#[cfg(target_os = "linux")]
pub(super) mod fds;
#[cfg(target_os = "linux")]
mod seccomp;
#[cfg(target_os = "linux")]
pub(super) mod tcp;

#[cfg(target_os = "linux")]
pub(super) mod udp;
