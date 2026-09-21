//! The broker owns the only raw socket. Payloads can create ordinary UDP sockets only.
use crate::egress::{address, dns::Routes, policy::Policy};
use std::{
    collections::BTreeMap,
    io,
    net::{Ipv4Addr, SocketAddrV4},
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    sync::Arc,
    time::Duration,
};
use tokio::{io::unix::AsyncFd, net::UdpSocket, sync::mpsc, task::JoinSet};

const MAX_FLOWS: usize = 128;
const IDLE: Duration = Duration::from_secs(30);
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Flow {
    source: SocketAddrV4,
    destination: SocketAddrV4,
}

pub(super) fn socket() -> io::Result<OwnedFd> {
    let fd = unsafe {
        libc::socket(
            libc::AF_INET,
            libc::SOCK_RAW | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            libc::IPPROTO_UDP,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    let one: libc::c_int = 1;
    if unsafe {
        libc::setsockopt(
            fd.as_raw_fd(),
            libc::IPPROTO_IP,
            libc::IP_HDRINCL,
            (&one as *const libc::c_int).cast(),
            std::mem::size_of_val(&one) as _,
        )
    } != 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(fd)
}

pub(in crate::egress) async fn serve(
    fd: OwnedFd,
    routes: Arc<Routes>,
    policy: Arc<Policy>,
) -> io::Result<()> {
    let socket = Arc::new(AsyncFd::new(fd)?);
    let mut flows = BTreeMap::<Flow, mpsc::Sender<Vec<u8>>>::new();
    let mut tasks = JoinSet::new();
    let mut buffer = vec![0; 65535];
    loop {
        tokio::select! {
            packet = receive(&socket, &mut buffer) => {
                let length = packet?;
                let Some((flow, body)) = parse(&buffer[..length]) else { continue; };
                if let Some(sender) = flows.get(&flow) {
                    let _ = sender.try_send(body.to_vec());
                    continue;
                }
                if flows.len() >= MAX_FLOWS { continue; }
                let host = routes.host(*flow.destination.ip()).unwrap_or_else(|| flow.destination.ip().to_string());
                if !policy.snapshot().is_ok_and(|p| p.permits(&host)) { continue; }
                let (sender, receiver) = mpsc::channel(8);
                let _ = sender.try_send(body.to_vec());
                flows.insert(flow, sender);
                let (socket, policy) = (socket.clone(), policy.clone());
                tasks.spawn(async move { let _ = relay(socket, flow, host, policy, receiver).await; flow });
            }
            result = tasks.join_next(), if !tasks.is_empty() => {
                match result {
                    Some(Ok(flow)) => { flows.remove(&flow); }
                    _ => return Err(io::Error::other("UDP relay failed")),
                }
            }
        }
    }
}

async fn relay(
    raw: Arc<AsyncFd<OwnedFd>>,
    flow: Flow,
    host: String,
    policy: Arc<Policy>,
    mut packets: mpsc::Receiver<Vec<u8>>,
) -> io::Result<()> {
    let mut changes = policy.subscribe();
    let forwarding = async {
        let addresses = tokio::time::timeout(
            IDLE,
            address::resolve_public(&host, flow.destination.port()),
        )
        .await
        .map_err(io::Error::other)??;
        let destination = addresses
            .first()
            .ok_or_else(|| io::Error::other("no destination"))?;
        let upstream = UdpSocket::bind(if destination.is_ipv4() {
            "0.0.0.0:0"
        } else {
            "[::]:0"
        })
        .await?;
        upstream.connect(destination).await?;
        let mut buffer = vec![0; 65507];
        loop {
            if !policy.snapshot().is_ok_and(|p| p.permits(&host)) {
                return Ok(());
            }
            tokio::select! {
                packet = packets.recv() => match packet {
                    Some(packet) => { tokio::time::timeout(IDLE, upstream.send(&packet)).await.map_err(io::Error::other)??; }
                    None => return Ok(()),
                },
                received = upstream.recv(&mut buffer) => {
                    let length = received?;
                    tokio::time::timeout(IDLE, send(&raw, flow, &buffer[..length])).await.map_err(io::Error::other)??;
                }
                _ = tokio::time::sleep(IDLE) => return Ok(()),
            }
        }
    };
    tokio::pin!(forwarding);
    loop {
        if !policy.snapshot().is_ok_and(|p| p.permits(&host)) {
            return Ok(());
        }
        tokio::select! {
            result = &mut forwarding => return result,
            _ = changes.changed() => {},
        }
    }
}

fn parse(packet: &[u8]) -> Option<(Flow, &[u8])> {
    if packet.len() < 28
        || packet[0] >> 4 != 4
        || packet[9] != 17
        || u16::from_be_bytes([packet[6], packet[7]]) & 0x3fff != 0
    {
        return None;
    }
    let header = (packet[0] as usize & 15) * 4;
    let total = u16::from_be_bytes([packet[2], packet[3]]) as usize;
    if header < 20 || total != packet.len() || header + 8 > total {
        return None;
    }
    let source = Ipv4Addr::new(packet[12], packet[13], packet[14], packet[15]);
    let destination = Ipv4Addr::new(packet[16], packet[17], packet[18], packet[19]);
    // Ignore local traffic and our injected replies. No arbitrary source spoofing.
    let local = |ip: Ipv4Addr| u32::from(ip) & 0xffc00000 == 0x7f000000;
    if !local(source) || local(destination) {
        return None;
    }
    let udp = &packet[header..];
    if u16::from_be_bytes([udp[4], udp[5]]) as usize != udp.len() {
        return None;
    }
    Some((
        Flow {
            source: SocketAddrV4::new(source, u16::from_be_bytes([udp[0], udp[1]])),
            destination: SocketAddrV4::new(destination, u16::from_be_bytes([udp[2], udp[3]])),
        },
        &udp[8..],
    ))
}

async fn receive(socket: &AsyncFd<OwnedFd>, buffer: &mut [u8]) -> io::Result<usize> {
    loop {
        let mut ready = socket.readable().await?;
        match ready.try_io(|fd| {
            let size =
                unsafe { libc::recv(fd.as_raw_fd(), buffer.as_mut_ptr().cast(), buffer.len(), 0) };
            if size < 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(size as usize)
            }
        }) {
            Ok(result) => return result,
            Err(_) => continue,
        }
    }
}

async fn send(socket: &AsyncFd<OwnedFd>, flow: Flow, body: &[u8]) -> io::Result<()> {
    let mut packet = vec![0; 28];
    packet[0] = 0x45;
    packet[2..4].copy_from_slice(&((28 + body.len()) as u16).to_be_bytes());
    packet[8] = 64;
    packet[9] = 17;
    packet[12..16].copy_from_slice(&flow.destination.ip().octets());
    packet[16..20].copy_from_slice(&flow.source.ip().octets());
    packet[20..22].copy_from_slice(&flow.destination.port().to_be_bytes());
    packet[22..24].copy_from_slice(&flow.source.port().to_be_bytes());
    packet[24..26].copy_from_slice(&((8 + body.len()) as u16).to_be_bytes());
    packet.extend_from_slice(body);
    let address = libc::sockaddr_in {
        sin_family: libc::AF_INET as _,
        sin_port: flow.source.port().to_be(),
        sin_addr: libc::in_addr {
            s_addr: u32::from_ne_bytes(flow.source.ip().octets()),
        },
        sin_zero: [0; 8],
    };
    loop {
        let mut ready = socket.writable().await?;
        match ready.try_io(|fd| {
            let sent = unsafe {
                libc::sendto(
                    fd.as_raw_fd(),
                    packet.as_ptr().cast(),
                    packet.len(),
                    0,
                    (&address as *const libc::sockaddr_in).cast(),
                    std::mem::size_of_val(&address) as _,
                )
            };
            if sent < 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(())
            }
        }) {
            Ok(result) => return result,
            Err(_) => continue,
        }
    }
}
