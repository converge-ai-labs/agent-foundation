#![forbid(unsafe_code)]

use clap::Parser;

#[derive(Debug, Parser)]
#[command(
    name = "a13n-service-cli",
    version,
    about = "Command-line client for a13n Service"
)]
struct Cli {}

fn main() {
    Cli::parse();
}
