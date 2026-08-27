# a13n-sdk

Rust SDK crate for Agent Foundation Service.

## Status

This `0.0.x` crate reserves the stable package and crate names while the service API is being designed. It intentionally exposes no client API yet. Generated models and transports will be added only after the service contract is stable enough to support compatibility guarantees.

## Installation

```toml
[dependencies]
a13n-sdk = "0.0"
```

```rust
use a13n_sdk as foundation_sdk;
```

## Development

Run the Rust workspace checks from the repository root:

```bash
make sdk-rust-check
```

## License

Licensed under the Apache License 2.0.
