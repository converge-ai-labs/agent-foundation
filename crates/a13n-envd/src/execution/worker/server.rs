use super::wire::{Command, ENVELOPE_BYTES, Reply};
use crate::{
    config::Config,
    daemon::{CarrierResponse, Daemon, ResponseHandoff},
    data_dispatch::DataDispatcher,
    eip::{DataFrame, DataFrameKind},
    stdio::{self, ControlResponse, InboundFrame},
    transfer::reset_status,
};
use std::{collections::BTreeMap, io, time::Duration};
use tokio::{
    io::{AsyncRead, AsyncWrite},
    sync::mpsc,
    task::JoinSet,
};

enum Delivery {
    Running { discarded: bool },
    Pending(Option<ResponseHandoff>),
}

/// Reuse the canonical Session engine and framing, including response handoffs.
/// A private socket write is not evidence that the public caller received a result.
pub(super) async fn serve<R, W>(
    reader: R,
    writer: W,
    config: Config,
    generation: u64,
    session_id: String,
) -> io::Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin + Send + 'static,
{
    let capacity = config.limits.max_concurrent_operations as usize;
    let max_control = config.limits.max_request_bytes as usize + ENVELOPE_BYTES;
    let max_response = config.limits.max_response_bytes as usize + ENVELOPE_BYTES;
    let max_data = config.limits.max_transfer_frame_bytes as usize;
    let (controls, responses) = mpsc::channel(capacity);
    let (data, frames) = mpsc::channel(config.limits.max_concurrent_file_transfers as usize * 2);
    let (daemon, carrier) =
        Daemon::isolated_session(&config, generation, session_id.clone(), data.clone())
            .map_err(io::Error::other)?;
    let mut incoming = DataDispatcher::new(daemon.clone(), carrier.clone(), data.clone(), &config);
    let mut output = tokio::spawn(stdio::writer_loop(
        writer,
        responses,
        frames,
        max_response,
        max_data,
    ));
    let mut requests = JoinSet::<(u64, CarrierResponse)>::new();
    let mut deliveries = BTreeMap::new();
    reply(
        &controls,
        0,
        config.default_working_directory.clone().into(),
    )
    .await?;
    // Move this same buffer through read_next so prefetched frames survive dispatch.
    let reader = tokio::io::BufReader::new(reader);
    let mut next = Box::pin(read_next(reader, max_control, max_data));
    let outcome = async {
        loop {
            tokio::select! {
                finished = requests.join_next(), if !requests.is_empty() => {
                    let (ticket, response) = finished.ok_or_else(|| io::Error::other("worker request disappeared"))?.map_err(io::Error::other)?;
                    let (payload, handoff) = response.into_parts();
                    if matches!(deliveries.get(&ticket), Some(Delivery::Running { discarded: true })) {
                        deliveries.remove(&ticket);
                        drop(handoff);
                        continue;
                    }
                    deliveries.insert(ticket, Delivery::Pending(handoff));
                    reply(&controls, ticket, serde_json::from_slice(&payload).map_err(io::Error::other)?).await?;
                }
                (reader, frame) = &mut next => {
                    next = Box::pin(read_next(reader, max_control, max_data));
                    match frame? {
                    None => return Ok(()),
                    Some(InboundFrame::Data(frame)) => {
                        if let Err(error) = incoming.enqueue(frame.clone()) {
                            data.send(DataFrame {kind: DataFrameKind::Reset, session_id: frame.session_id, handle: frame.handle, offset: frame.offset, payload: vec![], reset_status: Some(reset_status(error))}).await.map_err(io::Error::other)?;
                        }
                    }
                    Some(InboundFrame::Control(bytes)) => {
                        match serde_json::from_slice::<Command>(&bytes).map_err(io::Error::other)? {
                            Command::Request {ticket, payload, environment} => {
                                if deliveries.contains_key(&ticket) { return Err(io::Error::other("duplicate worker ticket")); }
                                let payload = serde_json::to_string(&payload).map_err(io::Error::other)?;
                                if payload.len() > config.limits.max_request_bytes as usize { return Err(io::Error::other("worker request exceeds limit")); }
                                let permit = daemon.admit_payload(&payload);
                                if permit.is_none() || deliveries.len() >= capacity * 3 + 16 {
                                    reply(&controls, ticket, serde_json::from_slice(&daemon.busy_response(&payload)).map_err(io::Error::other)?).await?;
                                    continue;
                                }
                                deliveries.insert(ticket, Delivery::Running {discarded: false});
                                let pending = daemon.track_pending_payload(&payload);
                                let daemon = daemon.clone();
                                let carrier = carrier.clone();
                                requests.spawn(async move {
                                    let (_permit, _pending) = (permit, pending);
                                    (ticket, super::ENVIRONMENT.scope(environment, daemon.handle_payload_for_carrier(&carrier, &payload)).await)
                                });
                            }
                            Command::Close {ticket} => {
                                let closed = daemon.close_isolated_session(&session_id).await;
                                reply(&controls, ticket, serde_json::json!({"closed":closed})).await?;
                            }
                            Command::Detach {ticket} => {
                                daemon.detach_isolated_session(&session_id).await.map_err(|_| io::Error::other("worker detach failed"))?;
                                reply(&controls, ticket, serde_json::Value::Null).await?;
                            }
                            Command::Finish {ticket, delivered} => {
                                if let Some(delivery) = deliveries.remove(&ticket) {
                                    match delivery {
                                        Delivery::Running {..} => { deliveries.insert(ticket, Delivery::Running {discarded: true}); }
                                        Delivery::Pending(handoff) => if delivered && let Some(handoff) = handoff { handoff.complete(); },
                                    }
                                }
                            }
                        }
                    }
                    }
                }
            }
        }
    };
    // Resource TTLs belong to this engine; Session idle/grace expiry stays in the broker.
    // Keep this future scoped to serve so shutdown cannot leave a detached sweep task.
    let maintenance = async {
        let mut interval = tokio::time::interval(Duration::from_millis(100));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            daemon.maintenance().await;
        }
    };
    let (result, output_finished) = tokio::select! {
        result = outcome => (result, false),
        result = &mut output => (result.map_err(io::Error::other).and_then(|result| result), true),
        () = maintenance => unreachable!("maintenance runs until the worker stops"),
    };
    carrier.close();
    drop(incoming);
    requests.abort_all();
    while requests.join_next().await.is_some() {}
    drop(deliveries);
    daemon.drain(Duration::from_secs(3)).await;
    if !output_finished {
        output.abort();
        let _ = output.await;
    }
    result
}

