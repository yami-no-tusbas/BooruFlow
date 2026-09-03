"""Dry-run first UI with folder drops and hierarchical priority editing."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.auto_organize import rule_node_from_dict, rule_node_to_dict
from booruflow.presentation.pyside6.ui_components import DataTable

COLUMNS=("Fichier actuel","Site","Post ID","Récupération","Artiste actuel","Artiste distant",
         "Nom futur","Dossier actuel","Destination calculée","Règle gagnante","Statut","Raison")

class FolderDropList(QListWidget):
    def __init__(self):
        super().__init__(); self.setAcceptDrops(True); self.setSelectionMode(self.SelectionMode.ExtendedSelection)
    @staticmethod
    def normalized(path:Path)->Path: return Path(path).resolve(strict=False)
    def add_paths(self,paths):
        existing={str(self.normalized(Path(self.item(i).text()))).casefold() for i in range(self.count())}
        for path in paths:
            normalized=self.normalized(Path(path)); key=str(normalized).casefold()
            if normalized.is_dir() and key not in existing: self.addItem(str(normalized)); existing.add(key)
    def dragEnterEvent(self,event:QDragEnterEvent):
        if any(Path(url.toLocalFile()).is_dir() for url in event.mimeData().urls() if url.isLocalFile()): event.acceptProposedAction()
        else: event.ignore()
    def dragMoveEvent(self,event): event.acceptProposedAction()
    def dropEvent(self,event:QDropEvent):
        self.add_paths(Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()); event.acceptProposedAction()

class PriorityTree(QTreeWidget):
    order_changed=Signal()
    def __init__(self):
        super().__init__(); self.setColumnCount(6); self.setHeaderLabels(("Priorité / règle","Destination","Tag(s)","Sites","Actif","Type"))
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True); self.setAcceptDrops(True); self.setDropIndicatorShown(True)
    def dropEvent(self,event):
        selected=self.currentItem(); target=self.itemAt(event.position().toPoint())
        if selected is None: event.ignore(); return
        if self.dropIndicatorPosition()==QAbstractItemView.DropIndicatorPosition.OnItem: event.ignore(); return
        selected_parent=selected.parent(); target_parent=target.parent() if target else None
        if selected_parent is not target_parent: event.ignore(); return
        super().dropEvent(event); self.order_changed.emit()
    def apply_filter(self,text):
        needle=str(text).strip().casefold()
        def visit(item):
            child_visible=any(visit(item.child(index)) for index in range(item.childCount()))
            own=not needle or any(needle in item.text(column).casefold() for column in range(self.columnCount()))
            visible=own or child_visible; item.setHidden(not visible)
            if needle and child_visible:item.setExpanded(True)
            return visible
        for index in range(self.topLevelItemCount()):visit(self.topLevelItem(index))

class AutoOrganizePage(QWidget):
    analyze_requested=Signal(tuple,str,bool,bool,bool,str); execute_requested=Signal(); stop_requested=Signal()
    rules_changed=Signal(); rules_save_requested=Signal(); rules_reset_requested=Signal()
    def __init__(self,catalog)->None:
        super().__init__(); self.catalog=catalog; self._plans=[]; layout=QVBoxLayout(self); layout.setContentsMargins(28,20,28,24)
        title=QLabel("Rangement auto"); title.setStyleSheet("font-size: 22px; font-weight: 600;"); layout.addWidget(title)
        help_text=QLabel("Analyse et aperçu obligatoires avant toute opération. Les cas ambigus ne sont jamais exécutés."); help_text.setWordWrap(True); layout.addWidget(help_text)
        self.folders=FolderDropList(); self.folders.setMinimumHeight(90); layout.addWidget(self.folders)
        row=QHBoxLayout(); self.add_button=QPushButton("Ajouter un dossier"); self.remove_button=QPushButton("Retirer"); self.clear_button=QPushButton("Vider")
        row.addWidget(self.add_button); row.addWidget(self.remove_button); row.addWidget(self.clear_button); row.addStretch(); layout.addLayout(row)
        destination_row=QHBoxLayout(); self.destination=QLabel("Aucun dossier destination choisi"); self.destination_button=QPushButton("Choisir la destination")
        destination_row.addWidget(self.destination,1); destination_row.addWidget(self.destination_button); layout.addLayout(destination_row)
        options=QHBoxLayout(); self.mode=QComboBox(); self.mode.addItem("Ranger","organize"); self.mode.addItem("Actualiser uniquement","refresh_only")
        self.recursive=QCheckBox("Récursif"); self.recursive.setChecked(True); self.use_cache=QCheckBox("Utiliser le cache"); self.use_cache.setChecked(True); self.force_refresh=QCheckBox("Forcer le rafraîchissement distant")
        options.addWidget(self.mode); options.addWidget(self.recursive); options.addStretch(); layout.addLayout(options)
        cache_options=QHBoxLayout(); cache_options.addWidget(self.use_cache); cache_options.addWidget(self.force_refresh); cache_options.addStretch(); layout.addLayout(cache_options)
        self.rules_group=QGroupBox("Priorités / Règles de rangement"); group_layout=QVBoxLayout(self.rules_group)
        self.rule_filter=QLineEdit(); self.rule_filter.setPlaceholderText("Rechercher une règle (bikini, witch, sword, cat_ears…)"); group_layout.addWidget(self.rule_filter)
        self.priority_tree=PriorityTree(); self.priority_tree.setMinimumHeight(230); group_layout.addWidget(self.priority_tree)
        priorities=QHBoxLayout(); self.up=QPushButton("Monter"); self.down=QPushButton("Descendre"); self.top=QPushButton("Tout en haut"); self.bottom=QPushButton("Tout en bas"); self.save_rules=QPushButton("Enregistrer"); self.reset_rules=QPushButton("Réinitialiser les priorités")
        for button in (self.up,self.down,self.top,self.bottom): priorities.addWidget(button)
        priorities.addStretch(); group_layout.addLayout(priorities)
        persistence=QHBoxLayout(); persistence.addStretch(); persistence.addWidget(self.save_rules); persistence.addWidget(self.reset_rules); group_layout.addLayout(persistence)
        self.rules_inventory=QLabel(); self.rules_inventory.setWordWrap(True); group_layout.addWidget(self.rules_inventory)
        self.rules_state=QLabel("L’ordre vertical des frères détermine la priorité."); group_layout.addWidget(self.rules_state); layout.addWidget(self.rules_group)
        actions=QHBoxLayout(); self.analyze_button=QPushButton("Analyser"); self.stop_button=QPushButton("Arrêter"); self.execute_button=QPushButton("Exécuter les opérations validées")
        self.stop_button.setEnabled(False); self.execute_button.setEnabled(False); actions.addWidget(self.analyze_button); actions.addWidget(self.stop_button); actions.addStretch(); actions.addWidget(self.execute_button); layout.addLayout(actions)
        self.progress=QProgressBar(); self.progress.setRange(0,1); self.progress.setValue(0); layout.addWidget(self.progress); self.state=QLabel("Prêt."); self.state.setWordWrap(True); layout.addWidget(self.state)
        self.last_error=QLabel("Dernière erreur : aucune"); self.last_error.setWordWrap(True); layout.addWidget(self.last_error)
        self.error_summary=QPlainTextEdit(); self.error_summary.setReadOnly(True); self.error_summary.setMaximumHeight(110); self.error_summary.setPlaceholderText("Les erreurs identiques seront regroupées ici et restent copiables."); layout.addWidget(self.error_summary); self._error_groups={}
        self.table=DataTable(0,len(COLUMNS)); self.table.setHorizontalHeaderLabels(COLUMNS); self.table.setSortingEnabled(True); self.table.set_empty_text(self.catalog.text("table.empty_analysis") if self.catalog else "Lancez une analyse pour prévisualiser les opérations proposées.")
        header=self.table.horizontalHeader(); header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column,width in enumerate((240,80,90,95,130,130,260,210,210,190,100,260)): self.table.setColumnWidth(column,width)
        layout.addWidget(self.table,1)
        self.details=QPlainTextEdit(); self.details.setReadOnly(True); self.details.setPlaceholderText("Sélectionnez une ligne pour voir le chemin de priorité."); self.details.setMaximumHeight(150); layout.addWidget(self.details)
        self.add_button.clicked.connect(self._add); self.remove_button.clicked.connect(self._remove); self.clear_button.clicked.connect(self.folders.clear); self.destination_button.clicked.connect(self._destination); self.analyze_button.clicked.connect(self._analyze)
        self.stop_button.clicked.connect(self.stop_requested.emit); self.execute_button.clicked.connect(self.execute_requested.emit); self.table.itemSelectionChanged.connect(self._show_details)
        self.priority_tree.order_changed.connect(self._rules_modified); self.priority_tree.itemChanged.connect(lambda *_:self._rules_modified())
        self.rule_filter.textChanged.connect(self.priority_tree.apply_filter)
        self.up.clicked.connect(lambda:self._move(-1,False)); self.down.clicked.connect(lambda:self._move(1,False)); self.top.clicked.connect(lambda:self._move(-1,True)); self.bottom.clicked.connect(lambda:self._move(1,True))
        self.save_rules.clicked.connect(self.rules_save_requested.emit); self.reset_rules.clicked.connect(self.rules_reset_requested.emit)
    def _add(self):
        value=QFileDialog.getExistingDirectory(self,"Choisir un dossier source")
        if value: self.folders.add_paths((Path(value),))
    def _remove(self):
        for item in self.folders.selectedItems(): self.folders.takeItem(self.folders.row(item))
    def _destination(self):
        value=QFileDialog.getExistingDirectory(self,"Choisir la racine de destination")
        if value: self.destination.setText(str(Path(value).resolve(strict=False)))
    def _analyze(self):
        roots=tuple(Path(self.folders.item(i).text()) for i in range(self.folders.count()))
        if not roots: self.state.setText("Ajoutez au moins un dossier source."); return
        destination=self.destination.text() if Path(self.destination.text()).is_dir() else ""
        if self.mode.currentData()=="organize" and not destination: self.state.setText("Choisissez une racine de destination pour le mode Ranger."); return
        self.analyze_requested.emit(roots,str(self.mode.currentData()),self.recursive.isChecked(),self.use_cache.isChecked(),self.force_refresh.isChecked(),destination)
    def set_rules(self,rules):
        self.priority_tree.blockSignals(True); self.priority_tree.setUpdatesEnabled(False); self.priority_tree.clear()
        def add(parent,node):
            type_label={"branch":"Branche","route":"Routeur","dynamic":"Fallback","rule":"Règle"}.get(node.kind,node.kind)
            item=QTreeWidgetItem(); item.setText(0,node.label); item.setText(1,node.destination); item.setText(2,", ".join(node.tags)); item.setText(3,", ".join(node.sites)); item.setText(4,"Oui" if node.active else "Non"); item.setText(5,type_label)
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsDragEnabled|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(4,Qt.CheckState.Checked if node.active else Qt.CheckState.Unchecked)
            data=rule_node_to_dict(node); data.pop("children",None); item.setData(0,Qt.ItemDataRole.UserRole,data); parent.addChild(item) if parent else self.priority_tree.addTopLevelItem(item)
            for child in node.children: add(item,child)
        for node in rules: add(None,node)
        self.priority_tree.expandToDepth(1); self.priority_tree.blockSignals(False); self.priority_tree.setUpdatesEnabled(True)
        self.priority_tree.apply_filter(self.rule_filter.text()); self.rules_state.setText("L’ordre vertical des frères détermine la priorité.")
    def set_rule_inventory(self,inventory):
        branches=" · ".join(f"{name}: {count}" for name,count in inventory.get("branches",{}).items())
        self.rules_inventory.setText(f"Feuilles Tags: {inventory.get('tags_total',0)} — Gelbooru: {inventory.get('gelbooru',0)} · e621: {inventory.get('e621',0)} · partagées: {inventory.get('shared',0)}\n{branches}")
    def rules(self):
        def build(item):
            data=dict(item.data(0,Qt.ItemDataRole.UserRole)); data["active"]=item.checkState(4)==Qt.CheckState.Checked; data["children"]=[rule_node_to_dict(build(item.child(i))) for i in range(item.childCount())]; return rule_node_from_dict(data)
        return tuple(build(self.priority_tree.topLevelItem(i)) for i in range(self.priority_tree.topLevelItemCount()))
    def _move(self,direction,to_edge):
        item=self.priority_tree.currentItem()
        if not item:return
        parent=item.parent(); count=parent.childCount() if parent else self.priority_tree.topLevelItemCount(); index=parent.indexOfChild(item) if parent else self.priority_tree.indexOfTopLevelItem(item)
        target=0 if to_edge and direction<0 else count-1 if to_edge else index+direction
        if target<0 or target>=count or target==index:return
        moved=parent.takeChild(index) if parent else self.priority_tree.takeTopLevelItem(index); parent.insertChild(target,moved) if parent else self.priority_tree.insertTopLevelItem(target,moved); self.priority_tree.setCurrentItem(moved); self._rules_modified()
    def _rules_modified(self): self.rules_state.setText("Modifié — enregistrez puis relancez l’analyse."); self.rules_changed.emit()
    def clear_plan(self,message): self._plans=[]; self.table.setRowCount(0); self.details.clear(); self.execute_button.setEnabled(False); self.state.setText(message)
    def set_running(self,running):
        self.analyze_button.setEnabled(not running); self.stop_button.setEnabled(running); self.execute_button.setEnabled(False if running else self.execute_button.isEnabled())
        self.rules_group.setEnabled(not running); self.add_button.setEnabled(not running); self.remove_button.setEnabled(not running); self.clear_button.setEnabled(not running)
        if running:self.clear_plan("Analyse en cours…"); self.progress.setRange(0,0); self._error_groups={}; self.error_summary.clear(); self.last_error.setText("Dernière erreur : aucune")
        else:self.progress.setRange(0,1); self.progress.setValue(1)
    def set_stopping(self):
        self.analyze_button.setEnabled(False); self.stop_button.setEnabled(False); self.execute_button.setEnabled(False); self.state.setText("Annulation demandée…")
    def set_analysis_progress(self,values):
        total=int(values.get("total",0)); processed=int(values.get("processed",0)); scanned=int(values.get("scanned",0))
        if total>0:self.progress.setRange(0,total); self.progress.setValue(processed)
        else:self.progress.setRange(0,0)
        self.state.setText(f"Fichiers : {processed}/{total or '?'} · scannés : {scanned} · cache : {values.get('cache_hits',0)} · API : {values.get('api_calls',0)} · ambiguïtés : {values.get('ambiguities',0)} · erreurs : {values.get('errors',0)}\nTags matchés : {values.get('tag_matches',0)} · classés Tags : {values.get('classified_tags',0)} · Species : {values.get('classified_species',0)} · Copyright : {values.get('classified_copyright',0)} · Artist : {values.get('classified_artist',0)} · C&L : {values.get('routed_cl',0)} · Y&L : {values.get('routed_yl',0)}")
        if values.get("last_error"): self.last_error.setText(f"Dernière erreur : {values['last_error']}")
    def record_error(self,detail):
        signature=str(detail.get("signature") or f"{detail.get('stage')}|{detail.get('exception_type')}|{detail.get('message')}")
        entry=self._error_groups.setdefault(signature,{"count":0,"sample":dict(detail)}); entry["count"]+=1; status=detail.get("status")
        short=f"{detail.get('site') or '?'} post {detail.get('post_id') or '?'} — " + (f"HTTP {status}" if status is not None else f"{detail.get('exception_type')}: {detail.get('message')}")
        self.last_error.setText(f"Dernière erreur : {short}")
        visible=[]
        for value in self._error_groups.values():
            sample=value["sample"]; visible.append(f"[{value['count']}] {sample.get('site','?')} · {sample.get('stage','?')} · {sample.get('status') or sample.get('exception_type','?')}\n{sample.get('message','')}\n{sample.get('endpoint','')}")
        self.error_summary.setPlainText("\n\n".join(visible))
    def show_plans(self,plans):
        started=time.perf_counter(); self._plans=list(plans); self.table.setUpdatesEnabled(False); self.table.blockSignals(True); self.table.setSortingEnabled(False); self.table.setRowCount(len(plans))
        try:
            for row,p in enumerate(plans):
                values=(p.source,p.site,p.post_id,p.fetch_state,p.current_artist,p.remote_artist,p.future_name,p.source.parent,p.destination.parent if p.destination else ""," / ".join(p.winner_path) or p.winner,p.status.value,p.message)
                for col,value in enumerate(values):self.table.setItem(row,col,QTableWidgetItem(str(value)))
            populated=time.perf_counter(); self.table.setSortingEnabled(True)
        finally:
            self.table.blockSignals(False); self.table.setUpdatesEnabled(True); self.table.viewport().update()
        finished=time.perf_counter()
        return {"populate_ms":(populated-started)*1000,"finalize_ms":(finished-populated)*1000,"total_ms":(finished-started)*1000}
    def _show_details(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self._plans):self.details.clear();return
        p=self._plans[row]; matches="\n".join(f"- {value}" for value in p.candidates) or "- aucune"
        self.details.setPlainText(f"Route:\n{p.route or 'normal'}\n\nMatches:\n{matches}\n\nWinner:\n{' / '.join(p.winner_path) or p.winner or 'aucune'}\n\nFallback:\n{p.fallback or 'aucun'}\n\nDestination:\n{p.destination.parent if p.destination else 'aucune'}\n\nReason:\n{p.message or 'Aucune règle applicable.'}")
