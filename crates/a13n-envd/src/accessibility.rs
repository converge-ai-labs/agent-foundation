//! Session-owned accessibility helper supervision and bounded private IPC.
//!
//! Native objects live only in the child. Killing a blocked child retires its
//! epoch; the next call starts a fresh child rather than importing old handles.
#![allow(
    clippy::result_large_err,
    reason = "EIP errors retain operation evidence"
)]

use std::{
    io::{self, Read, Write},
    process::Stdio,
    sync::Arc,
    time::Duration,
};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{Mutex, Semaphore},
    time::Instant,
};

use crate::{
    eip::{DispatchStage, EIPError, ErrorType, RetryHint},
    operation::{OperationInterruption, ShortIdAllocator},
};

const MAX_FRAME_BYTES: usize = 2 * 1024 * 1024;
const MAX_PENDING: usize = 32;

#[path = "accessibility_budget.rs"]
pub(crate) mod budget;

/// Exact platform inventory, separate from the canonical protocol inventory.
/// Platform implementations extend this only alongside their native dispatcher.
pub(crate) fn supports(method: &str) -> bool {
    #[cfg(target_os = "linux")]
    {
        linux::METHODS.contains(&method)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = method;
        false
    }
}

#[cfg(target_os = "linux")]
#[path = "accessibility_linux.rs"]
mod linux;

pub(crate) fn is_method(method: &str) -> bool {
    [
        "computer.a11y_",
        "computer.ax_",
        "computer.uia_",
        "computer.atspi_",
    ]
    .iter()
    .any(|prefix| method.starts_with(prefix))
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    method: String,
    params: Value,
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum Event {
    Ready { epoch: String },
    Dispatch,
    Result { value: Value },
    Failure { error: Box<EIPError> },
}

#[derive(Debug)]
pub(crate) struct Failure {
    pub error: EIPError,
    /// A lost reply after sending a mutation remains uncertain even if its
    /// dispatch event was not received. Only an explicit pre-dispatch response
    /// proves that it did not execute.
    pub uncertain: bool,
}

/// Native acknowledgements carry no Session receipt; only the parent ledger can
/// issue operation evidence. Native success is independent of task success.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct NativeMutation {
    pub effect: crate::eip::ComputerEffect,
    pub native_status: crate::eip::AccessibilityMutationResultNativeStatus,
    pub dispatch_complete: bool,
    pub observation: Option<crate::eip::AccessibilityNode>,
}

struct Worker {
    child: Child,
    input: ChildStdin,
    output: ChildStdout,
}

pub(crate) struct Supervisor {
    ids: ShortIdAllocator,
    pending: Arc<Semaphore>,
    worker: Mutex<Option<Worker>>,
}

impl Supervisor {
    pub(crate) fn new(ids: ShortIdAllocator) -> Self {
        Self {
            ids,
            pending: Arc::new(Semaphore::new(MAX_PENDING)),
            worker: Mutex::new(None),
        }
    }

    pub(crate) async fn close(&self) -> bool {
        self.pending.close();
        let mut worker = self.worker.lock().await;
        match worker.take() {
            Some(mut worker) => worker.stop().await,
            None => true,
        }
    }

    pub(crate) async fn call(
        &self,
        method: &str,
        params: Value,
        deadline: Instant,
        interrupted: impl Fn() -> Option<OperationInterruption>,
    ) -> Result<Value, Failure> {
        if !supports(method) {
            return Err(Failure {
                error: error(
                    ErrorType::Unsupported,
                    "accessibility method is not implemented on this platform",
                ),
                uncertain: false,
            });
        }
        self.execute(
            Request {
                method: method.to_owned(),
                params,
            },
            deadline,
            interrupted,
            native_command,
        )
        .await
    }

