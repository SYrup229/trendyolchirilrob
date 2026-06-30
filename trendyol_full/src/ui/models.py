"""
Lightweight QAbstractTableModel adapters for the views with potentially-large
datasets (Oblio catalog, Trendyol catalog, invoices). Virtualised — only visible
cells call data().
"""
from typing import Any, Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class DictRowsModel(QAbstractTableModel):
    """
    Generic model where each row is a dict and each column is described by a
    (header, value_fn) pair. `value_fn(row, role)` returns the display value,
    or any Qt role-specific value (e.g. ForegroundRole for badge colours).
    """

    def __init__(
        self,
        columns: list[tuple[str, Callable[[dict, int], Any]]],
        rows: list[dict] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._columns = columns
        self._rows: list[dict] = list(rows or [])

    # ---- API for the view ----
    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, index: int) -> dict | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    # ---- QAbstractTableModel ----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        _, fn = self._columns[index.column()]
        return fn(row, role)

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._columns):
            return self._columns[section][0]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None
