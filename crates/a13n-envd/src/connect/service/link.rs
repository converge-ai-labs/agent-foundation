//! Parse an OS protocol activation into bounded Service connection arguments, never shell commands.

use super::*;
use std::collections::BTreeMap;

pub(crate) fn arguments(link: &str) -> io::Result<Vec<OsString>> {
    let url = Url::parse(link).map_err(|_| invalid("invalid Console link"))?;
    if url.scheme() != "a13n-envd"
        || url.host_str() != Some("connect")
        || !matches!(url.path(), "" | "/")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid("expected an a13n-envd://connect link"));
    }
    let mut values = BTreeMap::new();
    for (key, value) in url.query_pairs() {
        if !matches!(key.as_ref(), "service" | "workspace" | "host" | "pairing")
            || values
                .insert(key.into_owned(), value.into_owned())
                .is_some()
        {
            return Err(invalid("unknown or duplicate Console link field"));
        }
    }
    let required = |key: &str| {
        values
            .get(key)
            .ok_or_else(|| invalid("missing Console link field"))
    };
    let service = host_url(required("service")?)?;
    if service.path() != "/" {
        return Err(invalid("Console Service URL must be an origin"));
    }
    for key in ["workspace", "host", "pairing"] {
        validate_alias(required(key)?)?;
        if required(key)?.starts_with('-') {
            return Err(invalid("invalid Console link identifier"));
        }
    }
    Ok(vec![
        service.to_string().into(),
        "--workspace-id".into(),
        required("workspace")?.into(),
        "--host".into(),
        required("host")?.into(),
        "--pairing-id".into(),
        required("pairing")?.into(),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn activation_is_data_and_cannot_choose_local_options() {
        let valid = "a13n-envd://connect?service=https%3A%2F%2Fconsole.example&workspace=ws_test&host=console-test&pairing=pair_test";
        let parsed = arguments(valid).unwrap();
        assert_eq!(parsed[0], "https://console.example/");
        assert_eq!(parsed[6], "pair_test");
        for extra in [
            "&config=/tmp/evil",
            "&service=https://evil.example",
            "&computer-use=true",
            "#fragment",
        ] {
            assert!(arguments(&format!("{valid}{extra}")).is_err());
        }
        assert!(
            arguments(&valid.replace("https%3A%2F%2Fconsole.example", "http://remote.example"))
                .is_err()
        );
        assert!(arguments(&valid.replace("ws_test", "--config")).is_err());
    }
}
