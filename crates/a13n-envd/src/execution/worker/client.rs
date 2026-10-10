//! Bounded private RPC relay. Public response delivery acknowledges the worker's ledger.
use super::wire::{Command, ENVELOPE_BYTES, Reply};
use crate::{
    config::DaemonLimits,
    eip::DataFrame,
    stdio::{self, ControlResponse, InboundFrame},
};
use std::{
    collections::BTreeMap,
    io,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
};
use tokio::{
    net::UnixStream,
    sync::{mpsc, oneshot, watch},
    task::JoinHandle,
};

type Pending = BTreeMap<u64, oneshot::Sender<Response>>;

struct Shared {
    controls: mpsc::Sender<ControlResponse>,
    pending: Mutex<Pending>,
    closed: watch::Sender<bool>,
}

impl Shared {
    fn fail(&self) {
        self.closed.send_replace(true);
        if let Ok(mut pending) = self.pending.lock() {
            pending.clear();
        }
    }

    fn finish(&self, ticket: u64, delivered: bool) {
        let command = encode(&Command::Finish { ticket, delivered });
        if self.controls.try_send(command).is_err() {
            self.fail();
        }
    }
}

pub(crate) struct Client {
    pub working_directory: String,
    shared: Arc<Shared>,
    next_ticket: AtomicU64,
    data: mpsc::Sender<DataFrame>,
    route: watch::Sender<Option<mpsc::Sender<DataFrame>>>,
    tasks: Vec<JoinHandle<()>>,
}

pub(crate) struct Response {
    pub payload: Vec<u8>,
    pub handoff: Handoff,
}

pub(crate) struct Handoff {
    shared: Arc<Shared>,
    ticket: u64,
    delivered: bool,
}

impl Handoff {
    pub(crate) fn complete(mut self) {
        self.delivered = true;
    }
}
impl Drop for Handoff {
    fn drop(&mut self) {
        self.shared.finish(self.ticket, self.delivered);
    }
}

struct PendingCall {
    shared: Arc<Shared>,
    ticket: u64,
}
impl Drop for PendingCall {
    fn drop(&mut self) {
        if self
            .shared
            .pending
            .lock()
            .is_ok_and(|mut pending| pending.remove(&self.ticket).is_some())
        {
            self.shared.finish(self.ticket, false);
        }
    }
}

impl Client {
    pub async fn connect(socket: UnixStream, limits: &DaemonLimits) -> io::Result<Self> {
        let capacity = limits.max_concurrent_operations as usize;
        let (controls, responses) = mpsc::channel(capacity * 4 + 16);
        let (data, frames) = mpsc::channel(limits.max_concurrent_file_transfers as usize * 2);
        let (route, routes) = watch::channel(None);
        let (closed, _) = watch::channel(false);
        let (ready, booted) = oneshot::channel();
        let shared = Arc::new(Shared {
            controls,
            closed,
            pending: Mutex::new(BTreeMap::from([(0, ready)])),
        });
        let (reader, writer) = socket.into_split();
        let max_request = limits.max_request_bytes as usize + ENVELOPE_BYTES;
        let max_response = limits.max_response_bytes as usize + ENVELOPE_BYTES;
        let max_data = limits.max_transfer_frame_bytes as usize;
        let output_state = shared.clone();
        let mut output_closed = shared.closed.subscribe();
        let output = tokio::spawn(async move {
            tokio::select! {
                _ = stdio::writer_loop(writer, responses, frames, max_request, max_data) => {},
                _ = output_closed.changed() => {},
            }
            output_state.fail();
        });
        let input_state = shared.clone();
        let mut input_closed = shared.closed.subscribe();
        let input = tokio::spawn(async move {
            tokio::select! {
                _ = read_replies(reader, input_state.clone(), routes, max_response, max_data) => {},
                _ = input_closed.changed() => {},
            }
            input_state.fail();
        });
        let mut client = Self {
            working_directory: String::new(),
            shared,
            next_ticket: AtomicU64::new(1),
            data,
            route,
            tasks: vec![input, output],
        };
        let response = booted.await.map_err(|_| broken())?;
        client.working_directory =
            serde_json::from_slice::<String>(&response.payload).map_err(|_| broken())?;
        response.handoff.complete();
        Ok(client)
    }

