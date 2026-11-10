use super::*;
use serde_json::json;

fn request(method: &str, params: Value) -> Request {
    Request {
        method: method.to_owned(),
        params,
    }
}

fn supervisor() -> Supervisor {
    Supervisor::new(ShortIdAllocator::for_generation(1))
}

#[tokio::test]
async fn private_frames_preserve_explicit_empty_operands() {
    let (mut writer, mut reader) = tokio::io::duplex(1024);
    let request = request(
        "computer.ax_selected_children_set",
        json!({"request": {"children": []}}),
    );
    write_frame(&mut writer, &request).await.unwrap();
    let decoded: Request = read_frame(&mut reader).await.unwrap();
    assert_eq!(decoded.params, request.params);
}

#[tokio::test]
async fn private_frames_reject_size_before_allocating_payload() {
    let (mut writer, mut reader) = tokio::io::duplex(16);
    writer
        .write_u32((MAX_FRAME_BYTES + 1) as u32)
        .await
        .unwrap();
    assert!(read_frame::<Request>(&mut reader).await.is_err());
}

struct CountingBackend(usize);

impl Backend for CountingBackend {
    fn execute(
        &mut self,
        method: &str,
        params: Value,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        match method {
            "computer.a11y_inspect" => Ok(json!({"count": self.0})),
            "computer.atspi_action" => {
                if params["reject"] == true {
                    return Err(error(ErrorType::InvalidParams, "invalid native operand"));
                }
                dispatch()?;
                self.0 += 1;
                Ok(json!({"count": self.0}))
            }
            "computer.atspi_invalid" => Ok(Value::Null),
            _ => Err(error(ErrorType::Unsupported, "unsupported native method")),
        }
    }
}

#[tokio::test]
async fn worker_retains_backend_and_emits_exact_native_dispatch_boundary() {
    let requests = [
        request("computer.atspi_action", json!({"reject": true})),
        request("computer.atspi_action", json!({})),
        request("computer.a11y_inspect", json!({})),
        request("computer.atspi_invalid", json!({})),
    ];
    let mut input = Vec::new();
    for request in requests {
        input.extend(encode_frame(&request).unwrap());
    }
    let mut output = Vec::new();
    serve_backend(
        "aep-test",
        Box::new(CountingBackend(0)),
        input.as_slice(),
        &mut output,
    )
    .unwrap();
    let mut frames = output.as_slice();
    assert!(
        matches!(read_frame::<Event>(&mut frames).await.unwrap(), Event::Ready { epoch } if epoch == "aep-test")
    );
    assert!(
        matches!(read_frame::<Event>(&mut frames).await.unwrap(), Event::Failure { error } if error.data.error_type == ErrorType::InvalidParams)
    );
    assert!(matches!(
        read_frame::<Event>(&mut frames).await.unwrap(),
        Event::Dispatch
    ));
    assert!(
        matches!(read_frame::<Event>(&mut frames).await.unwrap(), Event::Result { value } if value["count"] == 1)
    );
    assert!(
        matches!(read_frame::<Event>(&mut frames).await.unwrap(), Event::Result { value } if value["count"] == 1)
    );
    assert!(
        matches!(read_frame::<Event>(&mut frames).await.unwrap(), Event::Failure { error } if error.data.error_type == ErrorType::InternalError)
    );
    assert!(frames.is_empty());
}

#[test]
fn worker_rejects_partial_and_oversize_frames() {
    for input in [
        vec![0],
        ((MAX_FRAME_BYTES + 1) as u32).to_be_bytes().to_vec(),
    ] {
        assert!(
            serve_backend(
                "aep-test",
                Box::new(CountingBackend(0)),
                input.as_slice(),
                Vec::new()
            )
            .is_err()
        );
    }
}

#[tokio::test]
async fn oversized_mutation_is_provably_unsent() {
    let supervisor = supervisor();
    let failure = supervisor
        .execute(
            request(
                "computer.atspi_action",
                json!({"text": "x".repeat(MAX_FRAME_BYTES)}),
            ),
            Instant::now() + Duration::from_secs(1),
            || None,
            |_| panic!("invalid request must not spawn a child"),
        )
        .await
        .unwrap_err();
    assert!(!failure.uncertain);
    assert_eq!(failure.error.data.error_type, ErrorType::InvalidParams);
    assert!(supervisor.worker.lock().await.is_none());
}