    // Callers run this future in Session-owned work and join it during close.
    // Dropping a transport waiter must not drop an in-flight private exchange:
    // the single worker stream is reused only after its terminal frame is read.
    async fn execute(
        &self,
        mut request: Request,
        deadline: Instant,
        interrupted: impl Fn() -> Option<OperationInterruption>,
        command: impl FnOnce(&str) -> io::Result<Command>,
    ) -> Result<Value, Failure> {
        let mutation = !request.method.starts_with("computer.a11y_");
        // Encoding and size failures are provably before any delivery.
        encode_frame(&request).map_err(|_| Failure {
            error: error(
                ErrorType::InvalidParams,
                "accessibility request exceeds IPC limit",
            ),
            uncertain: false,
        })?;
        let _capacity = self
            .pending
            .clone()
            .try_acquire_owned()
            .map_err(|_| Failure {
                error: if self.pending.is_closed() {
                    error(ErrorType::NotInitialized, "accessibility Session is closed")
                } else {
                    error(ErrorType::Busy, "accessibility helper queue is full")
                },
                uncertain: false,
            })?;
        let interruption_error = || {
            if self.pending.is_closed() {
                return Some(error(
                    ErrorType::NotInitialized,
                    "accessibility Session is closed",
                ));
            }
            if let Some(reason) = interrupted() {
                return Some(error(
                    match reason {
                        OperationInterruption::Cancelled => ErrorType::Cancelled,
                        OperationInterruption::TimedOut => ErrorType::Timeout,
                    },
                    "accessibility operation interrupted",
                ));
            }
            (Instant::now() >= deadline)
                .then(|| error(ErrorType::Timeout, "accessibility deadline expired"))
        };
        let interruption = async {
            loop {
                if let Some(error) = interruption_error() {
                    return error;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        };
        tokio::pin!(interruption);
        let mut slot = tokio::select! {
            biased;
            error = &mut interruption => return Err(Failure { error, uncertain: false }),
            _ = tokio::time::sleep_until(deadline) => return Err(Failure { error: error(ErrorType::Timeout, "accessibility deadline expired in queue"), uncertain: false }),
            slot = self.worker.lock() => slot,
        };
        if let Some(error) = interruption_error() {
            return Err(Failure {
                error,
                uncertain: false,
            });
        }
        // A single executing request and bounded waiters share this deadline.
        // Never abandon the child while leaving a native call running in it.
        let mut sent = false;
        let mut spawned = false;
        let execution = async {
            if let Some(worker) = slot.as_mut()
                && worker.child.try_wait()?.is_some()
            {
                slot.take();
                return Ok(Err(Failure {
                    error: error(
                        ErrorType::ProviderUnavailable,
                        "accessibility helper exited while idle; reacquire references",
                    ),
                    uncertain: false,
                }));
            }
            if slot.is_none() {
                let epoch = self
                    .ids
                    .next("aep")
                    .map_err(|_| io::Error::other("accessibility epoch allocation failed"))?;
                // Retain the child before waiting for readiness, so timeout
                // and cancellation also kill and reap a blocked initialization.
                *slot = Some(Worker::spawn(command(&epoch)?)?);
                spawned = true;
                if let Err(error) = slot
                    .as_mut()
                    .expect("spawned helper is present")
                    .ready(&epoch)
                    .await?
                {
                    if let Some(mut worker) = slot.take() {
                        let _ = worker.stop().await;
                    }
                    return Ok(Err(Failure {
                        error,
                        uncertain: false,
                    }));
                }
            }
            // The polling future can still be asleep after queueing or startup.
            // Recheck synchronously at the last boundary before delivery.
            if let Some(error) = interruption_error() {
                return Ok(Err(Failure {
                    error,
                    uncertain: false,
                }));
            }
            // Native cooperative budgets use the time left after queue/startup,
            // not a fresh copy of the original public timeout.
            if let Some(params) = request.params.as_object_mut() {
                params.insert(
                    "_timeout_ms".to_owned(),
                    serde_json::json!(
                        deadline
                            .saturating_duration_since(Instant::now())
                            .as_millis()
                            .max(1) as u64
                    ),
                );
            }
            let data = match encode_frame(&request) {
                Ok(data) => data,
                Err(_) => {
                    return Ok(Err(Failure {
                        error: error(
                            ErrorType::InvalidParams,
                            "accessibility request exceeds IPC limit",
                        ),
                        uncertain: false,
                    }));
                }
            };
            let worker = slot.as_mut().expect("started helper is present");
            // Any partially delivered request can have reached native dispatch.
            sent = true;
            worker.input.write_all(&data).await?;
            worker.input.flush().await?;
            let mut dispatched = false;
            loop {
                match read_frame(&mut worker.output).await? {
                    Event::Dispatch if mutation && !dispatched => {
                        dispatched = true;
                    }
                    Event::Result { value } if !mutation || dispatched => return Ok(Ok(value)),
                    Event::Failure { error } => {
                        return Ok(Err(Failure {
                            error: *error,
                            uncertain: dispatched,
                        }));
                    }
                    _ => {
                        return Err(io::Error::other(
                            "invalid accessibility helper event sequence",
                        ));
                    }
                }
            }
        };
        let mut retire = false;
        let result: Result<Result<Value, Failure>, io::Error> = tokio::select! {
            biased;
            error = &mut interruption => { retire = true; Ok(Err(Failure { error, uncertain: false })) },
            _ = tokio::time::sleep_until(deadline) => { retire = true; Ok(Err(Failure { error: error(ErrorType::Timeout, "accessibility native call exceeded its deadline"), uncertain: false })) },
            result = execution => result,
        };
        match result {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(failure)) if !failure.uncertain && !(retire && (sent || spawned)) => {
                Err(failure)
            }
            outcome => {
                if let Some(mut worker) = slot.take() {
                    let _ = worker.stop().await;
                }
                let original = match outcome {
                    Ok(Err(failure)) => failure.error,
                    Err(_) => error(
                        ErrorType::InternalError,
                        "accessibility helper ended without a valid reply",
                    ),
                    Ok(Ok(_)) => unreachable!(),
                };
                let uncertain = mutation && sent;
                Err(Failure {
                    error: if uncertain {
                        unknown_outcome(
                            "accessibility mutation completion is unknown; reacquire references and reconcile before retrying",
                        )
                    } else {
                        original
                    },
                    uncertain,
                })
            }
        }
    }
}

fn native_command(epoch: &str) -> io::Result<Command> {
    let mut command = Command::new(std::env::current_exe()?);
    command.arg("--internal-accessibility-worker").arg(epoch);
    Ok(command)
}

impl Worker {
    fn spawn(mut command: Command) -> io::Result<Self> {
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()?;
        let input = child
            .stdin
            .take()
            .ok_or_else(|| io::Error::other("missing helper input"))?;
        let output = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("missing helper output"))?;
        Ok(Self {
            child,
            input,
            output,
        })
    }

