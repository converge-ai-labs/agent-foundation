"""Temporary XInput2 raw-key probe for the native CI input investigation."""

import ctypes as c


class Mask(c.Structure):
    _fields_ = [("deviceid", c.c_int), ("mask_len", c.c_int), ("mask", c.POINTER(c.c_ubyte))]


class Cookie(c.Structure):
    _fields_ = [
        ("type", c.c_int),
        ("serial", c.c_ulong),
        ("send_event", c.c_int),
        ("display", c.c_void_p),
        ("extension", c.c_int),
        ("evtype", c.c_int),
        ("cookie", c.c_uint),
        ("data", c.c_void_p),
    ]


class Event(c.Union):
    _fields_ = [("type", c.c_int), ("cookie", Cookie), ("pad", c.c_long * 24)]  # noqa: RUF012


def main():
    x = c.CDLL("libX11.so.6")
    xi = c.CDLL("libXi.so.6")
    x.XOpenDisplay.argtypes = [c.c_char_p]
    x.XOpenDisplay.restype = c.c_void_p
    x.XDefaultRootWindow.argtypes = [c.c_void_p]
    x.XDefaultRootWindow.restype = c.c_ulong
    x.XSync.argtypes = [c.c_void_p, c.c_int]
    x.XNextEvent.argtypes = [c.c_void_p, c.POINTER(Event)]
    x.XQueryExtension.argtypes = [c.c_void_p, c.c_char_p, c.POINTER(c.c_int), c.POINTER(c.c_int), c.POINTER(c.c_int)]
    xi.XISelectEvents.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(Mask), c.c_int]
    display = x.XOpenDisplay(None)
    if not display:
        raise RuntimeError("display unavailable")
    opcode, event, error = c.c_int(), c.c_int(), c.c_int()
    if not x.XQueryExtension(display, b"XInputExtension", c.byref(opcode), c.byref(event), c.byref(error)):
        raise RuntimeError("XInput unavailable")
    bits = (c.c_ubyte * 2)(0, (1 << 5) | (1 << 6))  # XI_RawKeyPress and XI_RawKeyRelease
    mask = Mask(1, len(bits), bits)  # XIAllMasterDevices
    if xi.XISelectEvents(display, x.XDefaultRootWindow(display), c.byref(mask), 1):
        raise RuntimeError("XISelectEvents failed")
    x.XSync(display, 0)
    print("ready", flush=True)
    count = 0
    while True:
        item = Event()
        x.XNextEvent(display, c.byref(item))
        if item.type == 35 and item.cookie.extension == opcode.value and item.cookie.evtype in (13, 14):
            if count < 20:
                print("raw_press" if item.cookie.evtype == 13 else "raw_release", flush=True)
                count += 1


if __name__ == "__main__":
    main()
