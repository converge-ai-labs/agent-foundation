# Native macOS accessibility fixture

The opt-in acceptance test runs AppKit controls through the production AX helper, EIP HTTP transport and Harness accessibility facade. Run it serially in a disposable, ordinary-user macOS login session. The fixture writes application state independently of native acknowledgements.

## Prepare the Mac

1. Install Xcode Command Line Tools, Rust, Python 3.13 and uv.
2. Build the daemon from the macOS accessibility branch with `cargo build --locked --package a13n-envd`.
3. Start Envd as the signed-in graphical user with Computer use enabled, disabled Sandbox and inherited egress. Use an authenticated loopback listener or an owned tunnel. Do not run it as root or a background system service.
4. Grant Accessibility permission through **System Settings > Privacy & Security > Accessibility** to the daemon or responsible launching application shown by macOS. Restart it if macOS requires that. `a11y_describe` checks trust without prompting. Screen Recording permission is separate and is needed only for screenshot checks.
5. Create an owned writable fixture directory and compile the provider there. Keep the binary outside the repository:

```bash
mkdir -p "$HOME/ax-lab"
xcrun clang -fobjc-arc -framework AppKit \
  dev/fixtures/macos-desktop/accessibility.m \
  -o "$HOME/ax-lab/accessibility-fixture"
```

## Run native acceptance

Set the following variables on the test driver. `A13N_MACOS_DIRECTORY` is the absolute directory on the Mac that contains the compiled fixture. The credential file contains only the daemon credential; keep it private and out of logs.

```bash
export A13N_AX_TEST=1
export A13N_MACOS_ENDPOINT=http://127.0.0.1:18725
export A13N_MACOS_CREDENTIAL_FILE=/path/to/private/credential
export A13N_MACOS_DEVICE_ID=macos-lab
export A13N_MACOS_DIRECTORY=/Users/lab/ax-lab
uv run --locked --package a13n-harness --group test --no-default-groups pytest \
  packages/a13n-harness/tests/test_accessibility_macos_native.py -v
```

The authenticated Session starts and owns the fixture process. Each run uses a unique state file. Session teardown stops the process. The test checks application-root discovery and traversal, retained AX actions, Unicode value writes, numeric-domain validation, read-only/protected/disabled rejection, protected-text omission, UTF-16-position ranges and explicit selection, cross-owner rejection, byte-budget failure and subsequent healthy reads. Failed readiness or missing native behavior fails this opt-in test; it does not silently fall back to another desktop.

Run the ordinary Rust tests on the Mac:

```bash
cargo test --locked --package a13n-envd --lib accessibility
```

To run the direct native Rust test, start the fixture separately, retain its PID, and run the test from the trusted launching context:

```bash
"$HOME/ax-lab/accessibility-fixture" "$HOME/ax-lab/direct-state.json" &
fixture_pid=$!
A13N_AX_FIXTURE_PID="$fixture_pid" cargo test --locked --package a13n-envd \
  --lib native_appkit_read_action_text_and_owner_validation -- --ignored --test-threads=1
kill "$fixture_pid"
```

## Native validation boundaries

Cross-compiling Rust checks the bindings and test types. Compiling the AppKit fixture checks its source against the installed SDK. Native Rust execution and the HTTP acceptance test establish runtime behavior; record the macOS version, architecture and granted permissions with those results.

AX exposes its own hierarchy for `raw`, `control` and `content`; UIA tree filters are not applied. Native spans retain AX positions, independent of UTF-8 bytes. AX metadata reports native writability. Unsupported native range operations remain explicit. The fixture does not expose every AX setter or application-specific attribute. Check multi-selection lists/tables, window/main/minimized/expanded/disclosing changes, attributed text and application-specific actions with providers that expose them.

Separately check permission denial, application termination and helper restart. For screenshot projection and point lookup, test a Retina display and a mixed-scale multi-monitor layout, including negative display origins, changed displays and controls crossing display boundaries. Use only returned image projections; native logical AX bounds are not image pixels. These checks require appropriate native hardware and an authorized screenshot permission.