    async fn ready(&mut self, epoch: &str) -> io::Result<Result<(), EIPError>> {
        match read_frame(&mut self.output).await? {
            Event::Ready { epoch: received } if received == epoch => Ok(Ok(())),
            Event::Failure { error } => Ok(Err(*error)),
            _ => Err(io::Error::other("invalid accessibility helper handshake")),
        }
    }

    async fn stop(&mut self) -> bool {
        let _ = self.child.start_kill();
        tokio::time::timeout(Duration::from_secs(1), self.child.wait())
            .await
            .is_ok_and(|status| status.is_ok())
    }
}

#[cfg(test)]
async fn write_frame(
    writer: &mut (impl tokio::io::AsyncWrite + Unpin),
    value: &impl Serialize,
) -> io::Result<()> {
    writer.write_all(&encode_frame(value)?).await?;
    writer.flush().await
}

async fn read_frame<T: serde::de::DeserializeOwned>(
    reader: &mut (impl AsyncRead + Unpin),
) -> io::Result<T> {
    let length = reader.read_u32().await? as usize;
    if length == 0 || length > MAX_FRAME_BYTES {
        return Err(io::Error::other("invalid accessibility IPC frame length"));
    }
    let mut data = vec![0; length];
    reader.read_exact(&mut data).await?;
    serde_json::from_slice(&data).map_err(io::Error::other)
}

