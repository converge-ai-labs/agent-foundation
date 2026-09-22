//! DNS answers identify a hostname; only the outer broker resolves its real address.
use super::policy::{Policy, Snapshot, hostname};
use std::{
    io,
    net::Ipv4Addr,
    sync::{Arc, Mutex},
};
use tokio::net::UdpSocket;

const MAX_NAMES: usize = 4096;
const FIRST_ADDRESS: u32 = 0x7f400001; // 127.64.0.1, reserved for intercepted traffic.

#[derive(Default)]
pub(crate) struct Routes(Mutex<Vec<String>>);

impl Routes {
    pub fn host(&self, address: Ipv4Addr) -> Option<String> {
        let index = u32::from(address).checked_sub(FIRST_ADDRESS)? as usize;
        self.0.lock().ok()?.get(index).cloned()
    }

    fn address(&self, host: &str) -> io::Result<Ipv4Addr> {
        let mut names = self
            .0
            .lock()
            .map_err(|_| io::Error::other("DNS map unavailable"))?;
        if let Some(index) = names.iter().position(|name| name == host) {
            return Ok(Ipv4Addr::from(FIRST_ADDRESS + index as u32));
        }
        if names.len() == MAX_NAMES {
            return Err(io::Error::other("DNS map is full"));
        }
        let address = Ipv4Addr::from(FIRST_ADDRESS + names.len() as u32);
        names.push(host.to_owned());
        Ok(address)
    }

    pub async fn serve(self: Arc<Self>, socket: UdpSocket, policy: Arc<Policy>) -> io::Result<()> {
        let mut buffer = [0; 513];
        loop {
            let (length, peer) = socket.recv_from(&mut buffer).await?;
            let snapshot = policy
                .snapshot()
                .map_err(|_| io::Error::other("policy closed"))?;
            if let Some(response) = self.answer(&buffer[..length], &snapshot) {
                socket.send_to(&response, peer).await?;
            }
        }
    }

    fn answer(&self, query: &[u8], policy: &Snapshot) -> Option<Vec<u8>> {
        if query.len() < 12 {
            return None;
        }
        let mut response = query[..12].to_vec();
        response[2] = 0x80 | (query[2] & 1); // response; preserve recursion desired
        response[3] = 0x80;
        response[4..12].fill(0);
        let question = parse_question(query);
        let Ok((host, kind, end)) = question else {
            response[3] |= 1; // FORMERR
            return Some(response);
        };
        response[5] = 1;
        response.extend_from_slice(&query[12..end]);
        if !policy.permits(&host) {
            response[3] |= 3; // NXDOMAIN
        } else if kind == 1 {
            match self.address(&host) {
                Ok(address) => {
                    response[7] = 1;
                    response.extend_from_slice(&[0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 30, 0, 4]);
                    response.extend_from_slice(&address.octets());
                }
                Err(_) => response[3] |= 2, // SERVFAIL; never reuse existing mappings.
            }
        }
        // Unsupported records, including AAAA, receive NOERROR with no answers.
        Some(response)
    }
}

fn parse_question(query: &[u8]) -> io::Result<(String, u16, usize)> {
    let invalid = || io::Error::other("invalid DNS question");
    if query.len() > 512 || query[2] & 0xf8 != 0 || query[4..6] != [0, 1] {
        return Err(invalid());
    }
    let mut cursor = 12;
    let mut labels = Vec::new();
    loop {
        let length = *query.get(cursor).ok_or_else(invalid)? as usize;
        cursor += 1;
        if length == 0 {
            break;
        }
        if length > 63 {
            return Err(invalid());
        } // compressed queries are not needed
        let bytes = query.get(cursor..cursor + length).ok_or_else(invalid)?;
        labels.push(std::str::from_utf8(bytes).map_err(|_| invalid())?);
        cursor += length;
    }
    let trailer = query.get(cursor..cursor + 4).ok_or_else(invalid)?;
    if trailer[2..] != [0, 1] {
        return Err(invalid());
    }
    let host = hostname(&labels.join(".")).map_err(|_| invalid())?;
    Ok((
        host,
        u16::from_be_bytes([trailer[0], trailer[1]]),
        cursor + 4,
    ))
}

#[cfg(test)]
mod tests {
    use super::super::policy::{PolicyInput, Update};
    use super::*;

    fn query(host: &str) -> Vec<u8> {
        let mut query = vec![1, 2, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0];
        for label in host.split('.') {
            query.push(label.len() as u8);
            query.extend_from_slice(label.as_bytes());
        }
        query.extend_from_slice(&[0, 0, 1, 0, 1]);
        query
    }

    #[test]
    fn answers_track_policy_without_reassigning_addresses() {
        let policy = Policy::new(PolicyInput::default()).unwrap();
        let routes = Routes::default();
        let allowed = routes
            .answer(&query("Example.COM"), &policy.snapshot().unwrap())
            .unwrap();
        assert_eq!(&allowed[allowed.len() - 4..], &[127, 64, 0, 1]);
        assert_eq!(
            routes.host(Ipv4Addr::new(127, 64, 0, 1)).as_deref(),
            Some("example.com")
        );
        let denied = policy
            .update(Update {
                expected_revision: 1,
                destinations: Some(crate::egress::policy::allowlist_destinations(vec![])),
                set_secrets: vec![],
                remove_secrets: vec![],
            })
            .unwrap();
        assert_eq!(
            routes.answer(&query("example.com"), &denied).unwrap()[3] & 15,
            3
        );
        assert_eq!(
            routes.address("other.example").unwrap(),
            Ipv4Addr::new(127, 64, 0, 2)
        );
        assert_eq!(
            routes.address("example.com").unwrap(),
            Ipv4Addr::new(127, 64, 0, 1)
        );
    }

    #[test]
    fn malformed_queries_never_allocate_routes() {
        let policy = Policy::new(PolicyInput::default())
            .unwrap()
            .snapshot()
            .unwrap();
        let routes = Routes::default();
        let original = query("example.com");
        for end in 0..original.len() {
            let reply = routes.answer(&original[..end], &policy);
            assert!(reply.is_none_or(|value| value[3] & 15 == 1));
        }
        let mut compressed = original;
        compressed[12] = 0xc0;
        assert_eq!(routes.answer(&compressed, &policy).unwrap()[3] & 15, 1);
        assert!(routes.0.lock().unwrap().is_empty());
    }
}
