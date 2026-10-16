"""Read-only rich text for help and guide windows."""
from PySide6 import QtWidgets


def text_view(html_text):
    """Rich text that re-wraps to the window width when it is resized (and scrolls if it is short).

    A word-wrapped QLabel in a fixed-size dialog wraps at a width Qt guesses,
    which left single words dangling on their own line.
    """
    view = QtWidgets.QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    view.setStyleSheet("QTextBrowser { background: transparent; }")
    view.setHtml(html_text)
    return view
