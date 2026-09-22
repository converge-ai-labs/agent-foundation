//! Network namespace setup; execution owns the worker and filesystem view.
use std::{
    io,
    net::{TcpListener, UdpSocket},
    process::Command,
};
const TCP_PORT: u16 = 15001;
const DNS_PORT: u16 = 53;

pub(crate) struct Listeners {
    pub tcp: TcpListener,
    pub udp: std::os::fd::OwnedFd,
    pub dns: UdpSocket,
}

pub(crate) fn network() -> io::Result<Listeners> {
    check_kernel()?;
    run("/usr/sbin/ip", &["link", "set", "lo", "up"])?;
    run(
        "/usr/sbin/ip",
        &[
            "route",
            "add",
            "local",
            "0.0.0.0/0",
            "dev",
            "lo",
            "src",
            "127.0.0.1",
        ],
    )?;
    let tcp = TcpListener::bind(("127.0.0.1", TCP_PORT))?;
    let udp = super::udp::socket()?;
    let dns = UdpSocket::bind(("127.0.0.1", DNS_PORT))?;
    let rules = format!(
        "add table ip a13n\nadd chain ip a13n output {{ type nat hook output priority -100; policy accept; }}\n\
         add rule ip a13n output ip daddr 127.0.0.1 udp dport 53 return\n\
         add rule ip a13n output ip daddr 127.0.0.0/10 return\n\
         add rule ip a13n output meta l4proto tcp dnat to 127.0.0.1:{TCP_PORT}\n\
         add chain ip a13n filter {{ type filter hook output priority 0; policy accept; }}\n\
         add rule ip a13n filter ip protocol icmp drop\n"
    );
    let mut nft = Command::new("/usr/sbin/nft")
        .args(["-f", "-"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::inherit())
        .spawn()?;
    use io::Write;
    nft.stdin
        .take()
        .ok_or_else(|| io::Error::other("nft input unavailable"))?
        .write_all(rules.as_bytes())?;
    if !nft.wait()?.success() {
        return Err(io::Error::other("DNAT unavailable"));
    }
    Ok(Listeners { tcp, udp, dns })
}

fn check_kernel() -> io::Result<()> {
    let mut name: libc::utsname = unsafe { std::mem::zeroed() };
    if unsafe { libc::uname(&mut name) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let release = unsafe { std::ffi::CStr::from_ptr(name.release.as_ptr()) }
        .to_str()
        .map_err(io::Error::other)?;
    // 6.1.2 fixes reconnectable SEQPACKET pairs racing with peer closure.
    if !supported_kernel(release) {
        return Err(io::Error::other(
            "egress isolation requires Linux 6.1.2 or newer",
        ));
    }
    Ok(())
}

fn run(executable: &str, args: &[&str]) -> io::Result<()> {
    if !Command::new(executable)
        .args(args)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()?
        .success()
    {
        return Err(io::Error::other("namespace network setup failed"));
    }
    Ok(())
}
fn supported_kernel(release: &str) -> bool {
    let parts: Vec<_> = release
        .split(['.', '-', '+'])
        .take(3)
        .map(str::parse::<u32>)
        .collect();
    matches!(parts.as_slice(), [Ok(major), Ok(minor), Ok(patch)] if (*major, *minor, *patch) >= (6, 1, 2))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn supported_kernel_versions_include_vendor_suffixes() {
        for release in ["6.1.2", "6.1.158+", "6.8.0-136-generic", "6.18.49"] {
            assert!(supported_kernel(release));
        }
        for release in ["6.1.1", "6.0.12", "6.2-rc1", "broken"] {
            assert!(!supported_kernel(release));
        }
    }
    // Full namespace, native sudo and shared-system-tree tests are exercised by
    // tests/execution_linux.py in a disposable privileged Linux sandbox.
}
