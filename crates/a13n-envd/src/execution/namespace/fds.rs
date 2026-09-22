//! Transfer only the namespace-bound sockets and worker death handle over the private bootstrap pipe.
use std::{
    io, mem,
    os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd},
    os::unix::net::UnixStream,
};

pub(crate) fn send(socket: &UnixStream, descriptors: &[RawFd]) -> io::Result<()> {
    if descriptors.is_empty() || descriptors.len() > 4 {
        return Err(io::Error::other("invalid bootstrap descriptor count"));
    }
    let mut byte = [1u8];
    let mut vector = libc::iovec {
        iov_base: byte.as_mut_ptr().cast(),
        iov_len: 1,
    };
    // usize storage provides cmsghdr alignment.
    let mut control = [0usize; 8];
    let mut message: libc::msghdr = unsafe { mem::zeroed() };
    message.msg_iov = &mut vector;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen =
        unsafe { libc::CMSG_SPACE(mem::size_of_val(descriptors) as _) as usize };
    unsafe {
        let header = libc::CMSG_FIRSTHDR(&message);
        (*header).cmsg_level = libc::SOL_SOCKET;
        (*header).cmsg_type = libc::SCM_RIGHTS;
        (*header).cmsg_len = libc::CMSG_LEN(mem::size_of_val(descriptors) as _) as usize;
        std::ptr::copy_nonoverlapping(
            descriptors.as_ptr().cast::<u8>(),
            libc::CMSG_DATA(header),
            mem::size_of_val(descriptors),
        );
        if libc::sendmsg(socket.as_raw_fd(), &message, libc::MSG_NOSIGNAL) != 1 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

pub(crate) fn receive<const N: usize>(socket: &UnixStream) -> io::Result<[OwnedFd; N]> {
    let mut byte = [0u8];
    let mut vector = libc::iovec {
        iov_base: byte.as_mut_ptr().cast(),
        iov_len: 1,
    };
    let mut control = [0usize; 8];
    let mut message: libc::msghdr = unsafe { mem::zeroed() };
    message.msg_iov = &mut vector;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = mem::size_of_val(&control);
    let mut descriptors = Vec::new();
    unsafe {
        let count = libc::recvmsg(socket.as_raw_fd(), &mut message, libc::MSG_CMSG_CLOEXEC);
        if count != 1 {
            return Err(io::Error::other("namespace bootstrap disconnected"));
        }
        let mut header = libc::CMSG_FIRSTHDR(&message);
        while !header.is_null() {
            if (*header).cmsg_level == libc::SOL_SOCKET && (*header).cmsg_type == libc::SCM_RIGHTS {
                let length =
                    ((*header).cmsg_len - libc::CMSG_LEN(0) as usize) / mem::size_of::<RawFd>();
                let fds =
                    std::slice::from_raw_parts(libc::CMSG_DATA(header).cast::<RawFd>(), length);
                for fd in fds {
                    descriptors.push(OwnedFd::from_raw_fd(*fd));
                }
            }
            header = libc::CMSG_NXTHDR(&message, header);
        }
    }
    if message.msg_flags & libc::MSG_CTRUNC != 0 || byte != [1] || descriptors.len() != N {
        return Err(io::Error::other("invalid namespace bootstrap descriptors"));
    }
    descriptors
        .try_into()
        .map_err(|_| io::Error::other("invalid bootstrap descriptor count"))
}
