use std::{io, sync::Arc};

use rcgen::{BasicConstraints, CertificateParams, CertifiedIssuer, IsCa, KeyPair, KeyUsagePurpose};
use rustls::{ClientConfig, RootCertStore, ServerConfig, pki_types::PrivatePkcs8KeyDer};

/// The private key never leaves the proxy process. Only the PEM certificate is mounted.
pub(crate) struct Authority {
    issuer: CertifiedIssuer<'static, KeyPair>,
    upstream: Arc<ClientConfig>,
}

impl Authority {
    pub fn new() -> io::Result<Self> {
        let mut roots = RootCertStore::empty();
        let native = rustls_native_certs::load_native_certs();
        for certificate in native.certs {
            roots.add(certificate).map_err(io::Error::other)?;
        }
        if roots.is_empty() {
            return Err(io::Error::other("upstream trust store is empty"));
        }
        Self::with_roots(roots)
    }

    pub fn with_roots(roots: RootCertStore) -> io::Result<Self> {
        let mut parameters = CertificateParams::default();
        parameters.distinguished_name = rcgen::DistinguishedName::new();
        parameters
            .distinguished_name
            .push(rcgen::DnType::CommonName, "a13n Session CA");
        parameters.is_ca = IsCa::Ca(BasicConstraints::Constrained(0));
        parameters.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let issuer = CertifiedIssuer::self_signed(
            parameters,
            KeyPair::generate().map_err(io::Error::other)?,
        )
        .map_err(io::Error::other)?;
        let mut client = ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        // The HTTP parser owns one supported wire protocol; no opaque upgrades.
        client.alpn_protocols = vec![b"http/1.1".to_vec()];
        Ok(Self {
            issuer,
            upstream: Arc::new(client),
        })
    }

    pub fn pem(&self) -> String {
        self.issuer.pem()
    }
    pub fn upstream(&self) -> Arc<ClientConfig> {
        self.upstream.clone()
    }

    pub fn server(&self, host: &str) -> io::Result<Arc<ServerConfig>> {
        let key = KeyPair::generate().map_err(io::Error::other)?;
        let mut parameters =
            CertificateParams::new(vec![host.to_owned()]).map_err(io::Error::other)?;
        parameters.distinguished_name = rcgen::DistinguishedName::new();
        parameters
            .distinguished_name
            .push(rcgen::DnType::CommonName, host);
        let certificate = parameters
            .signed_by(&key, &self.issuer)
            .map_err(io::Error::other)?;
        let private = PrivatePkcs8KeyDer::from(key.serialize_der());
        let mut server = ServerConfig::builder()
            .with_no_client_auth()
            .with_single_cert(vec![certificate.der().clone()], private.into())
            .map_err(io::Error::other)?;
        server.alpn_protocols = vec![b"http/1.1".to_vec()];
        Ok(Arc::new(server))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::egress::{
        policy::{Policy, PolicyInput, Update},
        proxy::Proxy,
    };
    use tokio::{
        io::AsyncReadExt,
        net::{TcpListener, TcpStream},
        time::{Duration, timeout},
    };
    use tokio_rustls::TlsConnector;

    #[tokio::test]
    async fn session_ca_is_trusted_and_revocation_closes_existing_tls_connection() {
        let policy = Arc::new(
            Policy::new(PolicyInput {
                destinations: crate::egress::policy::allowlist_destinations(vec![
                    "example.com".into(),
                ]),
                secrets: vec![],
            })
            .unwrap(),
        );
        let authority = Authority::with_roots(RootCertStore::empty()).unwrap();
        let mut roots = RootCertStore::empty();
        for certificate in rustls_pemfile::certs(&mut authority.pem().as_bytes()) {
            roots.add(certificate.unwrap()).unwrap();
        }
        let client = Arc::new(
            ClientConfig::builder()
                .with_root_certificates(roots)
                .with_no_client_auth(),
        );
        let proxy = Proxy::new(policy.clone(), authority);
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (socket, _) = listener.accept().await.unwrap();
            proxy.serve(socket, "example.com".into(), 443).await
        });
        let socket = TcpStream::connect(address).await.unwrap();
        let mut client = TlsConnector::from(client)
            .connect("example.com".try_into().unwrap(), socket)
            .await
            .unwrap();
        policy
            .update(Update {
                expected_revision: 1,
                destinations: Some(crate::egress::policy::allowlist_destinations(vec![])),
                set_secrets: vec![],
                remove_secrets: vec![],
            })
            .unwrap();
        let result = timeout(Duration::from_secs(2), server)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::PermissionDenied);
        let result = timeout(Duration::from_secs(2), client.read(&mut [0; 1]))
            .await
            .unwrap();
        assert!(matches!(result, Ok(0) | Err(_)));
    }
}
