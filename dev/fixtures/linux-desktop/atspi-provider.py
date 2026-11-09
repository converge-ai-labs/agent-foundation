"""Controlled native D-Bus provider for members not exposed by the GTK fixture.

This is a wire-level test double, not application-effect validation. Run only
inside test-accessibility.sh's private user bus. Every mutation is recorded.
"""

import json
import sys
from pathlib import Path

from gi.repository import Gio, GLib

PREFIX = "org.a11y.atspi."
ROOT = "/org/a11y/atspi/accessible/"
path = Path(sys.argv[1])
state = {"ready": False, "calls": [], "mode": "success", "selections": []}
# Each tuple is (member, input argument types, output argument types).
methods = {
    "Accessible": [
        ("GetState", [], ["au"]),
        ("GetRole", [], ["u"]),
        ("GetInterfaces", [], ["as"]),
        ("GetIndexInParent", [], ["i"]),
        ("GetChildAtIndex", ["i"], ["(so)"]),
    ],
    "Action": [
        ("GetName", ["i"], ["s"]),
        ("GetLocalizedName", ["i"], ["s"]),
        ("GetDescription", ["i"], ["s"]),
        ("GetKeyBinding", ["i"], ["s"]),
        ("DoAction", ["i"], ["b"]),
    ],
    "Component": [
        ("GrabFocus", [], ["b"]),
        ("ScrollTo", ["u"], ["b"]),
        ("ScrollToPoint", ["u", "i", "i"], ["b"]),
        ("SetPosition", ["i", "i", "u"], ["b"]),
        ("SetSize", ["i", "i"], ["b"]),
        ("SetExtents", ["i", "i", "i", "i", "u"], ["b"]),
    ],
    "Selection": [
        ("GetSelectedChild", ["i"], ["(so)"]),
        *[(name, ["i"], ["b"]) for name in ["SelectChild", "DeselectChild", "DeselectSelectedChild"]],
        ("SelectAll", [], ["b"]),
        ("ClearSelection", [], ["b"]),
    ],
    "EditableText": [
        ("SetTextContents", ["s"], ["b"]),
        ("InsertText", ["i", "s", "i"], ["b"]),
        *[(name, ["i", "i"], ["b"]) for name in ["DeleteText", "CutText"]],
        ("CopyText", ["i", "i"], []),
        ("PasteText", ["i"], ["b"]),
    ],
    "Text": [
        ("GetNSelections", [], ["i"]),
        ("GetSelection", ["i"], ["i", "i"]),
        ("GetAttributeRun", ["i", "b"], ["a{ss}", "i", "i"]),
        ("SetCaretOffset", ["i"], ["b"]),
        ("AddSelection", ["i", "i"], ["b"]),
        ("SetSelection", ["i", "i", "i"], ["b"]),
        ("RemoveSelection", ["i"], ["b"]),
        ("ScrollSubstringTo", ["i", "i", "u"], ["b"]),
        ("ScrollSubstringToPoint", ["i", "i", "u", "i", "i"], ["b"]),
    ],
    "Document": [
        ("GetTextSelections", [], ["a((so)i(so)ib)"]),
        ("SetTextSelections", ["a((so)i(so)ib)"], ["b"]),
    ],
    "Table": [
        *[
            (name, ["i"], ["b"])
            for name in ["AddRowSelection", "RemoveRowSelection", "AddColumnSelection", "RemoveColumnSelection"]
        ],
    ],
    "Value": [],
    "TestControl": [("Configure", ["s"], [])],
}
properties = {
    "Accessible": {"Parent": "(so)"},
    "Action": {"NActions": "i"},
    "Selection": {"NSelectedChildren": "i"},
    "Text": {"CharacterCount": "i"},
    "Table": {"NRows": "i", "NColumns": "i"},
    "Value": {"MinimumValue": "d", "MaximumValue": "d", "CurrentValue": "d"},
}