fn error(kind: ErrorType, message: &str) -> EIPError {
    crate::daemon::protocol_error(kind, message)
}

pub(crate) fn unknown_outcome(message: &str) -> EIPError {
    let mut error = error(ErrorType::UnknownOutcome, message);
    error.data.retry_hint = RetryHint::ReconcileFirst;
    error.data.dispatch_stage = DispatchStage::Unknown;
    error
}

fn encode_frame(value: &impl Serialize) -> io::Result<Vec<u8>> {
    let data = serde_json::to_vec(value).map_err(io::Error::other)?;
    if data.len() > MAX_FRAME_BYTES {
        return Err(io::Error::other("accessibility IPC frame exceeds limit"));
    }
    let mut frame = Vec::with_capacity(4 + data.len());
    frame.extend_from_slice(&(data.len() as u32).to_be_bytes());
    frame.extend_from_slice(&data);
    Ok(frame)
}

/// Native objects and their thread-affine runtime belong to this backend in the
/// helper process. Call `dispatch` once, after operand validation and immediately
/// before the first native side effect. A read must never call it.
trait Backend {
    fn execute(
        &mut self,
        method: &str,
        params: Value,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError>;
}

fn native_backend(epoch: &str) -> Result<Box<dyn Backend>, EIPError> {
    #[cfg(target_os = "linux")]
    {
        linux::new(epoch)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = epoch;
        Err(error(
            ErrorType::Unsupported,
            "no native accessibility backend is installed",
        ))
    }
}

/// Runs on the helper's initial thread, including backend construction and drop.
/// Platform backends enter their required apartment/run-loop here.
pub(crate) fn run_internal(epoch: &str) -> io::Result<()> {
    let backend = match native_backend(epoch) {
        Ok(backend) => backend,
        Err(error) => {
            let mut output = io::stdout().lock();
            output.write_all(&encode_frame(&Event::Failure {
                error: Box::new(error),
            })?)?;
            return output.flush();
        }
    };
    serve_backend(epoch, backend, io::stdin().lock(), io::stdout().lock())
}

fn serve_backend(
    epoch: &str,
    mut backend: Box<dyn Backend>,
    mut input: impl Read,
    mut output: impl Write,
) -> io::Result<()> {
    let mut emit = |event: Event| -> io::Result<()> {
        output.write_all(&encode_frame(&event)?)?;
        output.flush()
    };
    emit(Event::Ready {
        epoch: epoch.to_owned(),
    })?;
    loop {
        // EOF at a frame boundary closes the Session. Partial frames fail.
        let mut header = [0u8; 4];
        if input.read(&mut header[..1])? == 0 {
            return Ok(());
        }
        input.read_exact(&mut header[1..])?;
        let length = u32::from_be_bytes(header) as usize;
        if length == 0 || length > MAX_FRAME_BYTES {
            return Err(io::Error::other("invalid accessibility IPC frame length"));
        }
        let mut data = vec![0; length];
        input.read_exact(&mut data)?;
        let request: Request = serde_json::from_slice(&data).map_err(io::Error::other)?;
        let mutation = !request.method.starts_with("computer.a11y_");
        let mut dispatched = false;
        let result = backend.execute(&request.method, request.params, &mut || {
            if !mutation || dispatched {
                return Err(error(
                    ErrorType::InternalError,
                    "invalid native dispatch boundary",
                ));
            }
            emit(Event::Dispatch).map_err(|_| {
                error(
                    ErrorType::ProviderUnavailable,
                    "accessibility helper channel closed",
                )
            })?;
            dispatched = true;
            Ok(())
        });
        let event = match result {
            Ok(value) if !mutation || dispatched => Event::Result { value },
            Ok(_) => Event::Failure {
                error: Box::new(error(
                    ErrorType::InternalError,
                    "native mutation did not cross dispatch boundary",
                )),
            },
            Err(error) => Event::Failure {
                error: Box::new(error),
            },
        };
        emit(event)?;
    }
}

#[cfg(test)]
#[path = "accessibility_tests.rs"]
mod tests;
