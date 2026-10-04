---
title: Desktop computer use
description: Let an Agent see and operate a shared macOS, Linux X11, or Windows desktop through Envd.
---

Use a macOS, Linux X11 or Windows shared desktop from a Harness UI Thread. Envd sends screenshots directly to the model and executes bounded input actions. WebUI shows the captured images with the tool results. The daemon connects outward over reverse WebSocket; the desktop machine needs no inbound listener.

## Requirements and limits

- Run Envd in the logged-in macOS or Linux X11 graphical session. Linux needs an existing X11 server with RandR 1.5, XKB and XTEST, a TrueColor root visual, and the correct `DISPLAY` and X11 authentication. Wayland and XWayland are not supported.
- Grant Screen Recording and Accessibility permission in macOS System Settings to the process/launcher macOS identifies for Envd. Restart that process after changing permissions if required by macOS. The daemon reports missing permission; it does not grant it.
- On Windows, run Envd in the intended signed-in, unlocked interactive user session. Services, disconnected sessions, login/UAC secure desktops and higher-integrity targets are not supported.
- Use disabled Sandbox and inherited egress. Restricted Sandbox and denied/controlled egress reject computer-use opt-in rather than controlling a host desktop outside the selected boundary.
- Select an Agent with `dynamic_environment` tools and a model that accepts images.

This is the real shared desktop, not a private browser or VM. Input can send messages, modify files, or invoke other applications using the logged-in user's authority. A selected working directory does not restrict those effects. Humans and other agents may change focus or content between screenshot and click. Do not operate a sensitive desktop unattended.

## Connect the desktop

In WebUI, open **Settings → Environments → Connect Device**. Run the displayed command in the desktop's graphical session, adding the explicit opt-in:

```console
a13n-envd connect https://your-harness-ui.example.com --computer-use true
```

Use your actual reachable WebUI origin. A same-machine loopback HTTP origin is also supported; use HTTPS for a remote Host. Compare the verification code, then approve it in WebUI. Keep the daemon running. You can restart an existing paired connection with the same flag; this does not create another Device registration.

The equivalent settings are `"computer_use": true` in daemon JSON or `A13N_ENVD_COMPUTER_USE=true`. It defaults off and is independent of `full_control`. Enabling computer use does not enable shell commands, and enabling shell commands does not enable computer use. The ordinary configuration precedence applies; `--computer-use false` overrides an environment opt-in.

### Wait for macOS authorization

After Host pairing, Envd checks **Screen Recording** and **Accessibility**, requests missing permissions, and waits up to **120 seconds**. Grant access in **System Settings → Privacy & Security** to the process or launcher macOS identifies for Envd. The daemon starts its reverse WebSocket only after both checks succeed. The same gate precedes the HTTP listener and stdio protocol processing; disabled computer use does not prompt or wait.

Progress and errors go to stderr, never stdio protocol stdout. A timeout or Ctrl+C exits with a nonzero status and does not connect a partially authorized Device. If macOS requires a restart, restart the identified launcher and Envd, then repeat the command. Rejecting or dismissing a prompt is not always distinguishable from a pending grant; Envd waits until the deadline and reports the permissions still missing. Runtime revocation still fails observation/input even after successful startup.

For a longer wait, set `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS=300000`, or include `"computer_use_permission_timeout_ms": 300000` in daemon JSON. This positive duration does not enable computer use by itself.

For a parent launching stdio Envd, forward/drain stderr during startup and configure the client's `initialization_timeout` longer than the permission wait plus startup overhead (for example 135 seconds for the default wait). The Python client's default 10-second initialization timeout is not a human-authorization timeout. Reverse WebSocket clients initialize only after Envd connects; HTTP clients must wait until the listener is available.

### Linux X11 readiness

Launch from a terminal in the intended X11 session so Envd inherits `DISPLAY` and, when set, `XAUTHORITY`. It checks authenticated X11 access and required extensions before opening any EIP transport. It does not start Xorg/Xvfb, change `xhost` rules, mount host sockets, or request elevated input-device access. Do not disable X11 authentication to make a failed connection work.

Linux supports screenshots, click, move, drag, physical key chords, and discrete wheel steps. Use `computer_describe` to inspect `scroll_units`, then pass `unit="steps"` with at most 100 steps per axis. Pixel scrolling is rejected before pointer movement. Linux does **not** expose `computer_type_text`; it does not modify the keyboard map or clipboard to imitate Unicode text entry. Physical chords depend on the active keyboard layout and are not a literal-text replacement. `meta` means Super, and `alt` means Alt.

A lost X server requires a new Session and a fresh screenshot; Envd will not silently reconnect an old geometry reference to a replacement server. A Wayland launch environment or a server advertising XWAYLAND is rejected, even when `DISPLAY` is also set.

### Windows readiness and input

Start Envd from an ordinary terminal on the desktop you want to control. It checks that the current Windows session is active and its desktop receives input before starting any EIP transport. There is no generic Windows desktop-permission popup for this path. Keep UAC and other Windows protections enabled; do not run Envd as administrator merely to suppress an error. Pairing and the Harness action ceiling are separate from operating-system desktop access.

Windows supports screenshots, pointer gestures, physical scan-code chords, literal Unicode text and wheel steps. Use `unit="steps"` after checking `computer_describe`; pixel scrolling is rejected before pointer movement. `meta` is the Windows key. Capture and input coordinates use physical pixels even when display scaling is enabled. Protected/excluded content can remain black in a screenshot.

