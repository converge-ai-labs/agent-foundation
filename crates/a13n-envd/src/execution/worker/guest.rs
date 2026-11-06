//! Linux guest bootstrap. The SDK starts this process; private RPC owns all work.
use super::{WorkerConfig, enter, server};
use serde::{Deserialize, Serialize};
use std::{
    io::{self, Read},
    net::{TcpListener, TcpStream},
    os::fd::AsRawFd,
    time::{Duration, Instant},
};
use zeroize::Zeroize;

pub(crate) const BOOTSTRAP: &str = "/opt/a13n-worker/bootstrap.json";
pub(crate) const PORT: u16 = 42001;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Bootstrap {
    pub token: [u8; 32],
    pub worker: WorkerConfig,
    pub staging: Vec<crate::filesystem::StagingDirectory>,
}

pub(crate) fn validate(worker: &WorkerConfig) -> io::Result<()> {
    if worker.computer_use
        || worker.managed
        || worker.sandbox.restricted()
        || !worker.grant_sources.is_empty()
        || worker.egress != (crate::execution::boundary::Egress::Inherit {})
        || worker.execution.is_none()
    {
        return Err(io::Error::other("unsupported VM guest execution policy"));
    }
    worker
        .execution
        .unwrap()
        .validate()
        .map_err(io::Error::other)?;
    Ok(())
}

pub(crate) fn run() -> io::Result<()> {
    let mut file = std::fs::File::open(BOOTSTRAP)?;
    let mut bytes = Vec::new();
    file.by_ref()
        .take(super::MAX_BOOTSTRAP_BYTES as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > super::MAX_BOOTSTRAP_BYTES {
        return Err(io::Error::other("guest bootstrap exceeds limit"));
    }
    let decoded = serde_json::from_slice::<Bootstrap>(&bytes);
    bytes.zeroize();
    let mut bootstrap = decoded.map_err(|_| io::Error::other("invalid guest bootstrap"))?;
    validate(&bootstrap.worker)?;
    let listener = TcpListener::bind(("0.0.0.0", PORT))?;
    let socket = accept(&listener, &bootstrap.token, Duration::from_secs(30));
    bootstrap.token.zeroize();
    drop(listener); // No new control connection after the first authenticated bootstrap.
    let socket = socket?;
    let identity = bootstrap.worker.execution.unwrap();
    std::fs::create_dir_all(&bootstrap.worker.runtime_directory)?;
    let directory = std::fs::File::open(&bootstrap.worker.runtime_directory)?;
    if unsafe { libc::fchown(directory.as_raw_fd(), identity.uid, identity.gid) } != 0 {
        return Err(io::Error::last_os_error());
    }
    enter(&mut bootstrap.worker)?;
    let config = bootstrap.worker.config()?;
    socket.set_nonblocking(true)?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        let socket = tokio::net::TcpStream::from_std(socket)?;
        let (reader, writer) = socket.into_split();
        server::serve(
            reader,
            writer,
            config,
            bootstrap.worker.generation,
            bootstrap.worker.session_id,
            &bootstrap.staging,
        )
        .await
    });
    runtime.shutdown_timeout(Duration::from_secs(1));
    result
}

fn accept(listener: &TcpListener, token: &[u8; 32], timeout: Duration) -> io::Result<TcpStream> {
    listener.set_nonblocking(true)?;
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        match listener.accept() {
            Ok((mut socket, _)) => {
                socket.set_read_timeout(Some(
                    deadline
                        .saturating_duration_since(Instant::now())
                        .max(Duration::from_millis(1))
                        .min(Duration::from_secs(1)),
                ))?;
                let mut supplied = [0; 32];
                let authenticated = socket.read_exact(&mut supplied).is_ok() && supplied == *token;
                supplied.zeroize();
                if authenticated {
                    socket.set_read_timeout(None)?;
                    socket.set_nodelay(true)?;
                    return Ok(socket);
                }
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(10))
            }
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        "guest bootstrap connection timed out",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn rejects_first_connector_without_the_bootstrap_token() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let worker = std::thread::spawn(move || {
            let mut accepted = accept(&listener, &[7; 32], Duration::from_secs(3)).unwrap();
            let mut byte = [0];
            accepted.read_exact(&mut byte).unwrap();
            byte
        });
        let mut wrong = TcpStream::connect(address).unwrap();
        wrong.write_all(&[6; 32]).unwrap();
        let mut correct = TcpStream::connect(address).unwrap();
        correct.write_all(&[7; 32]).unwrap();
        correct.write_all(&[99]).unwrap();
        assert_eq!(worker.join().unwrap(), [99]);
    }

    #[test]
    fn bootstrap_wait_is_bounded() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        assert_eq!(
            accept(&listener, &[0; 32], Duration::from_millis(20))
                .unwrap_err()
                .kind(),
            io::ErrorKind::TimedOut
        );
    }
}