#[tokio::test]
async fn cancelled_and_expired_waiters_do_not_retire_executing_worker() {
    let supervisor = supervisor();
    let _lock = supervisor.worker.lock().await;
    for cancelled in [true, false] {
        let failure = supervisor
            .execute(
                request("computer.atspi_action", json!({})),
                Instant::now(),
                || cancelled.then_some(OperationInterruption::Cancelled),
                |_| panic!("queued request must not start a child"),
            )
            .await
            .unwrap_err();
        assert!(!failure.uncertain);
        assert_eq!(
            failure.error.data.error_type,
            if cancelled {
                ErrorType::Cancelled
            } else {
                ErrorType::Timeout
            }
        );
    }
}

#[tokio::test]
async fn helper_queue_and_session_close_are_bounded() {
    let supervisor = supervisor();
    let capacity = supervisor
        .pending
        .clone()
        .acquire_many_owned(MAX_PENDING as u32)
        .await
        .unwrap();
    let failure = supervisor
        .execute(
            request("computer.a11y_inspect", json!({})),
            Instant::now() + Duration::from_secs(1),
            || None,
            native_command,
        )
        .await
        .unwrap_err();
    assert_eq!(failure.error.data.error_type, ErrorType::Busy);
    drop(capacity);
    assert!(supervisor.close().await);
    let failure = supervisor
        .execute(
            request("computer.atspi_action", json!({})),
            Instant::now() + Duration::from_secs(1),
            || None,
            native_command,
        )
        .await
        .unwrap_err();
    assert!(!failure.uncertain);
    assert_eq!(failure.error.data.error_type, ErrorType::NotInitialized);
}

// Exercise actual child termination/reaping and restart without a desktop. The
// fixture speaks only private IPC; native platform acceptance has separate tests.
#[cfg(unix)]
mod process {
    use super::*;

    const FIXTURE: &str = r#"
import json, os, pathlib, struct, sys, time

def emit(value):
    data = json.dumps(value).encode()
    sys.stdout.buffer.write(struct.pack('>I', len(data)) + data)
    sys.stdout.buffer.flush()

if sys.argv[2] == 'startup_hang':
    time.sleep(60)
if sys.argv[2] == 'startup_error':
    emit(json.loads(sys.argv[3]))
    sys.exit(0)
emit({'kind': 'ready', 'epoch': sys.argv[1]})
count = 0
while True:
    header = sys.stdin.buffer.read(4)
    if not header:
        break
    data = sys.stdin.buffer.read(struct.unpack('>I', header)[0])
    request = json.loads(data)
    params = request['params']
    behavior = params.get('behavior')
    if behavior == 'loss_before_marker':
        sys.exit(0)
    if not request['method'].startswith('computer.a11y_'):
        emit({'kind': 'dispatch'})
    if 'marker' in params:
        pathlib.Path(params['marker']).write_text(str(os.getpid()))
    if behavior == 'hang':
        time.sleep(60)
    if behavior == 'loss_after_marker':
        sys.exit(0)
    count += 1
    emit({'kind': 'result', 'value': {'epoch': sys.argv[1], 'count': count, 'pid': os.getpid()}})
"#;

    fn fixture(epoch: &str, mode: &str) -> io::Result<Command> {
        let mut command = Command::new("python3");
        command.args(["-u", "-c", FIXTURE, epoch, mode]);
        Ok(command)
    }

    async fn invoke(
        supervisor: &Supervisor,
        method: &str,
        params: Value,
    ) -> Result<Value, Failure> {
        supervisor
            .execute(
                request(method, params),
                Instant::now() + Duration::from_secs(3),
                || None,
                |epoch| fixture(epoch, "ready"),
            )
            .await
    }

    fn assert_reconcile(failure: &Failure) {
        assert!(failure.uncertain);
        assert_eq!(failure.error.data.error_type, ErrorType::UnknownOutcome);
        assert_eq!(failure.error.data.retry_hint, RetryHint::ReconcileFirst);
        assert_eq!(failure.error.data.dispatch_stage, DispatchStage::Unknown);
    }

