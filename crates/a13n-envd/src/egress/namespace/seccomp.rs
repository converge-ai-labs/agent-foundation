//! Preserve native processes and Unix sockets without permitting namespace or
//! network-boundary reconfiguration. Bootstrap sockets stay out of payloads.
use std::io;

const LOAD: u16 = 0x20;
const EQUAL: u16 = 0x15;
const AND: u16 = 0x54;
const RETURN: u16 = 0x06;
const ALLOW: u32 = 0x7fff0000;
const DENY: u32 = 0x00050000 | libc::EPERM as u32;
const KILL: u32 = 0x80000000;

#[cfg(target_arch = "x86_64")]
const ARCH: u32 = 0xc000003e;
#[cfg(target_arch = "aarch64")]
const ARCH: u32 = 0xc00000b7;

fn instruction(code: u16, k: u32) -> libc::sock_filter {
    libc::sock_filter {
        code,
        jt: 0,
        jf: 0,
        k,
    }
}
fn equal(k: u32, yes: u8, no: u8) -> libc::sock_filter {
    libc::sock_filter {
        code: EQUAL,
        jt: yes,
        jf: no,
        k,
    }
}

#[cfg(any(target_arch = "x86_64", target_arch = "aarch64"))]
pub(super) fn install() -> io::Result<()> {
    // seccomp_data: nr at 0, arch at 4, args[0] at 16, args[1] at 24.
    let mut filter = vec![
        instruction(LOAD, 4),
        equal(ARCH, 1, 0),
        instruction(RETURN, KILL),
        instruction(LOAD, 0),
    ];
    #[cfg(target_arch = "x86_64")]
    filter.extend([
        instruction(AND, 0x40000000),
        equal(0, 1, 0),
        instruction(RETURN, KILL),
        instruction(LOAD, 0),
    ]);
    // io_uring socket/connect operations do not traverse the socket syscall filter.
    for syscall in [
        libc::SYS_io_uring_setup,
        libc::SYS_io_uring_enter,
        libc::SYS_io_uring_register,
        libc::SYS_mount,
        libc::SYS_umount2,
        libc::SYS_pivot_root,
        libc::SYS_open_tree,
        libc::SYS_move_mount,
        libc::SYS_mount_setattr,
        libc::SYS_fsopen,
        libc::SYS_fsconfig,
        libc::SYS_fsmount,
        libc::SYS_fspick,
        libc::SYS_setns,
        libc::SYS_unshare,
        libc::SYS_open_by_handle_at,
        libc::SYS_bpf,
        libc::SYS_perf_event_open,
    ] {
        filter.extend([equal(syscall as u32, 0, 1), instruction(RETURN, DENY)]);
    }
    filter.extend([
        // Pointer-based clone3 arguments cannot be inspected by classic BPF.
        // ENOSYS permits libc's ordinary thread/process clone fallback.
        equal(libc::SYS_clone3 as u32, 0, 1),
        instruction(RETURN, 0x00050000 | libc::ENOSYS as u32),
        equal(libc::SYS_clone as u32, 0, 4),
        instruction(LOAD, 16),
        instruction(
            AND,
            (libc::CLONE_NEWNS
                | libc::CLONE_NEWCGROUP
                | libc::CLONE_NEWUTS
                | libc::CLONE_NEWIPC
                | libc::CLONE_NEWUSER
                | libc::CLONE_NEWPID
                | libc::CLONE_NEWNET) as u32,
        ),
        equal(0, 1, 0),
        instruction(RETURN, DENY),
        instruction(LOAD, 0),
        // Unix sockets are native Session IPC. Deployment must not expose an
        // external privileged daemon or network proxy through shared paths.
        equal(libc::SYS_socket as u32, 0, 7),
        instruction(LOAD, 16),
        equal(libc::AF_INET as u32, 4, 0),
        equal(libc::AF_INET6 as u32, 3, 0),
        equal(libc::AF_UNIX as u32, 2, 0),
        equal(libc::AF_NETLINK as u32, 1, 0),
        instruction(RETURN, DENY),
        instruction(RETURN, ALLOW),
        instruction(RETURN, ALLOW),
    ]);
    let program = libc::sock_fprog {
        len: filter.len() as u16,
        filter: filter.as_mut_ptr(),
    };
    unsafe {
        if libc::prctl(libc::PR_SET_SECCOMP, 2, &program) != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

#[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
pub(super) fn install() -> io::Result<()> {
    Err(io::Error::other(
        "egress syscall isolation is unsupported on this architecture",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{Read, Write},
        os::fd::{AsRawFd, FromRawFd, OwnedFd},
        os::unix::net::UnixStream,
        process::Command,
    };

    #[test]
    fn socket_isolation() {
        const CHILD: &str = "A13N_ENVD_SECCOMP_TEST_CHILD";
        if std::env::var_os(CHILD).is_none() {
            let status = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "egress::namespace::seccomp::tests::socket_isolation",
                    "--nocapture",
                ])
                .env(CHILD, "1")
                .status()
                .unwrap();
            assert!(status.success());
            return;
        }
        let (listener, address, address_len) = named_seqpacket();
        crate::execution::disable_privilege_gain().unwrap();
        install().unwrap();
        seqpacket_cannot_retarget(listener, address, address_len);
        for family in [libc::AF_VSOCK, libc::AF_PACKET] {
            assert_eq!(unsafe { libc::socket(family, libc::SOCK_STREAM, 0) }, -1);
            assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::EPERM));
        }
        for kind in [libc::SOCK_STREAM, libc::SOCK_DGRAM, libc::SOCK_SEQPACKET] {
            let fd = unsafe { libc::socket(libc::AF_UNIX, kind, 0) };
            assert!(fd >= 0);
            unsafe {
                libc::close(fd);
            }
        }
        let (mut left, mut right) = UnixStream::pair().unwrap();
        left.write_all(b"ok").unwrap();
        let mut buffer = [0; 2];
        right.read_exact(&mut buffer).unwrap();
        assert_eq!(&buffer, b"ok");
        let mut pair = [-1; 2];
        assert_eq!(
            unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_DGRAM, 0, pair.as_mut_ptr()) },
            0
        );
        for fd in pair {
            unsafe {
                libc::close(fd);
            }
        }
        for syscall in [
            libc::SYS_unshare,
            libc::SYS_setns,
            libc::SYS_mount,
            libc::SYS_open_tree,
            libc::SYS_open_by_handle_at,
        ] {
            assert_eq!(unsafe { libc::syscall(syscall, 0, 0, 0, 0, 0, 0) }, -1);
            assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::EPERM));
        }
        assert_eq!(unsafe { libc::syscall(libc::SYS_clone3, 0, 0) }, -1);
        assert_eq!(
            io::Error::last_os_error().raw_os_error(),
            Some(libc::ENOSYS)
        );
        for syscall in [
            libc::SYS_io_uring_setup,
            libc::SYS_io_uring_enter,
            libc::SYS_io_uring_register,
        ] {
            assert_eq!(unsafe { libc::syscall(syscall, 0, 0, 0, 0, 0, 0) }, -1);
            assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::EPERM));
        }
        for kind in [libc::SOCK_STREAM, libc::SOCK_DGRAM] {
            let fd = unsafe { libc::socket(libc::AF_INET, kind, 0) };
            assert!(fd >= 0);
            unsafe {
                libc::close(fd);
            }
        }
    }

    fn named_seqpacket() -> (OwnedFd, libc::sockaddr_un, libc::socklen_t) {
        let fd =
            unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC, 0) };
        assert!(fd >= 0);
        let fd = unsafe { OwnedFd::from_raw_fd(fd) };
        let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        address.sun_family = libc::AF_UNIX as _;
        let name = format!("a13n-egress-test-{}", std::process::id());
        for (target, byte) in address.sun_path[1..].iter_mut().zip(name.bytes()) {
            *target = byte as _;
        }
        let length =
            (std::mem::offset_of!(libc::sockaddr_un, sun_path) + 1 + name.len()) as libc::socklen_t;
        assert_eq!(
            unsafe {
                libc::bind(
                    fd.as_raw_fd(),
                    (&address as *const libc::sockaddr_un).cast(),
                    length,
                )
            },
            0
        );
        assert_eq!(unsafe { libc::listen(fd.as_raw_fd(), 1) }, 0);
        (fd, address, length)
    }

    fn seqpacket_cannot_retarget(
        _listener: OwnedFd,
        address: libc::sockaddr_un,
        length: libc::socklen_t,
    ) {
        let mut pair = [-1; 2];
        assert_eq!(
            unsafe {
                libc::socketpair(
                    libc::AF_UNIX,
                    libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
                    0,
                    pair.as_mut_ptr(),
                )
            },
            0
        );
        let left = unsafe { OwnedFd::from_raw_fd(pair[0]) };
        let right = unsafe { OwnedFd::from_raw_fd(pair[1]) };
        let address = (&address as *const libc::sockaddr_un).cast();
        // SEQPACKET ignores the supplied name and sends only to its existing peer.
        assert_eq!(
            unsafe {
                libc::sendto(
                    left.as_raw_fd(),
                    b"x".as_ptr().cast(),
                    1,
                    libc::MSG_NOSIGNAL,
                    address,
                    length,
                )
            },
            1
        );
        let mut byte = [0u8; 1];
        assert_eq!(
            unsafe { libc::recv(right.as_raw_fd(), byte.as_mut_ptr().cast(), 1, 0) },
            1
        );
        assert_eq!(byte, *b"x");
        drop(right);
        for shutdown in [false, true] {
            if shutdown {
                unsafe {
                    libc::shutdown(left.as_raw_fd(), libc::SHUT_RDWR);
                }
            }
            assert_eq!(
                unsafe { libc::connect(left.as_raw_fd(), address, length) },
                -1
            );
            assert_eq!(
                io::Error::last_os_error().raw_os_error(),
                Some(libc::EISCONN)
            );
        }
    }
}
