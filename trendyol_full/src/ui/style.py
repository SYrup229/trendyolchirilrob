APP_STYLESHEET = """
* { font-family: 'Segoe UI', 'Inter', sans-serif; }

QMainWindow, QDialog, QWidget { background-color: #0f172a; color: #e2e8f0; }

QListWidget, QTableWidget, QTreeWidget, QTableView {
    background-color: #111c34;
    border: 1px solid #1e293b;
    alternate-background-color: #0f1a30;
    gridline-color: #1e293b;
}
QHeaderView::section {
    background-color: #0b1427;
    color: #cbd5e1;
    padding: 6px 8px;
    border: 0;
    border-right: 1px solid #1e293b;
    border-bottom: 1px solid #1e293b;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background-color: #0b1427;
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: #2563eb;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #2563eb;
}

QPushButton {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 7px 14px;
}
QPushButton:hover { background-color: #334155; }
QPushButton:pressed { background-color: #0f172a; }
QPushButton:disabled { color: #64748b; }

QPushButton#primary {
    background-color: #2563eb;
    border: 1px solid #1d4ed8;
    color: white;
    font-weight: 600;
}
QPushButton#primary:hover { background-color: #1d4ed8; }
QPushButton#primary:disabled { background-color: #1e293b; border-color: #334155; }

QPushButton#danger {
    background-color: #b91c1c;
    border: 1px solid #991b1b;
    color: white;
}
QPushButton#danger:hover { background-color: #991b1b; }

QLabel#title { font-size: 18px; font-weight: 600; color: #f8fafc; }
QLabel#subtitle { color: #94a3b8; }
QLabel#badge-ok { color: #16a34a; font-weight: 600; }
QLabel#badge-warn { color: #f59e0b; font-weight: 600; }
QLabel#badge-err { color: #ef4444; font-weight: 600; }

QListWidget#sidebar {
    background-color: #0b1427;
    border: 0;
    border-right: 1px solid #1e293b;
    padding-top: 10px;
}
QListWidget#sidebar::item {
    padding: 10px 16px;
    color: #cbd5e1;
}
QListWidget#sidebar::item:selected {
    background-color: #1e293b;
    color: #f8fafc;
    border-left: 3px solid #2563eb;
}

QStatusBar { background-color: #0b1427; color: #94a3b8; }
"""
