#[path = "build_support/mod.rs"]
mod build_support;

use std::{env, fs, path::PathBuf};

use prost::Message;
use prost_reflect::DescriptorPool;
use prost_types::FileDescriptorSet;
use sha2::{Digest, Sha256};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    const DESCRIPTOR_PATH: &str = "protocol/eip/v1/descriptor.pb";
    println!("cargo:rerun-if-changed={DESCRIPTOR_PATH}");
    println!("cargo:rerun-if-changed=build_support/mod.rs");

    let descriptor_bytes = fs::read(DESCRIPTOR_PATH)?;
    let descriptor_set = FileDescriptorSet::decode(descriptor_bytes.as_slice())?;
    let output_dir = PathBuf::from(env::var_os("OUT_DIR").ok_or("OUT_DIR is not set")?);

    let mut prost_config = prost_build::Config::new();
    prost_config.out_dir(&output_dir);
    prost_config.btree_map(["."]);
    prost_config.compile_fds(descriptor_set)?;

    let pool = DescriptorPool::decode(descriptor_bytes.as_slice())?;
    let descriptor_sha256 = format!("{:x}", Sha256::digest(&descriptor_bytes));
    let wire = format!(
        "pub const EIP_DESCRIPTOR_SHA256: &str = \"{descriptor_sha256}\";\n{}",
        build_support::render(&pool).map_err(std::io::Error::other)?
    );
    fs::write(output_dir.join("eip_wire.rs"), wire)?;
    #[cfg(feature = "vm-microsandbox")]
    if env::var("CARGO_CFG_TARGET_OS")? == "linux" {
        stage_microsandbox_runtime(&output_dir)?;
    }
    Ok(())
}

#[cfg(feature = "vm-microsandbox")]
fn stage_microsandbox_runtime(output: &std::path::Path) -> Result<(), Box<dyn std::error::Error>> {
    use microsandbox_utils::{PREBUILT_VERSION, bundle_download_url, http_client};

    println!("cargo:rerun-if-env-changed=MSB_EMBED_RUNTIME_BUNDLE_PATH");
    let destination = output.join("microsandbox-runtime.tar.gz");
    if let Some(source) = env::var_os("MSB_EMBED_RUNTIME_BUNDLE_PATH") {
        println!(
            "cargo:rerun-if-changed={}",
            PathBuf::from(&source).display()
        );
        fs::copy(source, destination)?;
    } else {
        // Build scripts execute on the host; runtime selection follows the target.
        let url = bundle_download_url(
            PREBUILT_VERSION,
            &env::var("CARGO_CFG_TARGET_ARCH")?,
            &env::var("CARGO_CFG_TARGET_OS")?,
        );
        println!("cargo:warning=downloading Microsandbox runtime v{PREBUILT_VERSION} from {url}");
        let response = http_client().get(&url).call()?;
        let mut archive = fs::File::create(destination)?;
        std::io::copy(&mut response.into_body().into_reader(), &mut archive)?;
    }
    Ok(())
}
