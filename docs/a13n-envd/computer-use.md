---
title: Desktop computer use
description: Drive Linux, macOS, and Windows graphical desktops through Envd with screenshot observation, pointer/keyboard actions, and native accessibility trees.
---

Enable `computer_use` in `a13n-envd` so an image-capable Agent can observe displays, send pointer/keyboard input, inspect native accessibility trees, and read/write clipboard text.

## Platform requirements

| Platform | Desktop requirement                                                             | Input & notes                                                                      |
| -------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| macOS    | Logged-in GUI session + **Screen Recording** and **Accessibility** granted      | Pointer, key chords, `computer_type_text`, scroll                                  |
| Linux    | Authenticated X11 (`DISPLAY` + `XAUTHORITY`, RandR 1.5, XKB, XTEST; no Wayland) | Pointer, key chords, scroll steps; enter text by writing the clipboard and pasting |
| Windows  | Signed-in, unlocked interactive user desktop                                    | Pointer, scan-code chords, `computer_type_text`, scroll steps                      |

Computer use requires disabled Envd OS Sandbox (`sandbox.mode = "disabled"`) and `inherit` egress.

## Connect and bind the desktop

1. From a terminal inside the target graphical session, pair Envd with `--computer-use true`:

```console
a13n-envd connect https://your-harness-ui.example.com --computer-use true
```

2. Approve the verification code in WebUI (**Settings → Environments**) and grant any OS desktop permissions (Envd waits up to `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`, default 120s).
3. Attach the Device to your Project or Thread with **Full control** (or an explicit `permission_ceiling` such as `environment.computer.describe`, `.observe`, `.zoom` for read-only observation).

### Restore macOS permissions after an upgrade

Replacing the `a13n-envd` binary can invalidate macOS TCC grants even when the toggle still looks enabled:

1. Stop Envd, open **System Settings → Privacy & Security → Accessibility** and **Screen Recording**.
2. Remove and re-add the `a13n-envd` binary (or launcher app), enable both toggles, and restart Envd.

## Screen, input, and clipboard tools

| Tool                                                                  | Purpose                                                                              |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `computer_describe` / `computer_observe`                              | List displays and capture a screenshot (`observation_id`, max dimension 256–2048 px) |
| `computer_zoom`                                                       | Crop high-res detail from `observation_id`; keep clicking in full-screen coordinates |
| `computer_click`, `computer_move`, `computer_drag`, `computer_scroll` | Pointer actions using coordinates from the latest `observation_id`                   |
| `computer_type_text`, `computer_press_keys`                           | Enter Unicode text (macOS/Windows) or press chords such as `["meta", "v"]`           |
| `computer_read_clipboard`, `computer_write_clipboard`                 | Read/write up to 16 KiB UTF-8 clipboard text (`environment.computer.*_clipboard`)    |

## Native accessibility

Envd exposes bounded semantic UI trees and actions on macOS (AX), Windows (UIA), and Linux X11 (AT-SPI via `org.a11y.Bus`):

- **Read tools:** `computer_accessibility_describe`, `_roots`, `_inspect`, `_find`, `_read`, `_range`, `_range_read` (grant `environment.computer.a11y_*`).
- **Mutation tools:** `computer_ax`, `computer_uia`, `computer_atspi`—each underlying native action must be explicitly permitted in `permission_ceiling` (for example, `environment.computer.ax_perform_action` or `uia_invoke`).

## Python EIP client example

```python
from a13n_envd_client.eip.v1 import ComputerRegion

async with session.observe_computer(max_dimension=1280) as reader:
    screenshot = b"".join([chunk async for chunk in reader])
    obs = reader.opened.observation

async with session.zoom_computer(
    observation_id=obs.observation_id,
    region=ComputerRegion(left=0, top=0, right=obs.width // 2, bottom=obs.height // 2),
) as reader:
    detail = b"".join([chunk async for chunk in reader])
```