    #[tokio::test]
    async fn cancellation_while_queued_is_rechecked_before_sending() {
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::task::{Context, Waker};

        let supervisor = supervisor();
        let before = invoke(&supervisor, "computer.a11y_inspect", json!({}))
            .await
            .unwrap();
        let lock = supervisor.worker.lock().await;
        let cancelled = AtomicBool::new(false);
        let mut call = std::pin::pin!(supervisor.execute(
            request("computer.atspi_action", json!({})),
            Instant::now() + Duration::from_secs(3),
            || {
                cancelled
                    .load(Ordering::SeqCst)
                    .then_some(OperationInterruption::Cancelled)
            },
            |_| panic!("queued cancellation must preserve the existing child"),
        ));
        // First poll parks the interruption future in its 10ms sleep and the
        // execution on the held lock. Cancel before making that lock available.
        assert!(
            std::future::Future::poll(call.as_mut(), &mut Context::from_waker(Waker::noop()),)
                .is_pending()
        );
        cancelled.store(true, Ordering::SeqCst);
        drop(lock);
        let failure = call.await.unwrap_err();
        assert!(!failure.uncertain);
        assert_eq!(failure.error.data.error_type, ErrorType::Cancelled);
        let after = invoke(&supervisor, "computer.a11y_inspect", json!({}))
            .await
            .unwrap();
        assert_eq!(before["epoch"], after["epoch"]);
        assert_eq!(after["count"], 2);
        assert!(supervisor.close().await);
    }

    #[tokio::test]
    async fn deadline_after_lock_check_preserves_unsent_healthy_helper() {
        use std::cell::Cell;

        let supervisor = supervisor();
        let before = invoke(&supervisor, "computer.a11y_inspect", json!({}))
            .await
            .unwrap();
        let checks = Cell::new(0);
        let failure = supervisor
            .execute(
                request("computer.atspi_action", json!({})),
                Instant::now() + Duration::from_millis(20),
                || {
                    checks.set(checks.get() + 1);
                    if checks.get() == 2 {
                        // Move the deadline between the post-lock check and the
                        // biased execution select, without delivering a request.
                        std::thread::sleep(Duration::from_millis(40));
                    }
                    None
                },
                |_| panic!("an unsent deadline must preserve the existing child"),
            )
            .await
            .unwrap_err();
        assert_eq!(
            checks.get(),
            2,
            "the select must win before execution is polled"
        );
        assert!(!failure.uncertain);
        assert_eq!(failure.error.data.error_type, ErrorType::Timeout);
        let after = invoke(&supervisor, "computer.a11y_inspect", json!({}))
            .await
            .unwrap();
        assert_eq!(before["epoch"], after["epoch"]);
        assert_eq!(after["count"], 2);
        assert!(supervisor.close().await);
    }

    #[tokio::test]
    async fn typed_startup_failure_is_unsent_and_reaps_failed_helper() {
        let supervisor = supervisor();
        let expected = error(
            ErrorType::ProviderUnavailable,
            "native runtime is unavailable",
        );
        let event = serde_json::to_string(&Event::Failure {
            error: Box::new(expected.clone()),
        })
        .unwrap();
        let failure = supervisor
            .execute(
                request("computer.atspi_action", json!({})),
                Instant::now() + Duration::from_secs(3),
                || None,
                |epoch| {
                    let mut command = fixture(epoch, "startup_error")?;
                    command.arg(event);
                    Ok(command)
                },
            )
            .await
            .unwrap_err();
        assert!(!failure.uncertain);
        assert_eq!(failure.error, expected);
        assert!(supervisor.worker.lock().await.is_none());
        assert_eq!(
            invoke(&supervisor, "computer.a11y_inspect", json!({}))
                .await
                .unwrap()["count"],
            1
        );
        assert!(supervisor.close().await);
    }

