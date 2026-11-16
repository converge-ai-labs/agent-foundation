//! Release flavor is independent of the canonical package version.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Variant {
    Default,
    Slim,
    Vm,
}

impl Variant {
    pub(crate) fn current() -> Self {
        if cfg!(all(
            target_os = "linux",
            any(feature = "vm-boxlite", feature = "vm-microsandbox")
        )) {
            Self::Vm
        } else if cfg!(feature = "desktop") {
            Self::Default
        } else {
            Self::Slim
        }
    }

    pub(crate) fn parse(value: &str) -> Result<Self, &'static str> {
        match value {
            "default" => Ok(Self::Default),
            "slim" => Ok(Self::Slim),
            "vm" if cfg!(target_os = "linux") => Ok(Self::Vm),
            "vm" => Err("VM builds are available only for Linux"),
            _ => Err("variant must be default, slim or vm"),
        }
    }

    pub(crate) fn name(self) -> &'static str {
        match self {
            Self::Default => "default",
            Self::Slim => "slim",
            Self::Vm => "vm",
        }
    }

    pub(crate) fn suffix(self) -> &'static str {
        match self {
            Self::Default => "",
            Self::Slim => "-slim",
            Self::Vm => "-vm",
        }
    }
}
