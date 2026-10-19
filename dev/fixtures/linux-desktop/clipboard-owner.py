"""Disposable adversarial X11 selection owner for bounded clipboard acquisition tests."""

import sys
from pathlib import Path

from Xlib import X, display, protocol

mode = sys.argv[1]
x = display.Display()
root = x.screen().root
window = root.create_window(0, 0, 1, 1, 0, 0, X.InputOnly, event_mask=X.PropertyChangeMask)
clipboard, targets, utf8, incr = [x.intern_atom(name) for name in ("CLIPBOARD", "TARGETS", "UTF8_STRING", "INCR")]
window.set_selection_owner(clipboard, X.CurrentTime)
x.sync()
probe = Path("/workspace/clipboard-probe")
probe.write_text(mode + ":ready")
transfer = None
chunks = []
while True:
    event = x.next_event()
    if event.type == X.SelectionRequest:
        prop = event.property or event.target
        if event.target == targets:
            offered = [x.intern_atom("image/png")] if mode == "no_text" else [utf8]
            if mode == "huge_targets":
                offered = [utf8] * 2048
            event.requestor.change_property(prop, Xatom := x.intern_atom("ATOM"), 32, offered)
        elif event.target == utf8:
            probe.write_text(mode + ":requested")
            if mode in ("stall", "incr", "incr_oversize", "incr_invalid"):
                event.requestor.change_attributes(event_mask=X.PropertyChangeMask)
                event.requestor.change_property(prop, incr, 32, [1])
                transfer = (event.requestor, prop)
                chunks = {
                    "stall": [],
                    "incr": ["中".encode(), "文🙂\n".encode(), b""],
                    "incr_oversize": [b"a" * 65536, b"b" * 65536, b"c" * 65536, b""],
                    "incr_invalid": [b"\xff", b""],
                }[mode]
            elif mode == "invalid":
                event.requestor.change_property(prop, utf8, 8, b"\xff")
            elif mode == "oversize":
                event.requestor.change_property(prop, utf8, 8, b"a" * 16385)
            else:
                event.requestor.change_property(prop, utf8, 8, b"")
        else:
            prop = X.NONE
        event.requestor.send_event(
            protocol.event.SelectionNotify(
                time=event.time,
                requestor=event.requestor,
                selection=event.selection,
                target=event.target,
                property=prop,
            )
        )
        x.flush()
    elif (
        event.type == X.PropertyNotify
        and transfer
        and event.window == transfer[0]
        and event.atom == transfer[1]
        and event.state == X.PropertyDelete
        and chunks
    ):
        transfer[0].change_property(transfer[1], utf8, 8, chunks.pop(0))
        x.flush()
