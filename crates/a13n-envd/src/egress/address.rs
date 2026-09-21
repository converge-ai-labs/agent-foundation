use std::{
    io,
    net::{IpAddr, Ipv4Addr, SocketAddr},
};

use super::policy::hostname;

/// Upstream routing never uses the intercepted client's DNS answer.
pub(super) async fn resolve_public(host: &str, port: u16) -> io::Result<Vec<SocketAddr>> {
    let addresses: Vec<_> = if let Ok(address) = host.parse::<IpAddr>() {
        vec![SocketAddr::new(address, port)]
    } else {
        hostname(host).map_err(|_| io::Error::other("invalid destination"))?;
        tokio::net::lookup_host((host, port)).await?.collect()
    };
    if addresses.is_empty() || addresses.iter().any(|address| !public(address.ip())) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "destination is not public",
        ));
    }
    Ok(addresses)
}

pub(super) fn public(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(ip) => public_v4(ip),
        IpAddr::V6(ip) => {
            // Admit global unicast only, excluding protocol, documentation and
            // transition ranges that may embed or route to non-public IPv4.
            let segments = ip.segments();
            (segments[0] & 0xe000) == 0x2000
                && !(segments[0] == 0x2001 && segments[1] < 0x0200)
                && !(segments[0] == 0x2001 && segments[1] == 0xdb8)
                && segments[0] != 0x2002
                && !(segments[0] == 0x3fff && (segments[1] & 0xf000) == 0)
        }
    }
}

fn public_v4(ip: Ipv4Addr) -> bool {
    let [a, b, c, _] = ip.octets();
    !(matches!(a, 0 | 10 | 127 | 224..=255)
        || (a == 100 && (64..=127).contains(&b))
        || (a == 169 && b == 254)
        || (a == 172 && (16..=31).contains(&b))
        || (a == 192 && (b == 168 || (b == 0 && (c == 0 || c == 2)) || (b == 88 && c == 99)))
        || (a == 198 && (b == 18 || b == 19 || (b == 51 && c == 100)))
        || (a == 203 && b == 0 && c == 113))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn blocks_local_metadata_multicast_and_transition_destinations() {
        for value in [
            "127.0.0.1",
            "10.1.2.3",
            "169.254.169.254",
            "100.100.100.200",
            "192.168.1.2",
            "198.18.0.1",
            "224.0.0.1",
            "::1",
            "::ffff:127.0.0.1",
            "fc00::1",
            "fe80::1",
            "2002:7f00:1::",
            "2001:db8::1",
        ] {
            assert!(!public(value.parse().unwrap()), "{value}");
        }
        for value in ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"] {
            assert!(public(value.parse().unwrap()), "{value}");
        }
    }
}
