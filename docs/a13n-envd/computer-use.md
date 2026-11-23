---
title: Computer use
description: Enable desktop screenshot, mouse/keyboard input, and native accessibility inspection in Envd on macOS, Linux (X11), and Windows.
---

Enable `computer_use` in `a13n-envd` when an Agent needs to view a desktop screen, click or type, or inspect native UI accessibility trees.

## Enable Computer Use

Pass `--computer-use` or set `A13N_ENVD_COMPUTER_USE=true` (requires disabled OS Sandbox and inherited egress):

```sh
A13N_ENVD_COMPUTER_USE=true a13n-envd connect https://host.example.com
```

Envd verifies desktop readiness before starting its transport (bounded by `computer_use_permission_timeout_ms`, default 120 seconds).

## Native accessibility

Envd exposes platform-native accessibility trees alongside screenshots (`computer_accessibility_describe`, plus `computer_ax` on macOS, `computer_uia` on Windows, and `computer_atspi` on Linux). Grant specific `environment.computer.a11y_*` operations in the mount's permission ceiling to allow semantic UI inspection and actions.

## Restore macOS permissions after an upgrade

If macOS reports missing **Screen Recording** or **Accessibility** permission after replacing the `a13n-envd` binary:

1. Open **System Settings → Privacy & Security → Screen Recording** and **Accessibility**.
2. Remove the old `a13n-envd` entry, re-add the updated binary (or toggle it off and on), and restart Envd.
