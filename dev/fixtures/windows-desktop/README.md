# Native Windows desktop fixture

These opt-in tests operate a disposable Windows desktop through the production EIP HTTP transport. They are not desktop mocks and must not run against a personal or production desktop.

## Provisioning

Use a legitimately licensed or officially evaluated Windows VM. Run the current `a13n-envd` build as the ordinary signed-in user with explicit computer-use opt-in, disabled Sandbox and inherited egress. Keep UAC, secure desktop and other Windows protections enabled. Do not run envd elevated or as a service. Restrict the authenticated test listener to an owned loopback/forwarded endpoint.

Create a writable guest directory. The tests upload the fixture scripts there and start each GUI fixture as a Session-owned process. Configure a 1280×800 primary screen for the fixed target coordinates. Run the tests serially; multiple tests sharing this desktop race focus and input.

From the repository root on the test driver, for example:

```bash
export A13N_WINDOWS_TEST=1
export A13N_WINDOWS_ENDPOINT=http://127.0.0.1:18725
export A13N_WINDOWS_CREDENTIAL_FILE=/path/to/private/credential
export A13N_WINDOWS_DEVICE_ID=windows-lab
export A13N_WINDOWS_DIRECTORY=/C:/Users/lab/Desktop/lab
uv run pytest packages/a13n-harness-ui/tests/test_computer_windows.py -v
```

The credential file contains only the daemon credential. Never commit it or put its value in logs. Clear unrelated Harness UI service-auth environment variables before running the test suite.

## Coverage and boundaries

- Native JPEG pixels before/after application changes; all pointer buttons; vertical/horizontal wheel steps; drag endpoint and cancellation cleanup.
- Unicode Chinese, supplementary-plane emoji, CRLF and Tab in a multiline WinForms control; physical chords and navigation-based selection.
- Operation-ID deduplication, invalid parameters and Session-local observation references.
- Swapped primary/secondary buttons and reduced-size screenshot coordinate mapping.
- French-layout physical input and independently held-key rejection. `external-key.ps1` is test setup only: it holds/releases a foreign key outside computer-use; the asserted action still uses native EIP. This is not a claim that all per-application layout combinations are covered.
- A real Harness UI App with a deterministic FunctionModel receives native screenshot bytes, invokes native input and stores authenticated display attachments matching those bytes. Browser DOM/reload rendering requires a separate browser review, not this Python assertion alone.

The GUI's timer reports application events and observed held state; it does not inject input. A transient file-sharing collision with an EIP reader is retried on the next timer tick. The mouse-swap test restores normal mapping and the held-key test releases its setup key in `finally`. Use a disposable VM because abnormal termination can interrupt cleanup and layout changes affect the shared desktop.

## Unavailable desktop check

Run the ordinary tests while the desktop is unlocked. Separately, with envd already running, manually lock the VM or open a UAC secure-desktop prompt without approving it. Then run only:

```bash
A13N_WINDOWS_UNAVAILABLE=1 uv run pytest \
  packages/a13n-harness-ui/tests/test_computer_windows.py \
  -k desktop_unavailable -v
```

Run this check separately for the lock screen and the UAC prompt: they are distinct readiness conditions. Confirm the lock screen through the VM console; a black screen alone is insufficient. The lock-screen curtain can leave the ordinary desktop's `UOI_IO` flag true, so it exercises the additional session lock-state check.

This verifies describe/capture denial and pre-dispatch rejection of text and keys. Restore the desktop through the normal VM console and rerun the ordinary tests to check recovery; do not disable UAC, switch the daemon to the secure desktop, or treat console input as computer-use test evidence.

The fixed-resolution VM tests do not establish physical multi-monitor, mixed-DPI, RDP reconnect, protected-content or raw-input-application behavior. Inspect those on suitable native hardware separately. Remove only the VM, volumes, images and installation/build media created for the test; preserve code and evidence and leave unrelated containers/caches alone.
