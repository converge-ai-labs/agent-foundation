"""Independent real Tk event loop for X11 integration tests (system Python)."""

import json
import sys
import tkinter as tk
from pathlib import Path

state_path = Path(sys.argv[1])
state = {"clicks": 0, "keys": [], "buttons": [], "drag": [], "ready": False}
root = tk.Tk()
root.title("X11 computer-use fixture")
root.attributes("-fullscreen", True)
root.geometry("1024x768+0+0")
canvas = tk.Canvas(root, width=1024, height=768, bg="#183450", highlightthickness=0)
canvas.pack(fill="both", expand=True)
canvas.create_text(32, 32, text="Native X11 desktop", fill="white", anchor="nw", font=("DejaVu Sans", 24))
button = canvas.create_rectangle(40, 80, 240, 180, fill="#d04040", outline="")
canvas.create_text(140, 130, text="Click once", fill="white", font=("DejaVu Sans", 16))


def save():
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(state_path)


def press(event):
    # Tk represents X11 buttons 6/7 as Shift + buttons 4/5.
    state["buttons"].append([event.num, event.state])
    if event.num == 1 and 40 <= event.x <= 240 and 80 <= event.y <= 180:
        state["clicks"] += 1
        canvas.itemconfigure(button, fill="#40c070")
    canvas.focus_set()
    save()


def key(event):
    state["keys"].append(event.keysym)
    canvas.create_text(32, 240, text="Keys: " + " ".join(state["keys"]), fill="white", anchor="nw")
    save()


def drag(event):
    state["drag"].append([event.x, event.y])
    save()


canvas.bind("<ButtonPress>", press)
canvas.bind("<KeyPress>", key)
canvas.bind("<B1-Motion>", drag)


def ready():
    if not root.winfo_viewable() or canvas.winfo_width() != 1024 or canvas.winfo_height() != 768:
        root.after(20, ready)
        return
    canvas.focus_set()
    state["ready"] = True
    save()


root.after(20, ready)
root.mainloop()