async fn reply(
    channel: &mpsc::Sender<ControlResponse>,
    ticket: u64,
    payload: serde_json::Value,
) -> io::Result<()> {
    channel
        .send(ControlResponse {
            payload: serde_json::to_vec(&Reply { ticket, payload }).map_err(io::Error::other)?,
            handoff: None,
        })
        .await
        .map_err(io::Error::other)
}

// Own the reader so a sibling completion never discards partial frame bytes.
async fn read_next<R: AsyncRead + Unpin>(
    mut reader: R,
    control: usize,
    data: usize,
) -> (R, io::Result<Option<InboundFrame>>) {
    let frame = stdio::read_frame(&mut reader, control, data).await;
    (reader, frame)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tokio::io::AsyncWriteExt;

    fn frame(command: Command) -> Vec<u8> {
        let body = serde_json::to_vec(&command).unwrap();
        let mut bytes = format!("Content-Length: {}\r\n\r\n", body.len()).into_bytes();
        bytes.extend(body);
        bytes
    }
    fn request(ticket: u64, method: &str, params: serde_json::Value) -> Vec<u8> {
        frame(Command::Request {
            ticket,
            environment: Default::default(),
            payload: json!({"jsonrpc":"2.0", "id":ticket, "eip_session":"session-framing-1", "method":method, "params":params}),
        })
    }
    async fn received(reader: &mut (impl AsyncRead + Unpin)) -> Reply {
        let Some(InboundFrame::Control(bytes)) = tokio::time::timeout(
            Duration::from_secs(3),
            stdio::read_frame(reader, 1 << 20, 1 << 20),
        )
        .await
        .unwrap()
        .unwrap() else {
            panic!("expected control reply");
        };
        serde_json::from_slice(&bytes).unwrap()
    }

    #[tokio::test]
    async fn idle_worker_reclaims_expired_writer() {
        let workspace = std::env::temp_dir()
            .join(crate::operation::random_selector("worker-expiry-test").unwrap());
        std::fs::create_dir(&workspace).unwrap();
        let mut config = Config::for_test("device-framing");
        config.default_working_directory = workspace.to_str().unwrap().to_owned();
        let (client, server) = tokio::io::duplex(4096);
        let (reader, writer) = tokio::io::split(server);
        let task = tokio::spawn(serve(reader, writer, config, 1, "session-framing-1".into()));
        let (mut reader, mut writer) = tokio::io::split(client);
        assert_eq!(received(&mut reader).await.ticket, 0);
        for (ticket, method, params) in [
            (
                1,
                "environment.readiness",
                json!({"context":{"operation_id":"op-ready"}}),
            ),
            (
                2,
                "file.open_writer",
                json!({
                    "context":{"operation_id":"op-writer"},
                    "path":{"path":workspace.join("target")},
                    "mode":"create", "transfer_timeout_ms":100,
                }),
            ),
        ] {
            writer
                .write_all(&request(ticket, method, params))
                .await
                .unwrap();
            let response = received(&mut reader).await;
            assert!(
                response.payload.get("result").is_some(),
                "{:?}",
                response.payload
            );
            writer
                .write_all(&frame(Command::Finish {
                    ticket,
                    delivered: true,
                }))
                .await
                .unwrap();
        }
        // No new resource request should be needed to trigger expiration.
        tokio::time::timeout(Duration::from_secs(3), async {
            while std::fs::read_dir(&workspace).unwrap().any(|entry| {
                entry
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".eip-stage-")
            }) {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("expired staging file was not reclaimed");
        writer
            .write_all(&request(3, "session.keepalive", json!({})))
            .await
            .unwrap();
        assert_eq!(
            received(&mut reader).await.payload["result"],
            json!({"alive":true})
        );
        drop(writer);
        drop(reader);
        tokio::time::timeout(Duration::from_secs(5), task)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        std::fs::remove_dir_all(workspace).unwrap();
    }

    #[tokio::test]
    async fn detached_worker_can_close_from_a_prefetched_frame() {
        let config = Config::for_test("device-framing");
        let (client, server) = tokio::io::duplex(4096);
        let (reader, writer) = tokio::io::split(server);
        let task = tokio::spawn(serve(reader, writer, config, 1, "session-framing-1".into()));
        let (mut reader, mut writer) = tokio::io::split(client);
        assert_eq!(received(&mut reader).await.ticket, 0);
        // Prefetching Close with Detach must not lose the second frame.
        let mut commands = frame(Command::Detach { ticket: 1 });
        commands.extend(frame(Command::Close { ticket: 2 }));
        writer.write_all(&commands).await.unwrap();
        assert_eq!(received(&mut reader).await.ticket, 1);
        let closed = received(&mut reader).await;
        assert_eq!(closed.ticket, 2);
        assert_eq!(closed.payload, json!({"closed": true}));
        drop(writer);
        drop(reader);
        tokio::time::timeout(Duration::from_secs(5), task)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn request_completion_preserves_a_partially_received_next_frame() {
        let config = Config::for_test("device-framing");
        let (client, server) = tokio::io::duplex(4096);
        let (reader, writer) = tokio::io::split(server);
        let task = tokio::spawn(serve(reader, writer, config, 1, "session-framing-1".into()));
        let (mut reader, mut writer) = tokio::io::split(client);
        assert_eq!(received(&mut reader).await.ticket, 0);
        writer
            .write_all(&request(
                1,
                "environment.readiness",
                json!({"context":{"operation_id":"op-ready"}}),
            ))
            .await
            .unwrap();
        assert_eq!(received(&mut reader).await.ticket, 1);
        writer
            .write_all(&frame(Command::Finish {
                ticket: 1,
                delivered: true,
            }))
            .await
            .unwrap();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        writer.write_all(&request(2, "port.wait", json!({"context":{"operation_id":"op-wait", "timeout_ms":100}, "target":{"protocol":"tcp","address":"loopback","port":listener.local_addr().unwrap().port()}, "desired_status":"not_listening"}))).await.unwrap();
        let next = request(3, "session.keepalive", json!({}));
        writer.write_all(&next[..next.len() - 3]).await.unwrap();
        // A response becomes writable while the next request is missing its tail.
        assert_eq!(received(&mut reader).await.ticket, 2);
        writer.write_all(&next[next.len() - 3..]).await.unwrap();
        assert_eq!(received(&mut reader).await.ticket, 3);
        drop(writer);
        drop(reader);
        tokio::time::timeout(Duration::from_secs(5), task)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
    }
}
