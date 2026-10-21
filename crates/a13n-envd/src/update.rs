//! Explicit native release acquisition. No daemon configuration or service lifecycle.
use std::{
    ffi::OsString,
    fs::{self, File},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

use reqwest::blocking::Client;
use semver::Version;
use serde::Deserialize;
use sha2::{Digest, Sha256};

const REPOSITORY: &str = "https://api.github.com/repos/converge-ai-labs/agent-foundation";
const DOWNLOAD: &str = "https://github.com/converge-ai-labs/agent-foundation/releases/download";
const PREFIX: &str = "release/a13n-envd-v";
const MAX_ARCHIVE: u64 = 256 * 1024 * 1024;
const MAX_METADATA: u64 = 4 * 1024 * 1024;
const PIN: &str = ".a13n-envd-version";
const HELP: &str = "a13n-envd update [--version X.Y.Z[-rc.N]] [--check] [--force]
                 [--install-dir ABSOLUTE_PATH] [--staging-dir ABSOLUTE_PATH]

Update this executable to the newest stable Envd release, or an exact version.
Stop daemons using this installation before updating; restart them afterward.
--check        Show the selected version and destination without installing.
--force        Reinstall even when the selected version is already installed.
--install-dir  Install to another directory (requires --version); used by Hosts
               for version-selected caches. The source installation is retained.
--staging-dir  Existing scratch directory owned by the caller, allowing Hosts to
               clean up interrupted downloads after the updater exits.
No automatic elevation, service restart, configuration or PATH changes.";

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Debug, Default)]
struct Options {
    version: Option<Version>,
    directory: Option<PathBuf>,
    staging: Option<PathBuf>,
    check: bool,
    force: bool,
}

impl Options {
    fn parse(arguments: &[OsString]) -> Result<Self> {
        let mut options = Self::default();
        let mut arguments = arguments.iter();
        while let Some(argument) = arguments.next() {
            match argument.to_str() {
                Some("--version") if options.version.is_none() => {
                    let value = arguments
                        .next()
                        .and_then(|value| value.to_str())
                        .ok_or("--version requires a canonical version")?;
                    options.version = Some(parse_version(value)?);
                }
                Some("--install-dir") if options.directory.is_none() => {
                    let value = arguments.next().ok_or("--install-dir requires a path")?;
                    let path = PathBuf::from(value);
                    if !path.is_absolute() {
                        return Err("--install-dir must be absolute".into());
                    }
                    options.directory = Some(path);
                }
                Some("--staging-dir") if options.staging.is_none() => {
                    let path =
                        PathBuf::from(arguments.next().ok_or("--staging-dir requires a path")?);
                    if !path.is_absolute() || !path.is_dir() {
                        return Err("--staging-dir must be an existing absolute directory".into());
                    }
                    options.staging = Some(path);
                }
                Some("--check") if !options.check => options.check = true,
                Some("--force") if !options.force => options.force = true,
                _ => {
                    return Err(format!(
                        "invalid update argument: {}; use update --help",
                        argument.to_string_lossy()
                    )
                    .into());
                }
            }
        }
        if options.directory.is_some() && options.version.is_none() {
            return Err("--install-dir requires --version".into());
        }
        if options.check && options.force {
            return Err("--check and --force cannot be combined".into());
        }
        Ok(options)
    }
}

fn parse_version(value: &str) -> Result<Version> {
    let version = Version::parse(value)?;
    let valid_rc = version
        .pre
        .as_str()
        .strip_prefix("rc.")
        .is_some_and(|number| {
            number
                .parse::<u64>()
                .is_ok_and(|n| n > 0 && n.to_string() == number)
        });
    if !version.build.is_empty() || (!version.pre.is_empty() && !valid_rc) {
        return Err("version must be X.Y.Z or X.Y.Z-rc.N (N >= 1)".into());
    }
    Ok(version)
}