Text uses UTF-16 Unicode input events, not clipboard replacement or a keyboard-layout guess. A CRLF pair is submitted as one carriage return to avoid duplicate line breaks. Applications using raw keyboard input may not accept Unicode entry, and standalone newline/Tab behavior depends on the target control. Verify the application after typing. Already-held required keys/buttons or text modifiers cause a pre-dispatch conflict; ask the user to release them instead of clearing their input.

If the desktop becomes locked or disconnected, reconnect/unlock it manually and capture a new observation. Input failure alone does not prove UIPI caused it, nor does a native success count prove the application accepted it. Partial/unknown effects, transport loss and cleanup failure require inspection before another action, not automatic replay.

## Bind the desktop to a Thread

1. Open **Environments** in the composer, or **Conversation details → Configuration** for saved next-Run settings.
2. Add an environment for the connected desktop.
3. Choose an existing working directory.
4. Name the alias `desktop`.
5. Keep **Full control** (the binding's action preset, not the daemon's `full_control` setting), the default under **Allowed actions**. The preset includes file access, command execution and desktop observation/control where enabled on the Device. **Read only** allows file reading and browsing without changes, commands or desktop access. For an older binding, explicitly select **Full control** to replace its existing action ceiling.
6. Select the default environment deliberately, or ask the Agent to use alias `desktop` explicitly.
7. Save the enclosing selection and start a new Run.

Pairing approves the Device connection. It does not let the model use the desktop; the binding's allowed actions do. Changing next-Run configuration does not alter a running operation.

A saved Project or Thread binding can narrow access to observation only:

```yaml
environment_bindings:
  - device_id: device-your-mac
    alias: desktop
    working_directory: /Users/your-account
    permission_ceiling:
      operations:
        - environment.computer.describe
        - environment.computer.observe
default_environment: desktop
```

Use actual configured IDs and paths. Add the six input actions (`click`, `move`, `drag`, `scroll`, `type_text`, `press_keys` under `environment.computer.*`) only when control is intended. Existing exact ceilings are preserved by the editor unless you select a replacement preset.

## Observe, act, verify

Try a bounded first prompt:

> Use the desktop environment. Describe its displays and capture a screenshot. Do not click or type yet.

The model receives image bytes directly, without a save-and-view round trip. Expand the **Observe desktop** tool result to see the captured image; open it for a larger preview. The image is historical evidence, not a live desktop viewer. The display copy is stored in the Thread's temporary files and can expire under scratch retention. Model continuation storage has its own lifecycle. A display-copy failure never triggers another capture or repeats input.

For control, ask the Agent to observe first, act on the returned `observation_id` and image pixel coordinates, then observe again to verify. Pointer references are bounded to the Run/Session and expire; a stale or layout-changed reference requires a new observation. Keyboard/text input uses current foreground focus, so click the intended field before typing.

Supported tools:

| Tool                              | Purpose                                                                   |
| --------------------------------- | ------------------------------------------------------------------------- |
| `computer_describe`               | List displays and native permission readiness                             |
| `computer_observe`                | Capture a display (primary by default), maximum dimension 256–2048 pixels |
| `computer_click`, `computer_move` | Use a position in the returned image                                      |
| `computer_drag`                   | Complete a bounded drag and release the button                            |
| `computer_scroll`                 | Scroll with an advertised unit; positive means right/down                 |
| `computer_type_text`              | Type literal Unicode text in current focus (macOS and Windows)            |
| `computer_press_keys`             | Press and release a chord such as `["meta", "a"]`                         |

Key names use `meta` for Command/Super/Windows, `alt` for Option/Alt, `enter`, `page_up`, and `page_down`. Input results report native event effect and cleanup status. `executed` is not proof of application-level success. A partial or unknown effect, incomplete release, disconnect or interrupted Run must not be blindly retried; inspect the desktop before deciding what to do next.

Screenshot storage shares the file-transfer staging byte and object budgets. Each capture needs 4 MiB of free staging capacity before it begins; after capture, only the actual encoded image length remains charged until the bytes are released. Close readers promptly rather than accumulating unread screenshots.

## Python client

Given a ready `EIPSession`, the high-level reader uses the same bounded, SHA-256-verified raw transfer machinery as file downloads:

```python
async with session.observe_computer(max_dimension=1280) as reader:
    screenshot = b"".join([chunk async for chunk in reader])
    observation = reader.opened.observation
# The image reader is closed, while the Session's geometry reference remains usable.
```

Use the generated typed `computer_*` client calls for input, with a fresh operation context. Do not retry an ambiguous action with a new operation ID. See [Python EIP client](python-client.md) for Session construction and transport ownership.

## Validation

The opt-in [Linux desktop fixture](https://github.com/converge-ai-labs/agent-foundation/tree/main/dev/fixtures/linux-desktop) runs Xvfb, Openbox, a real Tk application, native Envd, reverse WebSocket and Harness UI in one disposable, non-root container. It requires no host display mounts or published ports. It verifies actual GUI events and changed screenshot pixels rather than returning a scripted desktop image.

Portable integration tests use a scripted desktop peer over a real reverse WebSocket, with actual client, Harness, App, live/history and authenticated image retrieval. They do not prove native macOS or Windows capture/input, Retina or mixed-DPI geometry, OS permission behavior or event posting. Validate macOS behavior on a Mac: first describe/capture, then perform harmless input in a disposable text document, verify the next screenshot, and test permission denial and display-layout changes. Windows native behavior is not covered by these steps. No production fake-desktop mode is provided.
