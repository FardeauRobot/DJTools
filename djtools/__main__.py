import sys

from PySide6.QtWidgets import QApplication

from .config import Settings
from .ui import theme
from .ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DJTools")
    theme.apply(app)
    window = MainWindow(Settings())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