/// Runs before ordinary daemon startup; the executable owns all update side effects.
pub fn run(arguments: &[OsString]) -> Result<()> {
    if arguments == [OsString::from("--help")] || arguments == [OsString::from("-h")] {
        println!("{HELP}");
        return Ok(());
    }
    let options = Options::parse(arguments)?;
    let executable = fs::canonicalize(std::env::current_exe()?)?;
    let target = target()?;
    let destination = match &options.directory {
        Some(directory) => directory.join(binary_name()),
        None => executable.clone(),
    };
    let source = Releases::new()?;
    update(&options, &executable, &destination, target, &source)
}

fn target() -> Result<&'static str> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("linux", "x86_64") => Ok("x86_64-unknown-linux-gnu"),
        ("linux", "aarch64") => Ok("aarch64-unknown-linux-gnu"),
        ("macos", "x86_64") => Ok("x86_64-apple-darwin"),
        ("macos", "aarch64") => Ok("aarch64-apple-darwin"),
        ("windows", "x86_64") => Ok("x86_64-pc-windows-msvc"),
        ("windows", "aarch64") => Ok("aarch64-pc-windows-msvc"),
        _ => Err("unsupported Envd release target".into()),
    }
}

fn binary_name() -> &'static str {
    if cfg!(windows) {
        "a13n-envd.exe"
    } else {
        "a13n-envd"
    }
}

#[derive(Deserialize)]
struct Release {
    tag_name: String,
    draft: bool,
    prerelease: bool,
}

struct Releases {
    client: Client,
    api: String,
    download: String,
}

impl Releases {
    fn new() -> Result<Self> {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Ok(Self {
            client: Client::builder()
                .https_only(true)
                .user_agent(concat!("a13n-envd/", env!("CARGO_PKG_VERSION")))
                .connect_timeout(Duration::from_secs(10))
                .timeout(Duration::from_secs(120))
                .build()?,
            api: REPOSITORY.into(),
            download: DOWNLOAD.into(),
        })
    }

    fn get(&self, url: &str, output: &mut impl Write, limit: u64) -> Result<()> {
        let response = self.client.get(url).send()?.error_for_status()?;
        if response
            .content_length()
            .is_some_and(|length| length > limit)
        {
            return Err("release download exceeds size limit".into());
        }
        copy_bounded(response, output, limit)?;
        Ok(())
    }

    fn latest(&self) -> Result<Version> {
        // GitHub lists releases newest first. Never use the monorepo-wide /latest.
        for page in 1..=100 {
            let mut bytes = Vec::new();
            self.get(
                &format!("{}/releases?per_page=100&page={page}", self.api),
                &mut bytes,
                MAX_METADATA,
            )?;
            let releases: Vec<Release> = serde_json::from_slice(&bytes)?;
            if releases.is_empty() {
                break;
            }
            for release in releases {
                if release.draft || release.prerelease {
                    continue;
                }
                if let Some(value) = release.tag_name.strip_prefix(PREFIX)
                    && let Ok(version) = parse_version(value)
                    && version.pre.is_empty()
                {
                    return Ok(version);
                }
            }
        }
        Err("no stable Envd release found in the release search limit; use --version".into())
    }
}