    pub fn shutdown(&self) {
        self.shared.fail();
    }

    pub fn closed(&self) -> watch::Receiver<bool> {
        self.shared.closed.subscribe()
    }
    pub fn attach(&self, route: mpsc::Sender<DataFrame>) {
        self.route.send_replace(Some(route));
    }

    pub async fn request(
        &self,
        payload: serde_json::Value,
        environment: std::collections::BTreeMap<String, String>,
    ) -> io::Result<Response> {
        // Credential-bearing control never crosses the isolation boundary.
        if payload.get("method").and_then(serde_json::Value::as_str) == Some("egress.update") {
            return Err(io::Error::other("egress control belongs to the broker"));
        }
        let ticket = self.ticket()?;
        self.call(
            ticket,
            Command::Request {
                ticket,
                payload,
                environment,
            },
        )
        .await
    }

    pub async fn close_worker(&self) -> io::Result<bool> {
        let ticket = self.ticket()?;
        let response = self.call(ticket, Command::Close { ticket }).await?;
        let payload: serde_json::Value =
            serde_json::from_slice(&response.payload).map_err(|_| broken())?;
        payload
            .get("closed")
            .and_then(serde_json::Value::as_bool)
            .ok_or_else(broken)
    }

    pub async fn detach(&self) -> io::Result<()> {
        self.route.send_replace(None);
        let ticket = self.ticket()?;
        self.call(ticket, Command::Detach { ticket }).await?;
        Ok(())
    }

    pub async fn data(&self, frame: DataFrame) -> io::Result<()> {
        self.data.send(frame).await.map_err(|_| broken())
    }

    fn ticket(&self) -> io::Result<u64> {
        if *self.shared.closed.borrow() {
            return Err(broken());
        }
        self.next_ticket
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
                value.checked_add(1)
            })
            .map_err(|_| broken())
    }

    async fn call(&self, ticket: u64, command: Command) -> io::Result<Response> {
        let (sender, receiver) = oneshot::channel();
        let mut closed = self.shared.closed.subscribe();
        {
            let mut pending = self.shared.pending.lock().map_err(|_| broken())?;
            if *closed.borrow() {
                return Err(broken());
            }
            pending.insert(ticket, sender);
        }
        let _pending = PendingCall {
            shared: self.shared.clone(),
            ticket,
        };
        tokio::select! {
            result = async {
                self.shared.controls.send(encode(&command)).await.map_err(|_| broken())?;
                receiver.await.map_err(|_| broken())
            } => result,
            _ = closed.changed() => Err(broken()),
        }
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        self.shared.fail();
        for task in &self.tasks {
            task.abort();
        }
    }
}

async fn read_replies(
    reader: tokio::net::unix::OwnedReadHalf,
    shared: Arc<Shared>,
    mut route: watch::Receiver<Option<mpsc::Sender<DataFrame>>>,
    max_control: usize,
    max_data: usize,
) -> io::Result<()> {
    // Buffer bytewise header reads and retain prefetched bytes across frames.
    let mut reader = tokio::io::BufReader::new(reader);
    loop {
        match stdio::read_frame(&mut reader, max_control, max_data).await? {
            None => return Err(broken()),
            Some(InboundFrame::Control(bytes)) => {
                let reply: Reply = serde_json::from_slice(&bytes).map_err(|_| broken())?;
                let pending = shared
                    .pending
                    .lock()
                    .map_err(|_| broken())?
                    .remove(&reply.ticket);
                if let Some(pending) = pending {
                    let response = Response {
                        payload: serde_json::to_vec(&reply.payload).map_err(io::Error::other)?,
                        handoff: Handoff {
                            shared: shared.clone(),
                            ticket: reply.ticket,
                            delivered: false,
                        },
                    };
                    let _ = pending.send(response);
                } else {
                    shared.finish(reply.ticket, false);
                }
            }
            Some(InboundFrame::Data(frame)) => {
                let destination = route.borrow_and_update().clone();
                if let Some(destination) = destination {
                    tokio::select! {
                        _ = destination.send(frame) => {},
                        _ = route.changed() => {},
                        _ = tokio::time::sleep(std::time::Duration::from_secs(30)) => return Err(broken()),
                    }
                }
            }
        }
    }
}