def save():
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(path)


session = Gio.bus_get_sync(Gio.BusType.SESSION)
address = session.call_sync(
    "org.a11y.Bus",
    "/org/a11y/bus",
    "org.a11y.Bus",
    "GetAddress",
    None,
    GLib.VariantType.new("(s)"),
    Gio.DBusCallFlags.NONE,
    5000,
    None,
).unpack()[0]
bus = Gio.DBusConnection.new_for_address_sync(
    address,
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None,
    None,
)
owner = bus.get_unique_name()
state["owner"] = owner


def ref(name):
    return (owner, ROOT + name)


def invoke(_bus, _sender, object_path, interface, member, arguments, invocation):
    native = interface.removeprefix(PREFIX)
    args = arguments.unpack()
    reads = {
        "GetState": ([0, 0],),
        "GetRole": (61,),
        "GetInterfaces": ([PREFIX + name for name in methods if name != "TestControl"],),
        "GetIndexInParent": (0,),
        "GetChildAtIndex": (ref("child"),),
        "GetName": ("activate",),
        "GetLocalizedName": ("Activate",),
        "GetDescription": ("Test action",),
        "GetKeyBinding": ("",),
        "GetSelectedChild": (ref("child"),),
        "GetNSelections": (2,),
        "GetSelection": (2, 7) if args[0:1] == (0,) else (8, 10),
        "GetAttributeRun": ({"weight": "400"}, args[0], args[0] + 1) if member == "GetAttributeRun" else ({}, 0, 1),
        "GetTextSelections": (state["selections"],),
    }
    if member == "Configure":
        state["mode"] = args[0]
        save()
        invocation.return_value(GLib.Variant("()", ()))
        return
    output = next(output for name, _, output in methods[native] if name == member)
    signature = "(" + "".join(output) + ")"
    if member in reads:
        invocation.return_value(GLib.Variant(signature, reads[member]))
        return
    state["calls"].append([object_path, native, member, args])
    if member == "SetTextSelections":
        state["selections"] = args[0]
    save()
    if state["mode"] == "error":
        invocation.return_dbus_error("org.a11y.atspi.Error.Failed", "Fixture native error")
    elif state["mode"] == "no_reply":
        # Retain the invocation until the test kills this provider.
        pending.append(invocation)
    else:
        invocation.return_value(GLib.Variant(signature, (state["mode"] != "false",) if output else ()))


pending = []


def get_property(_bus, _sender, object_path, interface, name):
    native = interface.removeprefix(PREFIX)
    values = {
        "Parent": ref("root") if object_path == ROOT + "child" else ("", "/org/a11y/atspi/null"),
        "NActions": 1,
        "NSelectedChildren": 1,
        "CharacterCount": 20,
        "NRows": 3,
        "NColumns": 2,
        "MinimumValue": 0.0,
        "MaximumValue": 100.0,
        "CurrentValue": 20.0,
    }
    return GLib.Variant(properties[native][name], values[name])


def set_property(_bus, _sender, object_path, interface, name, value):
    state["calls"].append([object_path, interface.removeprefix(PREFIX), name, [value.unpack()]])
    save()
    return True


for native, members in methods.items():
    xml = f'<node><interface name="{PREFIX}{native}">'
    for member, inputs, outputs in members:
        xml += f'<method name="{member}">'
        for direction, types in [("in", inputs), ("out", outputs)]:
            xml += "".join(f'<arg type="{kind}" direction="{direction}"/>' for kind in types)
        xml += "</method>"
    for name, kind in properties.get(native, {}).items():
        access = "readwrite" if name == "CurrentValue" else "read"
        xml += f'<property name="{name}" type="{kind}" access="{access}"/>'
    xml += "</interface></node>"
    info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
    for node in ["root", "child", "other"]:
        bus.register_object(ROOT + node, info, invoke, get_property, set_property)
state["ready"] = True
save()
GLib.MainLoop().run()