fn update(
    options: &Options,
    executable: &Path,
    destination: &Path,
    target: &str,
    source: &Releases,
) -> Result<()> {
    let parent = destination
        .parent()
        .ok_or("executable has no parent directory")?;
    // Hosts pin versioned installations; an accidental self-update must not change
    // the meaning of a version-addressed cache entry.
    match fs::read_to_string(parent.join(PIN)) {
        Ok(pin) => {
            let pin = parse_version(pin.trim())?;
            if options.version.as_ref() != Some(&pin) {
                return Err(format!(
                    "Host-managed installation pinned to {pin}; update through the Host"
                )
                .into());
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    let version = match &options.version {
        Some(version) => version.clone(),
        None => source.latest()?,
    };
    reject_link(destination)?;
    let current = if destination == executable {
        Some(parse_version(env!("CARGO_PKG_VERSION"))?)
    } else if options.force {
        // A forced reinstall can repair a damaged executable, but never follows
        // a destination symlink or replaces a directory.
        installed_version(destination).unwrap_or(None)
    } else {
        installed_version(destination)?
    };
    println!("Destination: {}", destination.display());
    println!(
        "Current: {}; selected: {version}",
        current
            .as_ref()
            .map_or_else(|| "not installed".into(), ToString::to_string)
    );
    if options.check {
        return Ok(());
    }
    if !options.force && current.as_ref() == Some(&version) {
        println!("Already up to date.");
        return Ok(());
    }
    if options.version.is_none() && current.as_ref().is_some_and(|current| current > &version) {
        println!("Current version is newer; use --version to select an older release explicitly.");
        return Ok(());
    }
    println!(
        "Stop daemons using this installation before updating. This command does not restart services."
    );
    fs::create_dir_all(parent)?;
    reject_link(destination)?;
    let stage = tempfile::Builder::new()
        .prefix(".a13n-envd-update-")
        .tempdir_in(options.staging.as_deref().unwrap_or(parent))?;
    let extension = if target.ends_with("windows-msvc") {
        "zip"
    } else {
        "tar.gz"
    };
    let archive_name = format!("a13n-envd-{version}-{target}.{extension}");
    let base = format!("{}/{PREFIX}{version}", source.download);
    let archive = stage.path().join(&archive_name);
    source.get(
        &format!("{base}/{archive_name}"),
        &mut File::create(&archive)?,
        MAX_ARCHIVE,
    )?;
    let mut checksums = Vec::new();
    source.get(&format!("{base}/SHA256SUMS"), &mut checksums, MAX_METADATA)?;
    verify_checksum(&archive, &archive_name, &checksums)?;
    let candidate = stage.path().join(binary_name());
    extract(&archive, &candidate, extension == "zip")?;
    if installed_version(&candidate)? != Some(version.clone()) {
        return Err("downloaded executable does not report the selected version".into());
    }
    publish(&candidate, executable, destination)?;
    println!("Installed a13n-envd {version}. Start Envd with your usual command or service.");
    Ok(())
}

fn publish(candidate: &Path, executable: &Path, destination: &Path) -> Result<()> {
    reject_link(destination)?;
    if fs::canonicalize(destination).ok().as_deref() == Some(executable) {
        self_replace::self_replace(candidate)?;
    } else {
        // tempfile::persist replaces existing destinations on Unix and Windows.
        let mut publish = tempfile::NamedTempFile::new_in(
            destination.parent().ok_or("destination has no parent")?,
        )?;
        io::copy(&mut File::open(candidate)?, &mut publish)?;
        publish
            .as_file()
            .set_permissions(fs::metadata(candidate)?.permissions())?;
        publish.as_file().sync_all()?;
        publish.persist(destination)?;
    }
    Ok(())
}

fn reject_link(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if !metadata.is_file() || metadata.file_type().is_symlink() => {
            Err("destination must be a regular file, not a directory or symlink".into())
        }
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn copy_bounded(mut input: impl Read, output: &mut impl Write, limit: u64) -> Result<()> {
    let size = io::copy(&mut (&mut input).take(limit + 1), output)?;
    if size > limit {
        return Err("release content exceeds size limit".into());
    }
    Ok(())
}

fn verify_checksum(archive: &Path, name: &str, bytes: &[u8]) -> Result<()> {
    let checksums = std::str::from_utf8(bytes)?;
    let mut expected = None;
    for line in checksums.lines() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.last().is_some_and(|value| {
            // sha256sum may mark binary input with `*`; release checksums name
            // files relative to `find .`, with one leading `./`.
            let value = value.strip_prefix('*').unwrap_or(value);
            value.strip_prefix("./").unwrap_or(value) == name
        }) {
            if fields.len() != 2
                || expected.is_some()
                || fields[0].len() != 64
                || !fields[0].bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("malformed or ambiguous archive checksum".into());
            }
            expected = Some(fields[0].to_ascii_lowercase());
        }
    }
    let expected = expected.ok_or("archive checksum is missing")?;
    let mut digest = Sha256::new();
    io::copy(&mut File::open(archive)?, &mut digest)?;
    if format!("{:x}", digest.finalize()) != expected {
        return Err("archive SHA256 mismatch".into());
    }
    Ok(())
}

fn extract(archive: &Path, candidate: &Path, zip: bool) -> Result<()> {
    let mut seen = Vec::new();
    let mut output = File::create(candidate)?;
    let mut entry = |name: &str, regular: bool, size: u64, input: &mut dyn Read| -> Result<()> {
        if !regular
            || ![binary_name(), "LICENSE"].contains(&name)
            || seen.iter().any(|old| old == name)
        {
            return Err("archive contains an unexpected, linked or duplicate entry".into());
        }
        seen.push(name.to_owned());
        if size > MAX_ARCHIVE {
            return Err("archive entry exceeds size limit".into());
        }
        if name == binary_name() {
            if size == 0 {
                return Err("archive executable is empty".into());
            }
            copy_bounded(input, &mut output, MAX_ARCHIVE)?;
        } else {
            copy_bounded(input, &mut io::sink(), MAX_METADATA)?;
        }
        Ok(())
    };
    if zip {
        let mut archive = zip::ZipArchive::new(File::open(archive)?)?;
        if archive.len() != 2 {
            return Err("archive must contain exactly executable and LICENSE".into());
        }
        for index in 0..archive.len() {
            let mut file = archive.by_index(index)?;
            let name = file.name().to_owned();
            let regular = file.is_file()
                && file
                    .unix_mode()
                    .is_none_or(|mode| mode & 0o170000 == 0 || mode & 0o170000 == 0o100000);
            entry(&name, regular, file.size(), &mut file)?;
        }
    } else {
        let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(File::open(archive)?));
        for file in archive.entries()? {
            let mut file = file?;
            let name = std::str::from_utf8(&file.path_bytes())?.to_owned();
            entry(
                &name,
                file.header().entry_type().is_file(),
                file.size(),
                &mut file,
            )?;
        }
    }
    if seen.len() != 2 {
        return Err("archive must contain exactly executable and LICENSE".into());
    }
    output.sync_all()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        output.set_permissions(fs::Permissions::from_mode(0o755))?;
    }
    Ok(())
}

