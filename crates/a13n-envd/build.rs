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
    Ok(())
}
