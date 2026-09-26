# macOS Computer Use

Use a Mac's shared desktop from a Harness UI conversation: envd sends screenshots directly to the model, executes bounded input actions, and WebUI shows captured images with the tool results. The daemon connects outward over reverse WebSocket; the Mac needs no inbound listener.

## Requirements and limits

- Run a desktop-enabled envd build on macOS in the logged-in graphical session.
- Grant Screen Recording and Accessibility permission in macOS System Settings to the process/launcher macOS identifies for envd. Restart that process after changing permissions if required by macOS. The daemon reports missing permission; it does not grant it.
- Use disabled Sandbox and inherited egress. Restricted Sandbox, denied/controlled egress, Linux and Windows reject computer-use opt-in rather than controlling a host desktop outside the selected boundary.
- Select an Agent with `dynamic_environment` tools and a model that accepts images.

This is the real shared desktop, not a private browser or VM. Input can send messages, modify files, or invoke other applications using the logged-in user's authority. A selected working directory does not restrict those effects. Humans and other agents may change focus or content between screenshot and click. Do not operate a sensitive desktop unattended.

## Connect the Mac

In WebUI, open **Settings → Environments → Connect Device**. Run the displayed command on the Mac, adding the explicit opt-in:

```console
a13n-envd connect https://your-harness-ui.example.com --computer-use true
```

Use your actual reachable WebUI origin. A same-machine loopback HTTP origin is also supported; use HTTPS for a remote Host. Verify the pairing code and approve it in WebUI. Keep the daemon running. Existing paired connections can be restarted with the same flag without creating another binding.

The equivalent settings are `"computer_use": true` in daemon JSON or `A13N_ENVD_COMPUTER_USE=true`. It defaults off and is independent of `full_control`. Enabling computer use does not enable shell commands, and enabling shell commands does not enable computer use. The ordinary configuration precedence applies; `--computer-use false` overrides an environment opt-in.

## Authorize the conversation

1. Open **Working environments** in the composer, or **Conversation details → Configuration** for saved next-Run settings.
2. Add an environment for the connected Mac, choose an existing working directory, and name the alias `desktop`.
3. Select **Desktop observation and control** under **Allowed actions**. The default **Files and execution** excludes desktop actions. The desktop preset grants only desktop actions; add a separate binding if the agent also needs file/shell tools.
4. Select the default environment deliberately, or ask the agent to use alias `desktop` explicitly.
5. Save the enclosing selection and start a new Run.

Pairing approves the Device connection, not desktop model authority. Changing next-Run configuration does not alter a running operation.

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

For control, ask the agent to observe first, act on the returned `observation_id` and image pixel coordinates, then observe again to verify. Pointer references are bounded to the Run/Session and expire; a stale or layout-changed reference requires a new observation. Keyboard/text input uses current foreground focus, so click the intended field before typing.

Supported tools:

| Tool                              | Purpose                                                                   |
| --------------------------------- | ------------------------------------------------------------------------- |
| `computer_describe`               | List displays and native permission readiness                             |
| `computer_observe`                | Capture a display (primary by default), maximum dimension 256–2048 pixels |
| `computer_click`, `computer_move` | Use a position in the returned image                                      |
| `computer_drag`                   | Complete a bounded drag and release the button                            |
| `computer_scroll`                 | Scroll at a position; positive deltas mean right/down                     |
| `computer_type_text`              | Type literal Unicode text in current focus                                |
| `computer_press_keys`             | Press and release a chord such as `["meta", "a"]`                         |

Key names use `meta` for Command, `alt` for Option, `enter`, `page_up`, and `page_down`. Input results report native event effect and cleanup status. `executed` is not proof of application-level success. A partial or unknown effect, incomplete release, disconnect or interrupted Run must not be blindly retried; inspect the desktop before deciding what to do next.

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

Portable integration tests use a scripted desktop peer over a real reverse WebSocket, with actual client, Harness, App, live/history and authenticated image retrieval. They do not prove native macOS capture, Retina geometry, Accessibility permission or event posting. Validate those on a Mac: first describe/capture, then perform harmless input in a disposable text document, verify the next screenshot, and test permission denial and display-layout changes. No production fake-desktop mode is provided.