fn installed_version(path: &Path) -> Result<Option<Version>> {
    if !path.exists() {
        return Ok(None);
    }
    reject_link(path)?;
    // File-backed output avoids unbounded pipe buffers and lets the parent enforce
    // a deadline without waiting for a candidate's inherited stdout handle.
    let mut stdout = tempfile::tempfile()?;
    let mut child = Command::new(path)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(stdout.try_clone()?)
        .stderr(Stdio::null())
        .spawn()?;
    let deadline = Instant::now() + Duration::from_secs(10);
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline || stdout.metadata()?.len() > 1024 {
            let _ = child.kill();
            let _ = child.wait();
            return Err("executable version probe timed out or exceeded its output limit".into());
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    if !status.success() {
        return Err("executable version probe failed".into());
    }
    use std::io::{Seek, SeekFrom};
    stdout.seek(SeekFrom::Start(0))?;
    let mut text = String::new();
    stdout.take(1025).read_to_string(&mut text)?;
    if text.len() > 1024 {
        return Err("executable version output exceeds limit".into());
    }
    let value = text
        .strip_prefix("a13n-envd ")
        .and_then(|text| text.strip_suffix('\n'))
        .ok_or("invalid executable version output")?;
    Ok(Some(parse_version(value.trim_end_matches('\r'))?))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::BufRead, net::TcpListener, thread};

    fn options(args: &[&str]) -> Result<Options> {
        Options::parse(&args.iter().map(OsString::from).collect::<Vec<_>>())
    }

    // Private transport injection keeps production release origins fixed.
    fn server(responses: Vec<(String, u16, Vec<u8>)>) -> (Releases, thread::JoinHandle<()>) {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let task = thread::spawn(move || {
            for (path, status, body) in responses {
                let deadline = Instant::now() + Duration::from_secs(15);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                            assert!(Instant::now() < deadline, "request not received: {path}");
                            thread::sleep(Duration::from_millis(10));
                        }
                        Err(error) => panic!("{error}"),
                    }
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut reader = io::BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                assert_eq!(line.split_whitespace().nth(1), Some(path.as_str()));
                loop {
                    line.clear();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" || line.is_empty() {
                        break;
                    }
                }
                write!(
                    stream,
                    "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .unwrap();
                stream.write_all(&body).unwrap();
            }
        });
        (
            Releases {
                client: Client::builder().no_proxy().build().unwrap(),
                api: url.clone(),
                download: url,
            },
            task,
        )
    }

    fn tar_bytes(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        let mut archive = tar::Builder::new(encoder);
        for (name, content) in entries {
            let mut header = tar::Header::new_gnu();
            header.set_size(content.len() as u64);
            header.set_mode(0o755);
            header.set_cksum();
            archive.append_data(&mut header, name, *content).unwrap();
        }
        archive.into_inner().unwrap().finish().unwrap()
    }

    #[test]
    fn strict_versions_and_options() {
        for value in ["1.2.3", "1.2.3-rc.1", "0.0.0"] {
            assert!(parse_version(value).is_ok());
        }
        for value in [
            "v1.2.3",
            "01.2.3",
            "1.2",
            "1.2.3rc1",
            "1.2.3-rc.0",
            "1.2.3-rc.01",
            "1.2.3-beta.1",
            "1.2.3+build",
            "../1.2.3",
        ] {
            assert!(parse_version(value).is_err(), "{value}");
        }
        for args in [
            vec!["--check", "--force"],
            vec!["--version"],
            vec!["--install-dir", "."],
            vec!["--unknown"],
            vec!["--check", "--check"],
        ] {
            assert!(options(&args).is_err());
        }
        let temp = tempfile::tempdir().unwrap();
        assert!(options(&["--install-dir", temp.path().to_str().unwrap()]).is_err());
        assert!(
            options(&[
                "--version",
                "1.2.3",
                "--install-dir",
                temp.path().to_str().unwrap()
            ])
            .is_ok()
        );
    }

    #[test]
    fn latest_filters_components_and_prereleases_and_paginates() {
        let (source, task) = server(vec![
            (
                "/releases?per_page=100&page=1".into(),
                200,
                br#"[
                {"tag_name":"release/a13n-harness-ui-v99.0.0","draft":false,"prerelease":false},
                {"tag_name":"release/a13n-envd-v4.0.0","draft":true,"prerelease":false},
                {"tag_name":"release/a13n-envd-v3.0.0","draft":false,"prerelease":true},
                {"tag_name":"release/a13n-envd-v2.0.0-rc.1","draft":false,"prerelease":false}
            ]"#
                .to_vec(),
            ),
            (
                "/releases?per_page=100&page=2".into(),
                200,
                br#"[{"tag_name":"release/a13n-envd-v1.2.3","draft":false,"prerelease":false}]"#
                    .to_vec(),
            ),
        ]);
        assert_eq!(source.latest().unwrap(), parse_version("1.2.3").unwrap());
        task.join().unwrap();
    }

    #[test]
    fn metadata_errors_are_not_retried() {
        for (status, body) in [
            (403, b"rate limit".to_vec()),
            (200, b"invalid".to_vec()),
            (200, b"[]".to_vec()),
        ] {
            let (source, task) =
                server(vec![("/releases?per_page=100&page=1".into(), status, body)]);
            assert!(source.latest().is_err());
            task.join().unwrap();
        }
    }

    #[test]
    fn checksums_require_one_matching_valid_entry() {
        let archive = tempfile::NamedTempFile::new().unwrap();
        fs::write(archive.path(), b"content").unwrap();
        let line = format!("{:x}  archive.tar.gz\n", Sha256::digest(b"content"));
        for filename in [
            "archive.tar.gz",
            "./archive.tar.gz",
            "*archive.tar.gz",
            "*./archive.tar.gz",
        ] {
            let checksums = line.replace("archive.tar.gz", filename);
            verify_checksum(archive.path(), "archive.tar.gz", checksums.as_bytes()).unwrap();
        }
        for bad in [
            String::new(),
            line.repeat(2),
            format!(
                "{line}{}",
                line.replace("archive.tar.gz", "./archive.tar.gz")
            ),
            format!(
                "{line}{}",
                line.replace("archive.tar.gz", "*./archive.tar.gz")
            ),
            line.replace("archive.tar.gz", "other.tar.gz"),
            line.replace("archive.tar.gz", "nested/archive.tar.gz"),
            line.replace("archive.tar.gz", "../archive.tar.gz"),
            line.replace("archive.tar.gz", "././archive.tar.gz"),
            line.replace("archive.tar.gz", "**archive.tar.gz"),
            format!("{}  archive.tar.gz\n", "0".repeat(64)),
            "invalid archive.tar.gz".into(),
        ] {
            assert!(verify_checksum(archive.path(), "archive.tar.gz", bad.as_bytes()).is_err());
        }
    }

    #[test]
    fn archives_accept_only_binary_and_license() {
        let temp = tempfile::tempdir().unwrap();
        let archive = temp.path().join("archive");
        let candidate = temp.path().join("candidate");
        fs::write(
            &archive,
            tar_bytes(&[(binary_name(), b"binary"), ("LICENSE", b"license")]),
        )
        .unwrap();
        extract(&archive, &candidate, false).unwrap();
        assert_eq!(fs::read(&candidate).unwrap(), b"binary");
        for entries in [
            vec![(binary_name(), b"binary".as_slice())],
            vec![(binary_name(), b"".as_slice()), ("LICENSE", b"license")],
            vec![
                (binary_name(), b"binary".as_slice()),
                (binary_name(), b"duplicate"),
            ],
            vec![
                (binary_name(), b"binary".as_slice()),
                ("extra", b"unexpected"),
            ],
        ] {
            fs::write(&archive, tar_bytes(&entries)).unwrap();
            assert!(extract(&archive, &candidate, false).is_err());
        }
        let mut bundle = zip::ZipWriter::new(File::create(&archive).unwrap());
        for name in [binary_name(), "LICENSE"] {
            bundle
                .start_file(name, zip::write::SimpleFileOptions::default())
                .unwrap();
            bundle.write_all(b"content").unwrap();
        }
        bundle.finish().unwrap();
        extract(&archive, &candidate, true).unwrap();
        assert_eq!(fs::read(&candidate).unwrap(), b"content");
        assert!(copy_bounded(b"12345".as_slice(), &mut Vec::new(), 4).is_err());
    }

    #[test]
    fn check_noop_and_pins_never_download_or_replace() {
        let temp = tempfile::tempdir().unwrap();
        let executable = temp.path().join(binary_name());
        fs::write(&executable, b"unchanged").unwrap();
        let (source, task) = server(vec![]);
        let mut selected = options(&["--version", env!("CARGO_PKG_VERSION")]).unwrap();
        update(
            &selected,
            &executable,
            &executable,
            target().unwrap(),
            &source,
        )
        .unwrap();
        selected.check = true;
        let missing = temp.path().join("new").join(binary_name());
        update(&selected, &executable, &missing, target().unwrap(), &source).unwrap();
        assert!(!missing.parent().unwrap().exists());
        fs::write(temp.path().join(PIN), "9.9.9\n").unwrap();
        assert!(
            update(
                &selected,
                &executable,
                &executable,
                target().unwrap(),
                &source
            )
            .unwrap_err()
            .to_string()
            .contains("pinned")
        );
        assert_eq!(fs::read(&executable).unwrap(), b"unchanged");
        task.join().unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn separate_install_verifies_publishes_and_preserves_source_and_failed_target() {
        use std::os::unix::fs::PermissionsExt;
        for failure in ["", "checksum", "version", "archive"] {
            let temp = tempfile::tempdir().unwrap();
            let source_executable = temp.path().join("source");
            fs::write(&source_executable, b"source installation").unwrap();
            let destination = temp.path().join(binary_name());
            // --force must also repair a corrupt installation.
            fs::write(&destination, b"damaged").unwrap();
            fs::set_permissions(&destination, fs::Permissions::from_mode(0o755)).unwrap();
            let script = if failure == "version" {
                b"#!/bin/sh\necho a13n-envd 1.0.0\n".as_slice()
            } else {
                b"#!/bin/sh\necho a13n-envd 9.8.7\n".as_slice()
            };
            let archive = if failure == "archive" {
                b"invalid archive".to_vec()
            } else {
                tar_bytes(&[(binary_name(), script), ("LICENSE", b"license")])
            };
            let name = format!("a13n-envd-9.8.7-{}.tar.gz", target().unwrap());
            let checksum = format!(
                // release-a13n-envd.yml uses `find . ... | xargs -0 sha256sum`.
                "{:x}  ./{name}\n",
                Sha256::digest(if failure == "checksum" {
                    b"wrong".as_slice()
                } else {
                    &archive
                })
            );
            let (source, task) = server(vec![
                (format!("/{PREFIX}9.8.7/{name}"), 200, archive),
                (
                    format!("/{PREFIX}9.8.7/SHA256SUMS"),
                    200,
                    checksum.into_bytes(),
                ),
            ]);
            let selected = options(&["--version", "9.8.7", "--force"]).unwrap();
            let result = update(
                &selected,
                &source_executable,
                &destination,
                target().unwrap(),
                &source,
            );
            if failure.is_empty() {
                result.unwrap();
                assert_eq!(
                    installed_version(&destination).unwrap(),
                    Some(parse_version("9.8.7").unwrap())
                );
            } else {
                assert!(result.is_err(), "{failure}");
                assert_eq!(fs::read(&destination).unwrap(), b"damaged");
            }
            assert_eq!(
                fs::read(&source_executable).unwrap(),
                b"source installation"
            );
            assert_eq!(
                fs::read_dir(temp.path()).unwrap().count(),
                2,
                "staging must be removed"
            );
            task.join().unwrap();
        }
    }
}

#[cfg(test)]
mod publication_tests {
    use super::*;

    #[test]
    fn running_executable_can_replace_itself() {
        let temp = tempfile::tempdir().unwrap();
        let executable = temp.path().join(binary_name());
        fs::copy(std::env::current_exe().unwrap(), &executable).unwrap();
        let candidate = temp.path().join("replacement");
        fs::write(&candidate, b"verified replacement fixture").unwrap();
        let output = Command::new(&executable)
            .args([
                "--ignored",
                "--exact",
                "update::publication_tests::self_replace_child",
            ])
            .env("A13N_TEST_UPDATE_CANDIDATE", &candidate)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            fs::read(&executable).unwrap(),
            b"verified replacement fixture"
        );
    }

    #[test]
    #[ignore = "invoked in a disposable executable by running_executable_can_replace_itself"]
    fn self_replace_child() {
        let candidate = PathBuf::from(std::env::var_os("A13N_TEST_UPDATE_CANDIDATE").unwrap());
        let executable = fs::canonicalize(std::env::current_exe().unwrap()).unwrap();
        publish(&candidate, &executable, &executable).unwrap();
    }
}
