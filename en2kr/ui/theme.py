"""Shared dark theme."""

STYLE = """
QWidget { background-color: #16181d; color: #e6e6e6; font-family: 'Malgun Gothic'; font-size: 9pt; }
QDialog, QMainWindow { background-color: #16181d; }
QGroupBox { border: 1px solid #2c313a; border-radius: 6px; margin-top: 14px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #9aa4b2; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget, QTextEdit, QPlainTextEdit {
    background-color: #1f232a; border: 1px solid #2f3540; border-radius: 4px; padding: 4px;
    selection-background-color: #2f6fed; }
QComboBox QAbstractItemView { background-color: #1f232a; selection-background-color: #2f6fed; }
QHeaderView::section { background-color: #232832; color: #9aa4b2; border: none; padding: 5px; }
QTableWidget { gridline-color: #2a2f38; }
QPushButton { background-color: #2a303a; border: 1px solid #353c48; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #343b47; }
QPushButton:disabled { color: #666; }
QPushButton#primary { background-color: #2f6fed; border-color: #2f6fed; font-weight: bold; }
QPushButton#primary:hover { background-color: #477ff0; }
QPushButton#help { background-color: #3a404b; border-radius: 9px; padding: 0; font-weight: bold;
                   min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; }
QTabWidget::pane { border: 1px solid #2c313a; border-radius: 4px; }
QTabBar::tab { background: #1f232a; padding: 6px 12px; border: 1px solid #2c313a; border-bottom: none; }
QTabBar::tab:selected { background: #2a303a; }
QProgressBar { background: #1f232a; border: 1px solid #2f3540; border-radius: 4px; text-align: center; height: 12px; }
QProgressBar::chunk { background-color: #3fb950; border-radius: 3px; }
QSlider::groove:horizontal { height: 4px; background: #2f3540; border-radius: 2px; }
QSlider::handle:horizontal { background: #2f6fed; width: 14px; margin: -6px 0; border-radius: 7px; }
QLabel#muted { color: #8b949e; }
QLabel#warn { color: #f0a020; }
QStatusBar { background: #111317; }
QToolTip { background-color: #2a303a; color: #e6e6e6; border: 1px solid #444; }
"""

STATE_COLORS = {"ok": "#3fb950", "wait": "#d29922", "loading": "#58a6ff", "error": "#f85149", "off": "#6e7681"}
