"""Independent GTK paste fixture; only reset/focus are controlled by the test harness.

Actual text changes arrive through X11/VNC. Saved state reads the widget, not the input request.
Requires system Python GI/GTK3 and python3-xlib in a disposable desktop image.
"""

import json
import subprocess
import sys
from pathlib import Path

import gi
from Xlib import display

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

window = Gtk.Window(title="Envd clipboard acceptance")
window.set_default_size(900, 600)
text = Gtk.TextView()
scroll = Gtk.ScrolledWindow()
scroll.add(text)
window.add(scroll)
window.show_all()
window.present()
text.grab_focus()
x = display.Display()
clipboard = x.intern_atom("CLIPBOARD")
state_file = Path("/workspace/paste-state.json")
command_file = Path("/workspace/paste-command.json")
last = None
owner_process = None


def update():
    global last, owner_process
    if command_file.exists():
        command = command_file.read_text()
        if command != last:
            last = command
            request = json.loads(command)
            if request["action"].startswith("owner:"):
                if owner_process:
                    owner_process.terminate()
                    owner_process.wait()
                owner_process = subprocess.Popen(
                    [sys.executable, "/tmp/clipboard-owner.py", request["action"].split(":", 1)[1]]
                )
            elif request["action"] == "reset":
                text.get_buffer().set_text("")
            elif request["action"] == "replace_clipboard":
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("replacement-owner", -1)
            window.present()
            text.grab_focus()
    buffer = text.get_buffer()
    owner = x.get_selection_owner(clipboard)
    value = {
        "text": buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True),
        "owner": owner.id if owner else 0,
        "keys_held": any(x.query_keymap()),
        "ready": window.is_active() and text.has_focus(),
        "probe": Path("/workspace/clipboard-probe").read_text() if Path("/workspace/clipboard-probe").exists() else "",
    }
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(state_file)
    return True


GLib.timeout_add(10, update)
Gtk.main()
