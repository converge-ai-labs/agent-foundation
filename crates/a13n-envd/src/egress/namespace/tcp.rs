//! Accept inside the private network namespace; connect upstream in the broker.
use crate::egress::{dns::Routes, proxy::Proxy};
use std::{
    io,
    net::{Ipv4Addr, SocketAddrV4},
    os::fd::AsRawFd,
    sync::Arc,
};
use tokio::{
    net::{TcpListener, TcpStream},
    task::JoinSet,
};

const MAX_CONNECTIONS: usize = 128;
const SO_ORIGINAL_DST: libc::c_int = 80;

pub(in crate::egress) async fn serve(
    listener: TcpListener,
    routes: Arc<Routes>,
    proxy: Arc<Proxy>,
) -> io::Result<()> {
    let mut connections = JoinSet::new();
    loop {
        tokio::select! {
            accepted = listener.accept(), if connections.len() < MAX_CONNECTIONS => {
                let (socket, _) = accepted?;
                let Ok(destination) = original_destination(&socket) else { continue; };
                let host = routes.host(*destination.ip()).unwrap_or_else(|| destination.ip().to_string());
                let proxy = proxy.clone();
                connections.spawn(async move {
                    let _ = proxy.serve(socket, host, destination.port()).await;
                });
            }
            _ = connections.join_next(), if !connections.is_empty() => {}
        }
    }
}

fn original_destination(socket: &TcpStream) -> io::Result<SocketAddrV4> {
    let mut address: libc::sockaddr_in = unsafe { std::mem::zeroed() };
    let mut size = std::mem::size_of_val(&address) as libc::socklen_t;
    let result = unsafe {
        libc::getsockopt(
            socket.as_raw_fd(),
            libc::SOL_IP,
            SO_ORIGINAL_DST,
            (&mut address as *mut libc::sockaddr_in).cast(),
            &mut size,
        )
    };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    if size as usize != std::mem::size_of_val(&address)
        || address.sin_family as i32 != libc::AF_INET
    {
        return Err(io::Error::other("invalid original destination"));
    }
    Ok(SocketAddrV4::new(
        Ipv4Addr::from(address.sin_addr.s_addr.to_ne_bytes()),
        u16::from_be(address.sin_port),
    ))
}