fn encode(command: &Command) -> ControlResponse {
    ControlResponse {
        payload: serde_json::to_vec(command).expect("private protocol consists of JSON values"),
        handoff: None,
    }
}
fn broken() -> io::Error {
    io::Error::new(io::ErrorKind::BrokenPipe, "isolated worker disconnected")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncWriteExt;

    fn reply_frame(ticket: u64) -> Vec<u8> {
        let body = serde_json::to_vec(&Reply {
            ticket,
            payload: if ticket == 0 {
                serde_json::json!("/workspace")
            } else {
                serde_json::json!({"ok":true})
            },
        })
        .unwrap();
        let mut frame = format!("Content-Length: {}\r\n\r\n", body.len()).into_bytes();
        frame.extend(body);
        frame
    }
    async fn reply(socket: &mut UnixStream, ticket: u64) {
        socket.write_all(&reply_frame(ticket)).await.unwrap();
    }
    async fn command(socket: &mut UnixStream) -> Command {
        loop {
            let Some(InboundFrame::Control(body)) =
                stdio::read_frame(socket, 1 << 20, 1 << 20).await.unwrap()
            else {
                panic!("expected command");
            };
            let command = serde_json::from_slice(&body).unwrap();
            if matches!(command, Command::Finish { ticket: 0, .. }) {
                continue;
            }
            return command;
        }
    }

    #[tokio::test]
    async fn coalesced_replies_preserve_prefetched_frames() {
        let (socket, mut peer) = UnixStream::pair().unwrap();
        let worker = tokio::spawn(async move {
            reply(&mut peer, 0).await;
            let mut tickets = Vec::new();
            for _ in 0..2 {
                let Command::Request { ticket, .. } = command(&mut peer).await else {
                    panic!("expected request");
                };
                tickets.push(ticket);
            }
            // Both replies fit in one read; the second must survive the first dispatch.
            let frames: Vec<u8> = tickets
                .iter()
                .flat_map(|&ticket| reply_frame(ticket))
                .collect();
            peer.write_all(&frames).await.unwrap();
            for ticket in tickets {
                assert!(
                    matches!(command(&mut peer).await, Command::Finish { ticket: actual, delivered: true } if actual == ticket)
                );
            }
        });
        tokio::time::timeout(std::time::Duration::from_secs(3), async {
            let client = Client::connect(socket, &DaemonLimits::default())
                .await
                .unwrap();
            let (first, second) = tokio::join!(
                client.request(serde_json::json!({}), Default::default()),
                client.request(serde_json::json!({}), Default::default()),
            );
            for response in [first.unwrap(), second.unwrap()] {
                assert_eq!(
                    serde_json::from_slice::<serde_json::Value>(&response.payload).unwrap(),
                    serde_json::json!({"ok":true})
                );
                response.handoff.complete();
            }
            worker.await.unwrap();
        })
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn only_public_delivery_completes_a_worker_handoff() {
        let (socket, mut peer) = UnixStream::pair().unwrap();
        let worker = tokio::spawn(async move {
            reply(&mut peer, 0).await;
            for (expected, delivered) in [(1, false), (2, true)] {
                assert!(
                    matches!(command(&mut peer).await, Command::Request {ticket,..} if ticket == expected)
                );
                reply(&mut peer, expected).await;
                assert!(
                    matches!(command(&mut peer).await, Command::Finish {ticket,delivered:actual} if ticket == expected && actual == delivered)
                );
            }
        });
        let client = Client::connect(socket, &DaemonLimits::default())
            .await
            .unwrap();
        let response = client
            .request(serde_json::json!({}), Default::default())
            .await
            .unwrap();
        drop(response);
        client
            .request(serde_json::json!({}), Default::default())
            .await
            .unwrap()
            .handoff
            .complete();
        tokio::time::timeout(std::time::Duration::from_secs(3), worker)
            .await
            .unwrap()
            .unwrap();
        assert!(
            tokio::time::timeout(
                std::time::Duration::from_secs(1),
                client.request(serde_json::json!({}), Default::default())
            )
            .await
            .unwrap()
            .is_err()
        );
    }
}
