"""Flat GROUP/TAG review rows using the existing Qt table and tag palette."""
from PySide6.QtCore import QEvent, QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from booruflow.domain.booru_sites import category_name, resolve_category
from booruflow.domain.curated_profiles import parse_refinement_token
from booruflow.presentation.pyside6.icons import NAVIGATION_COLORS
from booruflow.presentation.pyside6.tag_presentation import present_tag
from booruflow.presentation.pyside6.theme import style_item_view

ROW_KIND_ROLE = Qt.ItemDataRole.UserRole + 120
INITIAL_ROLE = Qt.ItemDataRole.UserRole + 121
DECISION_ROLE = Qt.ItemDataRole.UserRole + 122
CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 123
BUCKET_ROLE = Qt.ItemDataRole.UserRole + 124
STATE_COLUMN = 6
ID_COLUMN = 5
CURATED_KEY_ROLE = Qt.ItemDataRole.UserRole + 125
CURATED_PARENT_ROLE = Qt.ItemDataRole.UserRole + 126
CURATED_PENDING_ROLE = Qt.ItemDataRole.UserRole + 127


def review_symbol(decision: str) -> str:
    return {"accepted": "✓", "keep": "✓", "rejected": "✕", "remove": "✕"}.get(decision, "?")


class SuggestionTable(QTableWidget):
    """Sorting is applied within groups by the presenter, never by Qt globally."""

    refinement_requested = Signal(str, str, bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_item_view(self)
        self._curated_expanded = set()
        self._render_context = None
        self.itemClicked.connect(self._click_curated)

    def reset_curated_expansion(self):
        self._curated_expanded.clear()

    def retranslate_curated(self, catalog):
        if self._render_context is not None:
            rows, _catalog, site, column, order = self._render_context
            if any(row.get("curated") for row in rows):
                self.render_rows(rows, catalog, site, column, order)

    def _click_curated(self, item):
        first = self.item(item.row(), 0)
        key = first.data(CURATED_KEY_ROLE)
        if key:
            self._expand_curated(key, key not in self._curated_expanded)
    def _toggle_curated_refinement(self, first):
        if first.data(CURATED_PARENT_ROLE) and not first.data(CURATED_KEY_ROLE):
            token = self.item(first.row(), ID_COLUMN).text()
            trigger, name = parse_refinement_token(token)
            self.refinement_requested.emit(trigger, name, not first.data(CURATED_PENDING_ROLE))

    def _expand_curated(self, key, opened):
        if opened:
            self._curated_expanded.add(key)
        else:
            self._curated_expanded.discard(key)
        if self._render_context is not None:
            rows, catalog, site, column, order = self._render_context
            self.render_rows(rows, catalog, site, column, order)


    def setSortingEnabled(self, enabled: bool) -> None:
        super().setSortingEnabled(False)
        self.horizontalHeader().setSortIndicatorShown(True)

    def sortItems(self, column, order=Qt.SortOrder.AscendingOrder) -> None:
        header = self.horizontalHeader()
        if header.sortIndicatorSection() == column and header.sortIndicatorOrder() == order:
            header.sortIndicatorChanged.emit(column, order)
        else:
            header.setSortIndicator(column, order)

    def tag_rows(self) -> list[int]:
        return [row for row in range(self.rowCount())
                if self.item(row, 0) is not None and self.item(row, 0).data(ROW_KIND_ROLE) == "TAG"]

    def selected_tokens(self) -> list[str]:
        return [self.item(index.row(), ID_COLUMN).text()
                for index in self.selectionModel().selectedRows()
                if self.item(index.row(), 0) is not None
                and self.item(index.row(), ID_COLUMN) is not None
                and self.item(index.row(), 0).data(ROW_KIND_ROLE) == "TAG"]

    def keyPressEvent(self, event) -> None:
        current = self.item(self.currentRow(), 0)
        if current is not None and event.key() in {Qt.Key.Key_Right, Qt.Key.Key_Left}:
            key = current.data(CURATED_KEY_ROLE)
            parent = current.data(CURATED_PARENT_ROLE)
            if key and (event.key() == Qt.Key.Key_Right or key in self._curated_expanded):
                self._expand_curated(key, event.key() == Qt.Key.Key_Right)
                event.accept()
                return
            if parent and event.key() == Qt.Key.Key_Left:
                row = next((r for r in range(self.rowCount())
                            if self.item(r, 0).data(CURATED_KEY_ROLE) == parent), None)
                if row is not None:
                    self.selectRow(row)
                event.accept()
                return
        if (current is not None and current.data(CURATED_PARENT_ROLE)
                and not current.data(CURATED_KEY_ROLE) and event.key() == Qt.Key.Key_Space
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self._toggle_curated_refinement(current)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            current = self.item(self.currentRow(), 0)
            if current is not None and current.data(ROW_KIND_ROLE) == "GROUP":
                bucket = current.data(BUCKET_ROLE)
                candidates = [row for row in self.tag_rows()
                              if self.item(row, 0).data(BUCKET_ROLE) == bucket
                              and self.item(row, 0).data(DECISION_ROLE) == "unreviewed"
                              and not self.isRowHidden(row)]
                selection = self.selectionModel()
                remove = bool(candidates) and all(selection.isRowSelected(row) for row in candidates)
                operation = (QItemSelectionModel.SelectionFlag.Deselect if remove
                             else QItemSelectionModel.SelectionFlag.Select)
                for row in candidates:
                    selection.select(self.model().index(row, 0),
                                     operation | QItemSelectionModel.SelectionFlag.Rows)
                event.accept()
                return
        super().keyPressEvent(event)

    def render_rows(self, rows, catalog, site, sort_column, sort_order, *, next_token=None, advance=False):
        from copy import deepcopy
        signature = (rows, catalog.code, site, sort_column, sort_order, self._curated_expanded)
        if not advance and signature == getattr(self, '_render_signature', None):
            return
        self._render_signature = deepcopy(signature)
        self._render_context = (rows, catalog, site, sort_column, sort_order)
        self._review_site = site
        selected = set(self.selected_tokens()) if self.columnCount() > ID_COLUMN else set()
        current = self.item(self.currentRow(), ID_COLUMN)
        current_token = current.text() if current else None
        scroll = self.verticalScrollBar().value()
        decision_order = {"unreviewed": 0, "accepted": 1, "keep": 1, "rejected": 2, "remove": 2}

        def key(row):
            category = row.get("category_id", row.get("category"))
            category_id = resolve_category(site, category_name(site, category))
            match = str(row["match"])
            match_rank = next((rank for rank, name in enumerate((
                "exact", "mapping", catalog.text("tagging.match.already_present"),
                catalog.text("tagging.match.missing"), catalog.text("tagging.match.not_applicable"),
            )) if name.casefold() in match.casefold()), 9)
            return (
                str(row["tag"]).casefold(), float(row["confidence"] or -1), match_rank,
                category_id if category_id is not None else -1, decision_order.get(row["decision"], 9),
                str(row["id"]), decision_order.get(row["decision"], 9),
            )[sort_column]

        groups = {}
        for row in rows:
            bucket = min(9, max(0, int(float(row["confidence"]) * 10))) if row["confidence"] != "" else -1
            groups.setdefault(bucket, []).append(row)
        self.clearContents()
        self.setRowCount(len(rows) + len(groups))
        index = 0
        for bucket in sorted(groups, reverse=True):
            label = (f"{bucket*10}–{100 if bucket == 9 else bucket*10+9} %"
                     if bucket >= 0 else catalog.text("tagging.review.unscored"))
            for column in range(7):
                item = QTableWidgetItem(label if column == 0 else "")
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setData(ROW_KIND_ROLE, "GROUP")
                item.setData(BUCKET_ROLE, bucket)
                font = item.font(); font.setBold(True); item.setFont(font)
                self.setItem(index, column, item)
            index += 1
            for row in sorted(groups[bucket], key=key, reverse=sort_order == Qt.SortOrder.DescendingOrder):
                values = (row["tag"], row["confidence"], row["match"], row.get("category", ""),
                          catalog.text(f"tagging.review.decision.{row['decision']}"), row["id"],
                          review_symbol(row["decision"]))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    item.setData(ROW_KIND_ROLE, "TAG")
                    item.setData(INITIAL_ROLE, bool(row.get("present_initially", False)))
                    item.setData(DECISION_ROLE, row["decision"])
                    item.setData(CATEGORY_ROLE, row.get("category_id", row.get("category")))
                    item.setData(BUCKET_ROLE, bucket)
                    if column == 4:
                        item.setData(Qt.ItemDataRole.UserRole, row["decision"])
                    if row.get("profile_scores"):
                        item.setToolTip(str(row["match"]))
                    if column == STATE_COLUMN:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        item.setToolTip(str(values[4]))
                    self.setItem(index, column, item)
                index += 1
                if row.get("curated"):
                    index = self._render_curated(index, row["curated"], catalog, bucket)
        self.refresh_theme()
        if any(row.get("curated") for row in rows):
            # Initial sizing can happen while the table is empty. Keep real names
            # and virtual labels readable when profiles first become available.
            self.setColumnWidth(0, max(220, self.columnWidth(0)))
            self.setColumnWidth(STATE_COLUMN, 38)
        tag_rows = self.tag_rows()
        selectable_rows = [r for r in range(self.rowCount())
                           if self.item(r, ID_COLUMN) is not None]
        target = str(next_token) if advance and next_token is not None else current_token
        target_row = next((row for row in selectable_rows
                           if self.item(row, ID_COLUMN).text() == target), None)
        self.clearSelection()
        if not advance:
            for row in tag_rows:
                if self.item(row, ID_COLUMN).text() in selected:
                    self.selectionModel().select(self.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        if target_row is None and tag_rows:
            target_row = tag_rows[0]
        if target_row is not None:
            self.selectionModel().setCurrentIndex(self.model().index(target_row, 0),
                                                  QItemSelectionModel.SelectionFlag.NoUpdate)
            if advance or not self.selected_tokens():
                self.selectRow(target_row)
        self.verticalScrollBar().setValue(scroll)

    def _render_curated(self, index, profile, catalog, bucket):
        trigger = profile["trigger"]
        key = f"group:{trigger}"

        def insert(label, token, *, group_key=None, parent=None, child=None):
            nonlocal index
            self.insertRow(index)
            values = [label, "", "", "", "", token, ""]
            if child is not None:
                values[1] = str(child["confidence"])
                values[2] = str(child["match"])
                values[3] = catalog.text(
                    f"tagging.category.{category_name('gelbooru', child['category_id'])}")
                decision = child.get('decision', 'unreviewed')
                values[4] = (catalog.text(f'tagging.review.decision.{decision}') if decision != 'unreviewed'
                             else catalog.text("curated.pending" if child["pending"] else "curated.candidate"))
                values[6] = review_symbol(decision) if decision != 'unreviewed' else ("●" if child["pending"] else "○")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setData(ROW_KIND_ROLE, "CURATED_GROUP" if group_key else "TAG")
                item.setData(CURATED_KEY_ROLE, group_key)
                item.setData(CURATED_PARENT_ROLE, parent)
                item.setData(BUCKET_ROLE, bucket)
                item.setData(DECISION_ROLE, child.get('decision', 'unreviewed') if child else 'unreviewed')
                if child is not None:
                    item.setData(CATEGORY_ROLE, child["category_id"])
                    item.setData(CURATED_PENDING_ROLE, child["pending"])
                    item.setToolTip(values[4] + "\n" + child["match"] + "\n" + catalog.text(
                        "curated.posts", count=f"{child['post_count']:,}"))
                    if child["pending"]:
                        font = item.font(); font.setBold(True); item.setFont(font)
                else:
                    font = item.font(); font.setBold(True); item.setFont(font)
                    item.setToolTip(catalog.text("curated.group_tip") + "\n" +
                                    ", ".join(profile["packs"]))
                self.setItem(index, column, item)
            index += 1

        def tag(child, parent):
            insert("      " + child["tag"], str(child["id"]), parent=parent, child=child)

        insert("  " + ("▾ " if key in self._curated_expanded else "▸ ") + key,
               key, group_key=key)
        if key not in self._curated_expanded:
            return index
        for child in profile["top"]:
            tag(child, key)
        for group in profile["groups"]:
            group_key = f"{key}/{group['id']}"
            labels = group.get("labels", {})
            locale = catalog.code.replace("-", "_").split("_")[0]
            label = (labels.get(locale) or labels.get("en") or group["id"]
                     if "labels" in group else group["label"])
            insert("    " + ("▾ " if group_key in self._curated_expanded else "▸ ") +
                   label, group_key, group_key=group_key, parent=key)
            if group_key in self._curated_expanded:
                for child in group["tags"]:
                    tag(child, group_key)
        return index

    def refresh_theme(self) -> None:
        palette = self.palette()
        for row in range(self.rowCount()):
            tag = self.item(row, 0)
            if tag is None:
                continue
            group = tag.data(ROW_KIND_ROLE) in {"GROUP", "CURATED_GROUP"}
            background = palette.color(QPalette.ColorRole.AlternateBase if tag.data(INITIAL_ROLE)
                                       else QPalette.ColorRole.Base)
            if group:
                background = palette.color(QPalette.ColorRole.Window)
            for column in range(self.columnCount()):
                item = self.item(row, column)
                if item is not None:
                    item.setBackground(background)
                    item.setForeground(palette.color(QPalette.ColorRole.Text))
            if not group:
                presentation = present_tag(getattr(self, "_review_site", "gelbooru"), tag.text(),
                                           tag.data(CATEGORY_ROLE), palette)
                tag.setForeground(presentation.style.color(palette))
                symbol = self.item(row, STATE_COLUMN)
                if symbol is not None and symbol.text() in {"✓", "✕"}:
                    # Reuse the application's review/check and cleanup accents.
                    color = QColor(NAVIGATION_COLORS["review" if symbol.text() == "✓" else "cleanup"])
                    if palette.color(QPalette.ColorRole.Base).lightness() > 128:
                        color = color.darker(150)
                    symbol.setForeground(color)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.refresh_theme()
