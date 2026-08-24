"""Search/review UI for browser-assisted Gelbooru tagging."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.tagging import TaggingRequest, build_clipboard_tags
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.image_analysis_page import ScaledImageLabel


class SuggestionItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value) -> None:
        super().__init__(text); self.sort_value = sort_value
    def __lt__(self, other) -> bool:
        if isinstance(other, SuggestionItem): return self.sort_value < other.sort_value
        return super().__lt__(other)


class CollapsibleResultGroup(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__(); self.cards = []
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton(text=title); self.toggle.setCheckable(True); self.toggle.setChecked(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon); self.toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.content = QWidget(); self.grid = QGridLayout(self.content); self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.toggle.toggled.connect(self._toggle); layout.addWidget(self.toggle); layout.addWidget(self.content)
    def add_card(self, card) -> None: self.cards.append(card); self.reflow()
    def reflow(self) -> None:
        columns = max(1, self.width() // 185)
        for index, card in enumerate(self.cards): self.grid.addWidget(card, index // columns, index % columns)
    def resizeEvent(self, event) -> None: super().resizeEvent(event); self.reflow()
    def _toggle(self, expanded: bool) -> None:
        self.content.setVisible(expanded); self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


class TaggingPage(QWidget):
    start_requested = Signal(object); stop_requested = Signal(); post_selected = Signal(int, object)
    analyze_requested = Signal(int); decision_requested = Signal(int, object); mapping_requested = Signal(int)
    refresh_metadata_requested = Signal(int); activity_logged = Signal(str, str)
    pool_refresh_requested = Signal(); pool_reopen_requested = Signal(list)

    def __init__(self, catalog: LanguageCatalog, settings: dict[str, object]) -> None:
        super().__init__(); self.catalog = catalog; self.settings = settings
        self.current_post_id = None; self.current_post = {}; self.result_posts = []; self.result_buttons = {}
        self.current_result_index = -1; self.processed_in_session = set(); self._search_scroll_value = 0
        self._pending_next_id = None; self._pending_fallback_row = None; self.result_generation = 0
        self._all_suggestion_rows = []; self._sort_column = 1; self._sort_order = Qt.SortOrder.DescendingOrder
        root = QVBoxLayout(self); root.setContentsMargins(18, 12, 18, 16); root.setSpacing(6)
        self.title = QLabel(); self.title.setStyleSheet("font-size:22px;font-weight:600"); root.addWidget(self.title)
        self.mode_stack = QStackedWidget(); root.addWidget(self.mode_stack, 1)
        self.mode_stack.setMinimumWidth(0)
        self.mode_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.search_view = QWidget(); self.review = QWidget(); self.mode_stack.addWidget(self.search_view); self.mode_stack.addWidget(self.review)
        self._build_search(); self._build_review(); self.network = QNetworkAccessManager(self)
        self.start_button.clicked.connect(self._start); self.stop_button.clicked.connect(self.stop_requested.emit)
        self.zoom.currentIndexChanged.connect(lambda: self.preview.set_zoom(int(self.zoom.currentData())))
        self.analyze_button.clicked.connect(self._request_analysis); self.accept_button.clicked.connect(lambda: self._emit_decision("accepted"))
        self.reject_button.clicked.connect(lambda: self._emit_decision("rejected")); self.map_button.clicked.connect(self._request_mapping)
        self.refresh_button.clicked.connect(self._request_metadata_refresh); self.copy_button.clicked.connect(lambda: self._copy_mode("add"))
        self.copy_all_button.clicked.connect(lambda: self._copy_mode("all")); self.copy_open_button.clicked.connect(self._copy_and_open)
        self.open_button.clicked.connect(lambda: self._open_post(self.current_post_id or 0)); self.back_button.clicked.connect(self.show_search)
        self.previous_button.clicked.connect(lambda: self._navigate_result(-1)); self.next_button.clicked.connect(lambda: self._navigate_result(1))
        self.accept_shortcut = QShortcut(QKeySequence("A"), self.suggestions); self.reject_shortcut = QShortcut(QKeySequence("R"), self.suggestions)
        self.accept_shortcut.activated.connect(lambda: self._emit_decision("accepted")); self.reject_shortcut.activated.connect(lambda: self._emit_decision("rejected"))
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget): widget.installEventFilter(self)
        self.retranslate(); self.show_search()

    def _build_search(self) -> None:
        layout = QVBoxLayout(self.search_view); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.group = QGroupBox(); controls = QGridLayout(self.group); controls.setContentsMargins(8, 6, 8, 6)
        self.query_label = QLabel(); self.query = QLineEdit(str(self.settings.get("tagging_query", "rating:general")))
        self.start_button = QPushButton(); self.stop_button = QPushButton(); self.stop_button.setEnabled(False)
        controls.addWidget(self.query_label, 0, 0); controls.addWidget(self.query, 0, 1, 1, 9); controls.addWidget(self.start_button, 0, 10); controls.addWidget(self.stop_button, 0, 11)
        self.spins = {}; self.spin_labels = {}; defaults = {"pages":10,"start":1,"minimum":0,"maximum":12,"critical":5,"high":8}
        for index, (key, default) in enumerate(defaults.items()):
            label = QLabel(); spin = QSpinBox(); spin.setRange(1 if key in {"pages","start"} else 0, 1_000_000 if key == "start" else 1_000)
            spin.setValue(int(self.settings.get(f"tagging_{key}", default))); self.spins[key] = spin; self.spin_labels[key] = label
            row, column = 1 + index // 3, (index % 3) * 4; controls.addWidget(label, row, column); controls.addWidget(spin, row, column + 1, 1, 2)
        layout.addWidget(self.group); status = QHBoxLayout(); self.progress = QProgressBar(); self.progress.setMaximumHeight(22); self.state = QLabel()
        status.addWidget(self.progress, 1); status.addWidget(self.state, 2); layout.addLayout(status)
        pool = QGroupBox("Pool Tagging"); pool_layout = QVBoxLayout(pool); pool_actions = QHBoxLayout(); self.pool_scope = QComboBox(); self.pool_scope.addItem("Tous", "all"); self.pool_scope.addItem("Distant", "remote"); self.pool_scope.addItem("Local", "local"); self.pool_refresh = QPushButton("Actualiser"); self.pool_reopen = QPushButton("Réouvrir la sélection"); pool_actions.addWidget(QLabel("Source :")); pool_actions.addWidget(self.pool_scope); pool_actions.addWidget(self.pool_refresh); pool_actions.addWidget(self.pool_reopen); pool_actions.addStretch(1); pool_layout.addLayout(pool_actions); self.pool_table = QTableWidget(0, 4); self.pool_table.setHorizontalHeaderLabels(("Item", "Source", "État", "Analyse")); self.pool_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.pool_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.pool_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.pool_table.setMaximumHeight(210); pool_layout.addWidget(self.pool_table); layout.addWidget(pool)
        self.pool_refresh.clicked.connect(self.pool_refresh_requested); self.pool_scope.currentIndexChanged.connect(self.pool_refresh_requested); self.pool_reopen.clicked.connect(self._reopen_pool_selection)
        self.results_scroll = QScrollArea(); self.results_scroll.setWidgetResizable(True); self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results = QWidget(); self.results_layout = QVBoxLayout(self.results); self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.results_scroll.setWidget(self.results); layout.addWidget(self.results_scroll, 1)

    def show_tagging_pool(self, rows) -> None:
        scope = str(self.pool_scope.currentData()); filtered = [row for row in rows if scope == "all" or (scope == "remote" and row["source_site"]) or (scope == "local" and not row["source_site"])]
        self.pool_table.setRowCount(len(filtered))
        for index, row in enumerate(filtered):
            values = (str(row["id"]), f"{row['source_site'] or 'local'}:{row['source_post_id'] or row['cached_path'] or '—'}", str(row["state"]), "demandée" if row["analysis_requested"] else "profil seulement")
            for column, value in enumerate(values): self.pool_table.setItem(index, column, QTableWidgetItem(value))
        self.pool_table.resizeColumnsToContents()

    def _reopen_pool_selection(self) -> None:
        ids = [int(self.pool_table.item(index.row(), 0).text()) for index in self.pool_table.selectionModel().selectedRows()]
        if ids: self.pool_reopen_requested.emit(ids)

    def _build_review(self) -> None:
        layout = QVBoxLayout(self.review); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(5)
        nav = QHBoxLayout(); self.back_button = QPushButton("← Retour aux résultats [Esc]"); self.previous_button = QPushButton("← Précédent"); self.next_button = QPushButton("Suivant →")
        self.review_title = QLabel("Sélectionnez une vignette"); self.analysis_state = QLabel("Analyse locale : non demandée"); self.result_counter = QLabel("Post 0 / 0")
        for widget in (self.back_button, self.previous_button, self.next_button, self.review_title): nav.addWidget(widget)
        nav.addWidget(self.analysis_state, 1); nav.addWidget(self.result_counter); layout.addLayout(nav)
        self.review_splitter = QSplitter(Qt.Orientation.Horizontal); image_panel = QWidget(); image_layout = QVBoxLayout(image_panel); image_layout.setContentsMargins(0,0,4,0)
        self.preview = ScaledImageLabel(); image_layout.addWidget(self.preview, 1); zoom_row = QHBoxLayout(); zoom_row.addWidget(QLabel("Zoom")); self.zoom = QComboBox()
        for label, value in (("Ajuster",0),("100 %",100),("200 %",200),("400 %",400)): self.zoom.addItem(label, value)
        zoom_row.addWidget(self.zoom); zoom_row.addStretch(1); image_layout.addLayout(zoom_row); self.review_splitter.addWidget(image_panel)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical); source_panel = QWidget(); source_layout = QVBoxLayout(source_panel); source_layout.setContentsMargins(4,0,0,0)
        source_layout.addWidget(QLabel("Tags actuels")); self.current_source_tags = QListWidget(); source_layout.addWidget(self.current_source_tags, 1); self.right_splitter.addWidget(source_panel)
        suggestions_panel = QWidget(); suggestions_layout = QVBoxLayout(suggestions_panel); suggestions_layout.setContentsMargins(4,0,0,0)
        filter_row = QHBoxLayout(); filter_row.addWidget(QLabel("Revue des tags")); filter_row.addStretch(1); filter_row.addWidget(QLabel("Décision :")); self.decision_filter = QComboBox()
        for label, value in (("Tous","all"),("À examiner","unreviewed"),("Conserver / ajouter","accepted"),("Retirer / ignorer","rejected")): self.decision_filter.addItem(label,value)
        self.decision_filter.setCurrentIndex(1); filter_row.addWidget(self.decision_filter); suggestions_layout.addLayout(filter_row)
        self.suggestions = QTableWidget(0,5); self.suggestions.setHorizontalHeaderLabels(("Tag","Confiance","Décision","Origine / correspondance","ID")); self.suggestions.setColumnHidden(4,True)
        self.suggestions.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.suggestions.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.suggestions.setSortingEnabled(True); self.suggestions.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.suggestions.horizontalHeader().sortIndicatorChanged.connect(self._sort_changed); self.decision_filter.currentIndexChanged.connect(self._render_suggestions)
        suggestions_layout.addWidget(self.suggestions,1); self.right_splitter.addWidget(suggestions_panel); self.right_splitter.setStretchFactor(0,1); self.right_splitter.setStretchFactor(1,3)
        self.review_splitter.addWidget(self.right_splitter); self.review_splitter.setStretchFactor(0,3); self.review_splitter.setStretchFactor(1,2); layout.addWidget(self.review_splitter,1)
        tags = QHBoxLayout(); tags.addWidget(QLabel("Tags finaux :")); self.tags_to_add = QLineEdit(); self.tags_to_add.setReadOnly(True); tags.addWidget(self.tags_to_add,1); layout.addLayout(tags)
        self.action_bar = QFrame(); self.action_bar.setFrameShape(QFrame.Shape.StyledPanel); self.action_bar.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        actions = QVBoxLayout(self.action_bar); actions.setContentsMargins(6,4,6,4); validation = QHBoxLayout()
        self.analyze_button = QPushButton("Analyser localement"); self.accept_button = QPushButton("Accepter [A]"); self.reject_button = QPushButton("Rejeter [R]"); self.map_button = QPushButton("Associer…"); self.refresh_button = QPushButton("Actualiser les métadonnées")
        for button in (self.analyze_button,self.accept_button,self.reject_button,self.map_button): validation.addWidget(button)
        validation.addStretch(1); validation.addWidget(self.refresh_button); actions.addLayout(validation); workflow = QHBoxLayout(); workflow.addStretch(1)
        self.copy_button = QPushButton("Copier"); self.copy_all_button = QPushButton("Copier tous les acceptés"); self.copy_open_button = QPushButton("Copier + ouvrir"); self.open_button = QPushButton("Ouvrir Gelbooru")
        for button in (self.copy_button,self.copy_all_button,self.copy_open_button,self.open_button): workflow.addWidget(button)
        for button in (
            self.analyze_button, self.accept_button, self.reject_button, self.map_button,
            self.refresh_button, self.copy_button, self.copy_all_button,
            self.copy_open_button, self.open_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            button.setMinimumWidth(max(70, button.sizeHint().width()))
        actions.addLayout(workflow); layout.addWidget(self.action_bar,0)
        self._review_ready = False
        self.suggestions.itemSelectionChanged.connect(self._update_review_action_states)

    def _start(self) -> None:
        try: request = TaggingRequest(self.query.text().strip(), self.spins["pages"].value(), self.spins["start"].value(), self.spins["minimum"].value(), self.spins["maximum"].value(), self.spins["critical"].value(), self.spins["high"].value())
        except ValueError as exc: self.state.setText(self.catalog.text("tagging.invalid", error=exc)); return
        self.processed_in_session.clear(); self.start_requested.emit(request)
    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running); self.stop_button.setEnabled(running)
        if running: self._clear_results(); self.state.setText(self.catalog.text("tagging.running")); self.show_search()
    def set_progress(self, page:int,current:int,total:int,examined:int,retained:int) -> None:
        self.progress.setRange(0,total); self.progress.setValue(current); self.progress.setFormat(f"{current}/{total}"); self.state.setText(self.catalog.text("tagging.progress",page=page,examined=examined,retained=retained))

    def show_results(self, posts: list[dict]) -> None:
        self._clear_results(); self.processed_in_session.clear(); ordered=[]; generation=self.result_generation
        for key in ("critical","high","low"):
            values=sorted((p for p in posts if p.get("priority")==key),key=lambda p:int(p.get("tag_count",0))); ordered.extend(values)
            section=CollapsibleResultGroup(self.catalog.text("tagging.section",priority=self.catalog.text(f"tagging.priority.{key}"),count=len(values))); self.results_layout.addWidget(section)
            for post in values:
                pid=int(post.get("id",0)); card=QToolButton(); card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon); card.setIconSize(QSize(150,135)); card.setFixedSize(175,180)
                card.setText(self.catalog.text("tagging.card",id=pid,count=int(post.get("tag_count",0)))); card.clicked.connect(lambda _checked=False,value=post:self._open_result_post(value)); section.add_card(card); self.result_buttons[pid]=card
                preview=str(post.get("preview_url") or "")
                if preview:
                    request=QNetworkRequest(QUrl(preview)); request.setRawHeader(b"User-Agent",b"BooruFlow/0.1"); request.setRawHeader(b"Referer",b"https://gelbooru.com/"); reply=self.network.get(request)
                    reply.finished.connect(lambda current=reply,target=card,value=generation:self._thumbnail_ready(current,target,value))
        self.result_posts=ordered; self.current_result_index=-1; self.show_search()
    def _clear_results(self) -> None:
        self.result_generation+=1; self.result_buttons.clear(); self.result_posts=[]
        while self.results_layout.count():
            item=self.results_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
    def _thumbnail_ready(self,reply:QNetworkReply,button:QToolButton,generation:int)->None:
        try:
            if generation==self.result_generation and reply.error()==QNetworkReply.NetworkError.NoError:
                pixmap=QPixmap()
                if pixmap.loadFromData(bytes(reply.readAll())): button.setIcon(QIcon(pixmap))
        except RuntimeError: pass
        finally: reply.deleteLater()

    def _open_result_post(self,post:dict)->None:
        index=next((i for i,p in enumerate(self.result_posts) if int(p.get("id",0))==int(post.get("id",0))),-1); self._open_result(max(index, 0),post)
    def _open_result(self,index:int,fallback:dict|None=None)->None:
        if self.result_posts: index%=len(self.result_posts); post=self.result_posts[index]
        elif fallback is not None: post=fallback; index=-1
        else:return
        self._search_scroll_value=self.results_scroll.verticalScrollBar().value(); self.current_result_index=index; self.current_post=dict(post); self.current_post_id=int(post.get("id",0))
        self.review_title.setText(f"Gelbooru #{self.current_post_id}"); self.current_source_tags.clear(); self.current_source_tags.addItems(str(post.get("tags") or "").split()); self.mode_stack.setCurrentWidget(self.review); self._update_counter(); self._update_review_action_states(); self.post_selected.emit(self.current_post_id,self.current_post)
    def _select_post(self,post:dict)->None:self._open_result_post(post)
    def show_search(self)->None:self.mode_stack.setCurrentWidget(self.search_view); self.results_scroll.verticalScrollBar().setValue(self._search_scroll_value)
    def _navigate_result(self,delta:int)->None:
        if self.result_posts and self.current_result_index>=0:self._open_result(self.current_result_index+delta)
    def _update_counter(self)->None:
        total=len(self.result_posts); current=self.current_result_index+1 if self.current_result_index>=0 else 0; remaining=sum(int(p.get("id",0)) not in self.processed_in_session for p in self.result_posts)
        self.result_counter.setText(f"Post {current} / {total} · {remaining} restants"); self.previous_button.setEnabled(total>1); self.next_button.setEnabled(total>1)
    def _open_post(self,post_id:int)->None:self.activity_logged.emit("Open",f"Gelbooru #{post_id}"); QDesktopServices.openUrl(QUrl(f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"))
    def _selected_observation_id(self):
        row=self.suggestions.currentRow(); item=self.suggestions.item(row,4) if row>=0 else None; return int(item.text()) if item and item.text() else None
    def _emit_decision(self,decision:str)->None:
        oid=self._selected_observation_id()
        if oid is not None and self.accept_button.isEnabled():
            row=self.suggestions.currentRow(); self._pending_fallback_row=row; target=self.suggestions.item(row+1,4) or self.suggestions.item(row-1,4); self._pending_next_id=int(target.text()) if target and target.text() else None; self.decision_requested.emit(oid,decision)
    def _request_mapping(self)->None:
        oid=self._selected_observation_id()
        if oid is not None:self.mapping_requested.emit(oid)
    def _request_analysis(self)->None:
        if self.current_post_id:self.analyze_requested.emit(self.current_post_id)
    def _request_metadata_refresh(self)->None:
        if self.current_post_id:self.refresh_metadata_requested.emit(self.current_post_id)
    def _copy_and_open(self)->None:
        self._copy_mode("add")
        if self.current_post_id:self._open_post(self.current_post_id); self._mark_processed_and_advance()
    def _mark_processed_and_advance(self)->None:
        if not self.result_posts or self.current_post_id is None:return
        self.processed_in_session.add(self.current_post_id); button=self.result_buttons.get(self.current_post_id)
        if button and "✓" not in button.text():button.setText(f"✓ Traité\n{button.text()}")
        indexes=list(range(self.current_result_index+1,len(self.result_posts)))+list(range(self.current_result_index))
        target=next((i for i in indexes if int(self.result_posts[i].get("id",0)) not in self.processed_in_session),None)
        if target is None:self.state.setText("Tous les résultats de cette recherche ont été traités."); self.show_search()
        else:self._open_result(target)
    def _clipboard_text(self,mode:str)->str:
        value=self.tags_to_add.text() if mode=="add" else str(self.copy_all_button.property("tags") or ""); return build_clipboard_tags(value.split())
    def _copy_mode(self,mode:str)->None:
        value=self._clipboard_text(mode)
        if value:self._copy(value); self.activity_logged.emit("Clipboard",f"{len(value.split())} tags copied ({mode})")
    @staticmethod
    def _copy(value:str)->None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(value)

    def show_local_review(self,state,image_path,source_tags,rows,add_tags,all_accepted)->None:
        self.mode_stack.setCurrentWidget(self.review)
        self.analysis_state.setText(state); self.preview.set_image(image_path); self.current_source_tags.clear(); self.current_source_tags.addItems(source_tags); self._all_suggestion_rows=list(rows); self._render_suggestions()
        self._review_ready=state.startswith(("Analyse disponible","Déjà analysée"));busy=state.startswith(("Analyse en attente","Analyse en cours"))
        self.analyze_button.setEnabled(bool(self.current_post_id) and not self._review_ready and not busy);self.analyze_button.setText("Réessayer" if state.startswith("Erreur") else "Analyse en cours…" if busy else "Analyser localement")
        self.tags_to_add.setText(" ".join(add_tags));self.copy_all_button.setProperty("tags"," ".join(all_accepted));self.copy_button.setEnabled(bool(add_tags));self.copy_all_button.setEnabled(bool(all_accepted));self._update_review_action_states()
    def _sort_changed(self,column,order)->None:self._sort_column=column;self._sort_order=order
    def _render_suggestions(self,*_args)->None:
        selected_id=self._selected_observation_id(); decision=str(self.decision_filter.currentData()); visible={"accepted":{"accepted","keep"},"rejected":{"rejected","remove"}}.get(decision,{decision}); rows=[row for row in self._all_suggestion_rows if decision=="all" or row["decision"] in visible]
        self.suggestions.setSortingEnabled(False); self.suggestions.setRowCount(len(rows)); decision_order={"unreviewed":0,"accepted":1,"rejected":2}; match_order={"exact":0,"mapping":1,"déjà présent":2,"introuvable localement":3,"non applicable":4}
        for r,row in enumerate(rows):
            confidence=float(row["confidence"] or -1); match_text=str(row["match"]); match_key=next((rank for name,rank in match_order.items() if name in match_text.casefold()),9)
            values=((row["tag"],str(row["tag"]).casefold()),(row["confidence"],confidence),(row["decision"],decision_order.get(row["decision"],9)),(match_text,match_key),(str(row["id"]),int(row["id"])))
            for c,(text,key) in enumerate(values):self.suggestions.setItem(r,c,SuggestionItem(str(text),key))
        self.suggestions.setSortingEnabled(True); self.suggestions.sortItems(self._sort_column,self._sort_order); self.suggestions.resizeColumnsToContents(); target_id=self._pending_next_id if self._pending_fallback_row is not None else selected_id; selected_row=-1
        if target_id is not None:
            for r in range(self.suggestions.rowCount()):
                if int(self.suggestions.item(r,4).text())==target_id:selected_row=r;break
        if selected_row<0 and self.suggestions.rowCount():selected_row=0 if self._pending_fallback_row is None else min(self._pending_fallback_row,self.suggestions.rowCount()-1)
        if selected_row>=0:self.suggestions.selectRow(selected_row);self.suggestions.setFocus()
        self._pending_next_id=None;self._pending_fallback_row=None;self._update_review_action_states()
    def _update_review_action_states(self)->None:
        selected=bool(self.suggestions.selectedItems()); has_post=bool(self.current_post_id)
        self.accept_button.setEnabled(self._review_ready and selected);self.reject_button.setEnabled(self._review_ready and selected);self.map_button.setEnabled(self._review_ready and selected)
        self.refresh_button.setEnabled(has_post);self.copy_open_button.setEnabled(has_post);self.open_button.setEnabled(has_post)
    def set_analysis_request_state(self,text:str,busy:bool)->None:self.analysis_state.setText(text);self.analyze_button.setEnabled(not busy);self.analyze_button.setText("Analyse en cours…" if busy else "Analyser localement")
    def eventFilter(self,watched,event)->bool:
        if event.type()!=QEvent.Type.KeyPress:return super().eventFilter(watched,event)
        widget=watched if isinstance(watched,QWidget) else None
        if event.key()==Qt.Key.Key_Escape and self.mode_stack.currentWidget() is self.review:
            while widget is not None and widget is not self:
                if isinstance(widget,(QLineEdit,QTextEdit,QPlainTextEdit,QAbstractSpinBox)):return super().eventFilter(watched,event)
                widget=widget.parentWidget()
            self.show_search();return True
        if event.key()!=Qt.Key.Key_Space:return super().eventFilter(watched,event)
        while widget is not None and widget is not self:
            if isinstance(widget,(QLineEdit,QTextEdit,QPlainTextEdit,QAbstractSpinBox)):return super().eventFilter(watched,event)
            widget=widget.parentWidget()
        if event.modifiers()==Qt.KeyboardModifier.NoModifier:
            if not event.isAutoRepeat() and self.copy_open_button.isEnabled():self.copy_open_button.click()
            return True
        return super().eventFilter(watched,event)
    def retranslate(self)->None:
        text=self.catalog.text;self.title.setText(text("nav.tagging"));self.group.setTitle(text("tagging.group"));self.query_label.setText(text("tagging.query"))
        for key,label in self.spin_labels.items():label.setText(text(f"tagging.{key}"))
        self.start_button.setText(text("tagging.start_button"));self.stop_button.setText(text("tagging.stop"))
        if self.catalog.code=="fr":self.copy_open_button.setText("Copier + ouvrir [Espace]");self.copy_open_button.setToolTip("Copier les tags puis ouvrir le post (Espace)")
        else:self.copy_open_button.setText("Copy + open [Space]");self.copy_open_button.setToolTip("Copy tags then open the post (Space)")
        if self.start_button.isEnabled():self.state.setText(text("tagging.ready"))
