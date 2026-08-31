"""Entry point: python3 -m ekran.main"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from .window import Window


class EkranApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.ekran.Ekran", flags=0)
        self.connect("activate", self._on_activate)

    def _on_activate(self, _app):
        win = Window(self)
        win.present()


def main():
    app = EkranApp()
    app.run(None)


if __name__ == "__main__":
    main()
