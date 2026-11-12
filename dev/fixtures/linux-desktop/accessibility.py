"""Real GTK/ATK provider with an independent application-side effect log.

Run with the system Python inside the test's disposable Xvfb/D-Bus session.
"""

import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

state_path = Path(sys.argv[1])
state = {"ready": False, "clicks": 0, "text": "", "value": 25, "selected": [], "closed": False}


def save():
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.replace(state_path)


def named(widget, name):
    widget.get_accessible().set_name(name)
    return widget


window = Gtk.Window(title="AT-SPI acceptance fixture")
window.set_default_size(800, 650)
window.connect("destroy", Gtk.main_quit)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
box.set_border_width(16)
window.add(box)
button = named(Gtk.Button(label="Apply changes"), "Apply changes")


def clicked(_button):
    state["clicks"] += 1
    save()


button.connect("clicked", clicked)
box.pack_start(button, False, False, 0)
entry = named(Gtk.Entry(), "Unicode editor")
entry.set_text("Initial text")


def changed(widget):
    state["text"] = widget.get_text()
    save()


entry.connect("changed", changed)
box.pack_start(entry, False, False, 0)
password = named(Gtk.Entry(), "Protected editor")
password.set_visibility(False)
password.set_text("fixture-private-value")
box.pack_start(password, False, False, 0)
scale = named(Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1), "Volume")
scale.set_value(25)


def value_changed(widget):
    state["value"] = widget.get_value()
    save()


scale.connect("value-changed", value_changed)
box.pack_start(scale, False, False, 0)
store = Gtk.ListStore(str, str)
for index in range(30):
    store.append([f"Row {index}", f"Value {index}"])
table = named(Gtk.TreeView(model=store), "Results table")
for index, title in enumerate(["Name", "Value"]):
    table.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=index))
selection = table.get_selection()
selection.set_mode(Gtk.SelectionMode.MULTIPLE)


def selected(widget):
    _model, paths = widget.get_selected_rows()
    state["selected"] = [path.get_indices()[0] for path in paths]
    save()


selection.connect("changed", selected)
scroll = Gtk.ScrolledWindow()
scroll.set_size_request(400, 180)
scroll.add(table)
box.pack_start(scroll, True, True, 0)
text_view = named(Gtk.TextView(), "Document editor")
text_view.get_buffer().set_text("First line\nSecond line with Unicode: 世界 😀\nThird line")
box.pack_start(text_view, True, True, 0)
image = named(Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.DIALOG), "Information image")
box.pack_start(image, False, False, 0)
close_button = named(Gtk.Button(label="Close fixture"), "Close fixture")


def close_fixture(_button):
    state["closed"] = True
    save()
    window.destroy()


close_button.connect("clicked", close_fixture)
box.pack_start(close_button, False, False, 0)


def ready():
    if not window.get_mapped():
        return GLib.SOURCE_CONTINUE
    state["ready"] = True
    state["text"] = entry.get_text()
    save()
    return GLib.SOURCE_REMOVE


window.show_all()
GLib.timeout_add(20, ready)
Gtk.main()
