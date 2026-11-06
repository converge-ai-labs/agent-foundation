---
title: Use the Host browser
description: Prepare browser automation on the WebUI Host, browse with the Agent, and capture or annotate its window.
---

Use the Host browser when the Agent needs to operate a website or you want to review a page together. First [start WebUI](webui.md#start-the-server). The browser runs on the server Host, separately from the browser displaying WebUI.

## Browse on the Host

**Browser** lets the Agent browse with Chromium on the server while you watch its native window. Linux shows a shared private desktop; macOS shows only the conversation's owned browser window. Browsing requires a Linux or macOS Host, computer sharing, and a Full Control Run. Windows browsers can view a supported Host; Windows cannot host this desktop.

### First-time setup

1. [Install Host components](#install-host-components) on the server, not on the device displaying WebUI. Leave computer sharing enabled when starting WebUI.
2. Start WebUI with the default `browser.mode: auto`. Harness UI detects installed components and prepares the shared browser. Check **Settings → Environments → Browser** for runtime status and components. If startup fails, resolve the error and use **Retry browser startup**.
3. Start a **Full Control** conversation and ask the Agent to browse. A Run receives browser tools when the browser is ready at admission; first navigation or tab creation opens the conversation's window. You can also choose **Browser → Open browser**, including during a Run.
4. First live browser use reveals the viewer without moving keyboard focus. If Files/Changes is open or the screen is narrow, choose **View browser**. Closing the viewer keeps the browser running and prevents another automatic reveal in that Run.
5. For login or manual correction on Linux, choose **Take control**, finish the action, then **Hand back to Agent**. On macOS, choose **Pause automation**, operate the browser locally on the Mac, then **Resume automation**.

Browsing needs no per-conversation switch or manual Capability entry. Use **Settings → Environments → Browser** for the global Auto/Disabled mode and executable overrides; save and restart after changing them. Ordinary chat, Files and Shell do not allocate conversation browser pages. Browser startup never holds up a Run. If the browser is unavailable, the Agent receives the reason and continues without browser tools. After recovery, start a later Run to use the browser; tools are not added to an active Run.

### Install Host components

**Linux:** install Chrome, Chromium, or Edge using your distribution or vendor instructions. On Debian/Ubuntu, prepare TigerVNC with:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends tigervnc-standalone-server
```

Optional window controls and TigerVNC resizing:

```bash
sudo apt-get install --no-install-recommends openbox x11-xserver-utils
```

These commands install desktop components, not Chromium. Harness includes cdp-use and generates VNC passwords/X11 authentication; no separate Node.js, `vncpasswd`, `xauth`, or websockify setup is required. In Docker, install components inside the server image/container. **Manual installation commands** are instructions for you to run, not UI-executed installation.

Run as non-root with Chromium's sandbox enabled. Root requires an explicit `--browser-no-sandbox` or `A13N_HARNESS_UI_BROWSER_NO_SANDBOX=1`; this removes Chromium's sandbox protection. `--browser-sandbox` overrides that environment opt-out. WayVNC/Sway independently requires a non-root account.

**macOS:**

1. Install Chrome, Chromium, or Edge and run Harness UI as the logged-in desktop user.
2. Choose **Browser → Open browser** to open a window; Chromium is the only required browser component.
3. For optional preview on macOS 14 or later, grant Screen Recording permission to the application running Harness UI. **Open Mac Screen Recording settings** opens the Host settings; you grant permission there.
4. Keep the Mac unlocked and the browser window visible. After granting permission or unlocking, choose **Reconnect**. Restart the Host application if macOS asks you to.

Preview captures only the exact conversation window created by Harness UI, including the real address bar. Missing permission or capture failure leaves automation available. The viewer stays read-only: it forwards no keyboard, mouse, or clipboard input. Screen Sharing and VNC passwords are unnecessary; old saved VNC credentials and the former password environment variable are unused.

Capture never falls back to the desktop, a personal browser, or another conversation's window. Separate popup windows and native dialogs are outside the preview. A closed or unidentified bound window needs **Reset browser** to establish a new preview. Use local zoom; Viewport presets are disabled and the physical display resolution stays unchanged.

### Detect and select executables

Automatic selection uses installed browsers, preferring Chrome, Chromium, then Edge. Linux searches the server's `PATH`; macOS checks `/Applications` and `~/Applications`. Install missing programs before **Scan** or **Retry browser startup**. Restart the server after changing its `PATH`.

| Linux display choice | Required components                                | Resizing                      |
| -------------------- | -------------------------------------------------- | ----------------------------- |
| TigerVNC, preferred  | `Xtigervnc` or `Xvnc`                              | Optional RandR/xrandr support |
| x11vnc               | x11vnc and Xvfb                                    | Fixed desktop                 |
| WayVNC               | Compatible WayVNC, headless Sway, non-root account | Fixed desktop                 |

Automatic discovery prefers a complete adapter in that order. WayVNC needs private Unix sockets and input-disabled viewing support. Linux desktops use private display/runtime resources, not the user's existing desktop. An X11 window manager such as Openbox, Fluxbox, IceWM, or twm supplies window dragging and close controls; basic browsing works without it.

To override a path, open **Settings → Environments → Browser**, choose a detected executable or **Other** with an absolute path, then save and restart. **Automatic** clears an override. Invalid explicit paths fail instead of falling back. **Scan** checks the current draft and refreshes required/optional component results without saving or launching. Successful detection can still be followed by a startup error, such as missing shared libraries or display permissions.

**Active configuration** shows the settings used by this App. Saved browser changes take effect after restarting Harness UI. Retry uses the active configuration, not saved pending changes or the unsaved draft, and opens no conversation pages.

On macOS, settings show optional **Window preview** instead of a VNC executable selector or password.

The shared profile lives at `<data_root>/browsers/profile`, normally `~/.a13n-harness-ui/data/browsers/profile`. It has no separate location setting. Conversations and Projects share one Chromium instance and profile, with separate windows/Agent tabs. Personal profiles are not imported, and existing profile directories are not moved or merged.

On Linux, the managed browser uses basic password storage to avoid waiting for a locked desktop keyring. Saved passwords and cookies are **not protected by the OS keyring**; protect the profile and its backups as credentials. The system keyring and macOS credential storage are unchanged. Restart Harness UI after upgrading to apply this behavior. The profile is retained, but sites whose login data used keyring encryption may require another sign-in.

### Start and control the browser

Viewing starts read-only. On Linux, **Take control** pauses browser tools and page collection across all conversations and focuses this conversation's window. Only one viewer controls input. **Hand back to Agent** revokes human input and requires fresh page observation. You can hand back from another conversation, including one with no open tabs. Taking control from another conversation transfers input and focuses its window while all Agent browser tools stay paused.

Each model request includes compact Browser availability context. During takeover, the Agent can call `browser_notify_on_handback` to request a reminder without accessing the page or waiting. A blocked page-tool call also registers the same reminder. Repeated requests coalesce. If control has already returned, the tool reports availability without creating a future reminder. The Agent can finish independent work or end its turn instead of polling. Handback notifies only these conversations, including those whose turn has ended, so the Agent can observe afresh and continue relevant work. Pending approvals still require your decision. Stop, archive, and clearing context cancel pending notices. Notices are kept only while this server and browser runtime remain running; a previous Goal is not resumed.

To change devices, open the same Thread in another browser and choose **Take control here**. The previous controller loses input while the Agent stays paused; opening the Thread alone remains read-only. After a disconnection, choose **Reconnect control** to reconnect while keeping the Agent paused, or **Hand back to Agent** to resume automation.

The toolbar shows **Taking control…** while current operations finish, then **Connecting control…** and **Waiting for the first frame…**. Wait for **You have control** before typing or clicking. Connection can retain the previous picture with a pending message; a timeout offers retry, and you can hand back while connection is pending or failed.

On macOS, choose **Pause automation** before interacting with the real browser locally on the Mac. This also pauses browser tools and page collection across conversations. Choose **Resume automation** when done; the Agent must observe the updated page before continuing. Closing the viewer leaves automation paused. The WebUI preview remains read-only throughout.

On Linux, click the remote desktop before typing. Local input methods compose on your device and paste committed text into the remote field. The field must accept paste and the Host needs suitable fonts. Linux Unicode transfer requires an extended-clipboard server such as TigerVNC or WayVNC; the tested x11vnc server lacks it.

During Linux human control, focused desktop text clipboards synchronize up to 65,536 characters. HTTPS or localhost and browser clipboard permission are required. Client Command shortcuts map to Linux Ctrl shortcuts. Handback, disconnection, and closing the viewer stop synchronization. The remote bridge transfers text, not images/files; file dialogs use Host files.

On Linux with supported resizing, take control and choose a Viewport preset for responsive layout checks. The dimensions describe the page's CSS viewport; other viewers see the same size. Resizing preserves login and avoids reload. It does not emulate a mobile OS, user agent, or touch device. Close conversation tabs and Reset retain the selection until server restart.

| Action                                           | What remains                                                                 |
| ------------------------------------------------ | ---------------------------------------------------------------------------- |
| Close drawer, switch conversation, finish Run    | Browser continues running                                                    |
| **Browser options → Close conversation tabs**    | Close current windows; keep the Run binding so the same Run can reopen pages |
| **Reset browser**                                | Recreate this conversation's window at a visible position                    |
| **Reconnect**                                    | Reconnect the viewer without restarting Chromium                             |
| Close last conversation tab                      | Binding stays enabled; Agent navigation or Open browser can create another   |
| Exit Chromium or lose a required display process | Shared runtime and all windows stop                                          |

Reset or recovery after failure creates a new read-only session. Choose **Try again** after the shared runtime stops. Send a new prompt for the affected conversation to use its replacement window; old actions are not replayed, and waiting tools fail rather than remaining paused indefinitely. Other conversations retain their windows on a single-conversation Reset. Unsaved page data can be lost on a crash. After server restart, Auto mode prepares the shared browser again. Closing individual tabs during human control leaves automation paused until handback. **Close conversation tabs** also releases human control when that conversation is the controller.

Linux human controllers can see and operate other windows on the shared private desktop. macOS preview is limited to one window, while website logins, saved passwords, and preferences still share the Chromium profile across conversations. Browser authority and networking belong to the Host, outside Sandbox isolation. Linux uses noVNC; its source/license notice is served at `/assets/LICENSE.noVNC`, with other bundled notices at `/third-party-licenses.txt`.

### Capture and annotate a screenshot

1. In a connected Browser drawer, click the camera, **Capture and annotate screenshot**. It freezes the currently rendered Linux desktop or macOS window preview locally, including browser controls, while the live browser continues.
2. Crop or add arrows, shapes, freehand marks, text, or mosaic with a mouse, pen, or touch pointer. Use **Opaque redaction** for private details; mosaic is only a visual effect.
3. Use **Select** or the **Annotations** picker to choose a mark. Move/resize it, Delete it, or nudge with arrows (Shift for larger steps). Ctrl/Cmd+Z undoes; Ctrl/Cmd+Shift+Z redoes. Shift constrains shapes/arrow angles.
4. Finish text with Ctrl/Cmd+Enter or cancel it with Escape. Outside text input, **Copy PNG** or Ctrl/Cmd+Enter copies the result and returns focus to chat.
5. Paste explicitly into the composer, inspect the attachment, add your instruction, and send.

Capture needs no takeover and does not pause the Agent. The editor suspends only this viewer's remote input and text synchronization. Copy waits for previously issued text clipboard writes. Capture/copy never upload, attach, or send automatically.

PNG dimensions follow the source-pixel crop, independent of preview zoom or screen density. Keyboard controls support editing existing marks; creating marks/crops requires a pointer. Copy/export failure preserves edits. Retry or use **Download PNG**, which leaves the editor open. Closing asks for confirmation when text or undo/redo history would be discarded. Pasting into chat follows ordinary attachment limits.
