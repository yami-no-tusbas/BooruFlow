import importlib.util
import os
import unittest
from pathlib import Path

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class DataTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_shared_defaults_and_empty_text(self) -> None:
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView

        from booruflow.presentation.pyside6.ui_components import DataTable

        table = DataTable(0, 2)
        table.set_empty_text("Nothing here")

        self.assertEqual(table.empty_text(), "Nothing here")
        self.assertEqual(
            table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.assertEqual(
            table.selectionBehavior(), QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.assertEqual(
            table.selectionMode(), QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.assertTrue(table.alternatingRowColors())
        self.assertTrue(table.verticalHeader().isHidden())
        self.assertEqual(
            table.horizontalHeader().sectionResizeMode(0),
            QHeaderView.ResizeMode.Interactive,
        )
        table.close()

    def test_row_refresh_preserves_user_column_width(self) -> None:
        from booruflow.presentation.pyside6.ui_components import DataTable

        table = DataTable(0, 2)
        table.setColumnWidth(0, 173)
        table.setRowCount(4)
        table.setRowCount(0)

        self.assertEqual(table.columnWidth(0), 173)
        table.close()

    def test_empty_text_can_be_retranslated_at_runtime(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.ui_components import DataTable

        catalog = LanguageCatalog(LANGUAGES, "en")
        table = DataTable(0, 1)
        table.set_empty_text(catalog.text("table.empty_tasks"))
        self.assertEqual(table.empty_text(), "No task has been recorded yet.")

        catalog.set_language("fr")
        table.set_empty_text(catalog.text("table.empty_tasks"))
        self.assertEqual(table.empty_text(), "Aucune tâche n’a encore été enregistrée.")
        table.close()