    #[tokio::test]
    async fn helper_reply_loss_retires_epoch_and_never_replays_mutation() {
        for behavior in ["loss_before_marker", "loss_after_marker"] {
            let supervisor = supervisor();
            let before = invoke(&supervisor, "computer.a11y_inspect", json!({}))
                .await
                .unwrap();
            assert_eq!(
                invoke(&supervisor, "computer.a11y_inspect", json!({}))
                    .await
                    .unwrap()["count"],
                2
            );
            let failure = invoke(
                &supervisor,
                "computer.atspi_action",
                json!({"behavior": behavior}),
            )
            .await
            .unwrap_err();
            assert_reconcile(&failure);
            assert!(supervisor.worker.lock().await.is_none());
            let after = invoke(&supervisor, "computer.a11y_inspect", json!({}))
                .await
                .unwrap();
            assert_ne!(before["epoch"], after["epoch"]);
            assert_eq!(after["count"], 1);
            assert!(supervisor.close().await);
        }
    }

    #[tokio::test]
    async fn cancelling_executing_reads_and_mutations_kills_and_reaps_child() {
        for method in ["computer.a11y_inspect", "computer.atspi_action"] {
            let supervisor = supervisor();
            let temp = tempfile::tempdir().unwrap();
            let marker = temp.path().join("dispatched");
            let failure = supervisor
                .execute(
                    request(method, json!({"behavior": "hang", "marker": marker})),
                    Instant::now() + Duration::from_secs(3),
                    || marker.exists().then_some(OperationInterruption::Cancelled),
                    |epoch| fixture(epoch, "ready"),
                )
                .await
                .unwrap_err();
            if method.starts_with("computer.a11y_") {
                assert!(!failure.uncertain);
                assert_eq!(failure.error.data.error_type, ErrorType::Cancelled);
            } else {
                assert_reconcile(&failure);
            }
            assert!(marker.exists());
            let pid: i32 = std::fs::read_to_string(marker).unwrap().parse().unwrap();
            // The child has been waited, not merely signalled.
            assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
            assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
            assert!(supervisor.worker.lock().await.is_none());
        }
    }

    #[tokio::test]
    async fn deadline_retires_blocked_startup_without_uncertain_dispatch() {
        let supervisor = supervisor();
        let failure = supervisor
            .execute(
                request("computer.atspi_action", json!({})),
                Instant::now() + Duration::from_millis(50),
                || None,
                |epoch| fixture(epoch, "startup_hang"),
            )
            .await
            .unwrap_err();
        assert!(!failure.uncertain);
        assert_eq!(failure.error.data.error_type, ErrorType::Timeout);
        assert!(supervisor.worker.lock().await.is_none());
    }

    #[tokio::test]
    async fn deadline_retires_executing_child() {
        let supervisor = supervisor();
        // Establish readiness independently of the execution deadline.
        invoke(&supervisor, "computer.a11y_inspect", json!({}))
            .await
            .unwrap();
        let pid = supervisor
            .worker
            .lock()
            .await
            .as_ref()
            .unwrap()
            .child
            .id()
            .unwrap() as i32;
        let failure = supervisor
            .execute(
                request("computer.atspi_action", json!({"behavior": "hang"})),
                Instant::now() + Duration::from_millis(100),
                || None,
                |_| panic!("existing child must be reused"),
            )
            .await
            .unwrap_err();
        assert_reconcile(&failure);
        assert!(supervisor.worker.lock().await.is_none());
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    }

    #[tokio::test]
    async fn session_close_interrupts_executing_child_and_stops_queue() {
        let supervisor = Arc::new(supervisor());
        let temp = tempfile::tempdir().unwrap();
        let marker = temp.path().join("dispatched");
        let running = supervisor.clone();
        let path = marker.clone();
        let operation = tokio::spawn(async move {
            invoke(
                &running,
                "computer.atspi_action",
                json!({"behavior": "hang", "marker": path}),
            )
            .await
        });
        tokio::time::timeout(Duration::from_secs(3), async {
            while !marker.exists() {
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        assert!(
            tokio::time::timeout(Duration::from_secs(1), supervisor.close())
                .await
                .unwrap()
        );
        assert_reconcile(&operation.await.unwrap().unwrap_err());
        assert!(supervisor.worker.lock().await.is_none());
    }
}
