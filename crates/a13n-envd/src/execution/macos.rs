//! Seatbelt confines the same Session and discovery workers used on Linux.
//! Grants are canonical paths, not a second filesystem implementation.
use super::boundary::{Access, Egress, GrantSource, Sandbox};
use std::{fs, io, path::Path, process::Command};

const SYSTEM: &[&str] = &[
    "/System",
    "/usr/bin",
    "/usr/lib",
    "/bin",
    "/sbin",
    "/private/var/db/timezone",
    "/private/etc/localtime",
];

pub(crate) fn command(
    sandbox: &Sandbox,
    sources: &[GrantSource],
    egress: Egress,
    state: &Path,
) -> io::Result<Command> {
    let Sandbox::Restricted { grants } = sandbox else {
        return Err(io::Error::other("Seatbelt requires restricted Sandbox"));
    };
    if sources.len() != grants.len() || egress.controlled() {
        return Err(io::Error::other("invalid Seatbelt launch policy"));
    }
    let executable = fs::canonicalize(std::env::current_exe()?)?;
    let mut command = Command::new("/usr/bin/sandbox-exec");
    let mut rules = vec![
        "(version 1)".to_owned(),
        "(deny default)".into(),
        "(allow process-exec process-fork)".into(),
        "(allow signal (target same-sandbox))".into(),
        "(allow process-info* (target same-sandbox))".into(),
        "(allow sysctl-read)".into(),
        // Native process startup opens the root directory. This literal rule
        // permits that handle, not recursive access to ungranted file contents.
        "(allow file-read-data (literal \"/\"))".into(),
        "(allow file-read* file-write* (literal \"/dev/null\") (literal \"/dev/zero\") (literal \"/dev/random\") (literal \"/dev/urandom\"))".into(),
    ];
    let mut index = 0;
    let mut allow = |path: &Path, writable: bool| -> io::Result<()> {
        let path = fs::canonicalize(path)?;
        let name = format!("PATH_{index}");
        index += 1;
        command.arg("-D").arg(format!("{name}={}", path.display()));
        let operation = if writable {
            "file-read* file-write*"
        } else {
            "file-read*"
        };
        let kind = if path.is_dir() { "subpath" } else { "literal" };
        rules.push(format!("(allow {operation} ({kind} (param \"{name}\")))"));
        // Path resolution may inspect parents, but does not need their contents.
        for parent in path.ancestors().skip(1) {
            let name = format!("PATH_{index}");
            index += 1;
            command
                .arg("-D")
                .arg(format!("{name}={}", parent.display()));
            rules.push(format!(
                "(allow file-read-metadata (literal (param \"{name}\")))"
            ));
        }
        Ok(())
    };
    for path in SYSTEM.iter().map(Path::new).filter(|path| path.exists()) {
        allow(path, false)?;
    }
    allow(&executable, false)?;
    allow(state, true)?;
    for (grant, source) in grants.iter().zip(sources) {
        // Do not silently authorize a different directory between Device startup
        // and worker launch. Seatbelt subsequently enforces canonical path rules.
        source.check(&fs::File::open(&grant.path)?)?;
        allow(&grant.path, grant.access == Access::ReadWrite)?;
    }
    if egress == (Egress::Inherit {}) {
        rules.push("(allow network*)".into());
        rules.push(
            "(allow mach-lookup (global-name \"com.apple.system.opendirectoryd.libinfo\"))".into(),
        );
        rules.push("(allow file-read* (literal \"/private/etc/hosts\") (literal \"/private/etc/resolv.conf\") (literal \"/private/etc/services\"))".into());
    }
    command.arg("-p").arg(rules.join("\n")).arg(executable);
    Ok(command)
}
