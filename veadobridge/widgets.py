"""Small Tk helpers shared by the tabs."""

import tkinter as tk
from tkinter import ttk

DOT_COLOURS = {
    "ok": "#2e9e4f",
    "busy": "#d99a1b",
    "off": "#8a8a8a",
    "bad": "#c33c3c",
}


class StatusDot(ttk.Frame):
    """A coloured bullet plus a label, used for service status lines."""

    def __init__(self, master, text="", **kwargs):
        super().__init__(master, **kwargs)
        self._dot = tk.Label(self, text="●", fg=DOT_COLOURS["off"])
        self._dot.pack(side=tk.LEFT)
        self._text = ttk.Label(self, text=text)
        self._text.pack(side=tk.LEFT, padx=(4, 0))

    def set(self, state, text):
        self._dot.configure(fg=DOT_COLOURS.get(state, DOT_COLOURS["off"]))
        self._text.configure(text=text)


class ScrollableFrame(ttk.Frame):
    """A frame that scrolls vertically. Put content into `.body`."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.body = ttk.Frame(self._canvas)
        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self._canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_body_configure(self, _event):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self):
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)
        self._canvas.bind_all("<Button-4>", self._on_wheel)
        self._canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self):
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.unbind_all(sequence)

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self._canvas.yview_scroll(delta, "units")
