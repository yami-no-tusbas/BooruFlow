"""Interactive multi-axis Similar Artists exploration page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from booruflow.domain.similar_artists import ArtistIdentity
from booruflow.infrastructure.image_sources import post_page_url
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.image_analysis_page import ScaledImageLabel


def confidence_label(value: str) -> str:
    return {"very_low": "très faible", "low": "faible", "medium": "moyen",
            "established": "établi", "unbuilt": "non construit"}.get(value, value)


class SimilarArtistsPage(QWidget):
    artist_search_requested = Signal(object)
    item_search_requested = Signal(int)
    local_image_requested = Signal(str)
    update_requested = Signal()
    gallery_requested = Signal(object)
    compare_requested = Signal(object)
    references_added = Signal(list)
    reference_removed = Signal(int)
    references_cleared = Signal()
    remote_requested = Signal(str,str)
    continue_requested = Signal()
    reference_activated = Signal(int)
    corpus_requested = Signal()
    unassigned_examine_requested = Signal()
    references_assign_requested = Signal()
    filename_repair_requested = Signal()
    library_index_requested = Signal(list)
    library_pause_requested = Signal()
    library_cancel_requested = Signal()
    library_resume_requested = Signal()
    remote_discovery_requested = Signal(str, str)
    remote_cancel_requested = Signal()
    artist_open_requested = Signal(object)
    remote_purge_requested = Signal(int)
    local_duplicates_requested = Signal()

    def __init__(self, catalog: LanguageCatalog) -> None:
        super().__init__(); self.catalog = catalog; self.setAcceptDrops(True)
        self.artist_options: list[dict] = []; self.result_rows: list[dict] = []
        root = QVBoxLayout(self); root.setContentsMargins(20, 16, 20, 20); root.setSpacing(10)
        self.title = QLabel(); self.title.setStyleSheet("font-size:22px;font-weight:600")
        root.addWidget(self.title)
        self.subtitle=QLabel("Trouver des artistes à partir de références visuelles");self.subtitle.setStyleSheet("font-size:17px;font-weight:600");root.addWidget(self.subtitle)
        self.drop_zone=QPushButton("Déposez une ou plusieurs images ou dossiers ici\nou cliquez pour choisir des fichiers")
        self.drop_zone.setMinimumHeight(105);self.drop_zone.setStyleSheet("border:2px dashed #55aaff;padding:20px;font-size:16px")
        self.drop_zone.clicked.connect(self._choose_many);root.addWidget(self.drop_zone)
        remote_row=QHBoxLayout();self.remote_site=QComboBox();self.remote_site.addItem("Gelbooru","gelbooru");self.remote_site.addItem("e621","e621");self.remote_id=QLineEdit();self.remote_id.setPlaceholderText("Post ID");self.remote_load=QPushButton("Charger")
        remote_row.addWidget(QLabel("Post distant :"));remote_row.addWidget(self.remote_site);remote_row.addWidget(self.remote_id,1);remote_row.addWidget(self.remote_load);root.addLayout(remote_row)
        self.references_group=QGroupBox("1. Références — 0 image");references_layout=QVBoxLayout(self.references_group);self.references=QListWidget();self.references.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection);self.references.setViewMode(QListWidget.ViewMode.IconMode);self.references.setIconSize(QtCoreSize(110,90));self.references.setResizeMode(QListWidget.ResizeMode.Adjust);self.references.setMaximumHeight(170);references_layout.addWidget(self.references)
        reference_actions=QHBoxLayout();self.add_references=QPushButton("Ajouter des images");self.remove_reference=QPushButton("Retirer la sélection");self.clear_references=QPushButton("Vider");self.continue_button=QPushButton("Continuer quand même");self.continue_button.hide()
        for widget in (self.add_references,self.remove_reference,self.clear_references,self.continue_button):reference_actions.addWidget(widget)
        reference_actions.addStretch(1);references_layout.addLayout(reference_actions);root.addWidget(self.references_group)
        controls = QGroupBox("Options avancées");controls.setCheckable(True);controls.setChecked(False); grid = QGridLayout(controls)
        self.mode = QComboBox(); self.mode.addItem("Artiste → artistes", "artist"); self.mode.addItem("Image → artistes / identification", "image")
        self.mode.hide()
        self.backend = QComboBox(); self.backend.addItem("Author_ID", "author_id_embedding"); self.backend.addItem("OpenCLIP", "openclip")
        self.minimum_images = QSpinBox(); self.minimum_images.setRange(1, 10_000); self.minimum_images.setValue(2)
        self.limit = QSpinBox(); self.limit.setRange(1, 200); self.limit.setValue(20)
        grid.addWidget(QLabel("Recherche par :"),0,0); grid.addWidget(self.mode,0,1)
        grid.addWidget(QLabel("Classement principal :"),0,2); grid.addWidget(self.backend,0,3)
        grid.addWidget(QLabel("Minimum images :"),0,4); grid.addWidget(self.minimum_images,0,5)
        grid.addWidget(QLabel("Top :"),0,6); grid.addWidget(self.limit,0,7)
        self.artist_search = QLineEdit(); self.artist_search.setPlaceholderText("Rechercher un artist tag…")
        self.artist_list = QListWidget(); self.artist_list.setMaximumHeight(105)
        self.artist_go = QPushButton("Rechercher")
        grid.addWidget(self.artist_search,1,0,1,3); grid.addWidget(self.artist_go,1,3)
        grid.addWidget(self.artist_list,2,0,1,4)
        self.local_path = QLineEdit(); self.local_path.setPlaceholderText("Déposer une image ou choisir un fichier")
        self.choose_file = QPushButton("Choisir…"); self.image_go = QPushButton("Analyser l’image")
        self.item_id = QSpinBox(); self.item_id.setRange(0, 2_147_483_647); self.item_go = QPushButton("AnalysisItem")
        grid.addWidget(self.local_path,1,4,1,2); grid.addWidget(self.choose_file,1,6); grid.addWidget(self.image_go,1,7)
        self.item_label=QLabel("Ouvrir un AnalysisItem par ID interne :");self.item_id.setToolTip("Identifiant interne BooruFlow d’une image déjà indexée. Fonction destinée au diagnostic ou à un usage avancé.");self.item_go.setText("Ouvrir")
        grid.addWidget(self.item_label,2,4); grid.addWidget(self.item_id,2,5); grid.addWidget(self.item_go,2,6)
        self.purge_days=QSpinBox();self.purge_days.setRange(1,3650);self.purge_days.setValue(90);self.purge_remote=QPushButton("Purger les profils distants inutilisés");grid.addWidget(QLabel("Inutilisés depuis (jours) :"),3,0);grid.addWidget(self.purge_days,3,1);grid.addWidget(self.purge_remote,3,2,1,3)
        root.addWidget(controls)
        status_row=QHBoxLayout(); self.corpus=QLabel(); self.corpus.setWordWrap(True); status_row.addWidget(self.corpus,1)
        self.update_profiles=QPushButton("Mettre à jour les profils"); status_row.addWidget(self.update_profiles); root.addLayout(status_row)
        artist_health=QVBoxLayout();self.unassigned_status=QLabel();artist_health.addWidget(self.unassigned_status);artist_actions=QHBoxLayout();self.examine_unassigned=QPushButton("Examiner");self.repair_filenames=QPushButton("Réparer les filenames");self.repair_filenames.setToolTip("Réparer les métadonnées depuis les noms de fichiers");self.assign_references=QPushButton("Associer à un artiste");artist_actions.addWidget(self.examine_unassigned);artist_actions.addWidget(self.repair_filenames);artist_actions.addWidget(self.assign_references);artist_actions.addStretch(1);artist_health.addLayout(artist_actions);root.addLayout(artist_health)
        self.analysis_title=QLabel("2. Analyse");self.analysis_title.setStyleSheet("font-size:17px;font-weight:600");root.addWidget(self.analysis_title)
        self.state=QLabel("Prêt à recevoir des références."); self.state.setWordWrap(True); root.addWidget(self.state)
        self.query_summary=QLabel(); self.query_summary.setStyleSheet("font-weight:600"); root.addWidget(self.query_summary)
        self.use_corpus=QPushButton("← Revenir au corpus complet");self.use_corpus.hide();root.addWidget(self.use_corpus)
        self.identification=QLabel(); self.identification.setWordWrap(True); self.identification.hide(); root.addWidget(self.identification)
        self.results_title=QLabel("3. Artistes similaires — 0 résultat");self.results_title.setStyleSheet("font-size:17px;font-weight:600");root.addWidget(self.results_title)
        self.results=QTableWidget(0,12); self.results.setHorizontalHeaderLabels((
            "#","Artiste","Site","Style","Contenu","Palette","Images","Profil","Cohérence","Œuvres","Comparer","Booru",
        )); self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.horizontalHeaderItem(3).setToolTip("Style = similarité Author_ID");self.results.horizontalHeaderItem(4).setToolTip("Contenu = similarité OpenCLIP");self.results.horizontalHeaderItem(5).setToolTip("Palette = distance entre les caractéristiques de couleur")
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.results.setSortingEnabled(False)
        self.results.horizontalHeader().setStretchLastSection(True); root.addWidget(self.results,1)
        self.gallery=QPushButton("Voir les œuvres proches"); self.compare=QPushButton("Comparer");self.gallery.hide();self.compare.hide()
        self.mode.currentIndexChanged.connect(self._mode_changed); self.artist_search.textChanged.connect(self._filter_artists)
        self.artist_go.clicked.connect(self._emit_artist); self.artist_list.itemDoubleClicked.connect(lambda _item:self._emit_artist())
        self.choose_file.clicked.connect(self._choose); self.image_go.clicked.connect(self._emit_local)
        self.item_go.clicked.connect(lambda:self.item_search_requested.emit(self.item_id.value()))
        self.update_profiles.clicked.connect(self.update_requested); self.gallery.clicked.connect(lambda:self._emit_result(self.gallery_requested)); self.compare.clicked.connect(lambda:self._emit_result(self.compare_requested))
        self.minimum_images.valueChanged.connect(self._refilter); self.limit.valueChanged.connect(self._refilter)
        self.add_references.clicked.connect(self._choose_many);self.remove_reference.clicked.connect(self._remove_selected_reference);self.clear_references.clicked.connect(self.references_cleared);self.continue_button.clicked.connect(self.continue_requested);self.remote_load.clicked.connect(lambda:self.remote_requested.emit(str(self.remote_site.currentData()),self.remote_id.text().strip()) if self.remote_id.text().strip() else None)
        self.references.itemDoubleClicked.connect(lambda item:self.reference_activated.emit(int(item.data(Qt.ItemDataRole.UserRole))));self.use_corpus.clicked.connect(self.corpus_requested);self.results.itemDoubleClicked.connect(lambda _item:self._emit_result(self.gallery_requested));controls.toggled.connect(lambda checked:self._set_advanced_visible(controls,checked))
        self.examine_unassigned.clicked.connect(self.unassigned_examine_requested);self.assign_references.clicked.connect(self.references_assign_requested)
        self.repair_filenames.clicked.connect(self.filename_repair_requested)
        mass=QVBoxLayout();self.library_title=QLabel("Indexation bibliothèque");self.library_title.setStyleSheet("font-size:17px;font-weight:600");mass.addWidget(self.library_title);library_actions=QHBoxLayout();self.library_index=QPushButton("Indexer des dossiers");self.library_files=QPushButton("Indexer des fichiers");self.library_resume=QPushButton("Reprendre");self.library_resume.hide();self.library_pause=QPushButton("Pause");self.library_cancel=QPushButton("Annuler");self.library_pause.setEnabled(False);self.library_cancel.setEnabled(False);library_actions.addWidget(self.library_index);library_actions.addWidget(self.library_files);library_actions.addWidget(self.library_resume);library_actions.addWidget(self.library_pause);library_actions.addWidget(self.library_cancel);library_actions.addStretch(1);mass.addLayout(library_actions);self.library_status=QLabel("Aucune indexation active");self.library_status.setWordWrap(True);mass.addWidget(self.library_status);self.library_phase=QLabel("Phase : —");mass.addWidget(self.library_phase);self.library_current=QLabel("Fichier courant : —");self.library_current.setWordWrap(True);mass.addWidget(self.library_current);self.library_progress=QProgressBar();self.library_progress.setRange(0,100);self.library_progress.setValue(0);self.library_progress.setFormat("0 / 0 · 0.0 %");mass.addWidget(self.library_progress);discovery_actions=QHBoxLayout();self.discovery_source=QComboBox();self.discovery_source.addItem("Auto","auto");self.discovery_source.addItem("Gelbooru","gelbooru");self.discovery_source.addItem("e621","e621");self.discovery_source.addItem("Tous","all");self.discovery_source.setToolTip("Boards utilisés pour la découverte distante");self.discovery_mode=QComboBox();self.discovery_mode.addItem("Rapide","quick");self.discovery_mode.addItem("Normal","normal");self.discovery_mode.addItem("Large","large");self.discovery_mode.setToolTip("Budget de découverte distante");self.discover_remote=QPushButton("Découvrir à distance");self.only_new=QCheckBox("Nouveaux uniquement");discovery_actions.addWidget(QLabel("Source :"));discovery_actions.addWidget(self.discovery_source);discovery_actions.addWidget(self.discovery_mode);discovery_actions.addWidget(self.discover_remote);discovery_actions.addWidget(self.only_new);discovery_actions.addStretch(1);mass.addLayout(discovery_actions);root.insertLayout(root.indexOf(self.analysis_title),mass)
        self.local_duplicates=QPushButton("Voir les doublons locaux");mass.insertWidget(2,self.local_duplicates,0,Qt.AlignmentFlag.AlignLeft)
        self.remote_title=QLabel("4. Découverte distante");self.remote_title.setStyleSheet("font-size:17px;font-weight:600");mass.insertWidget(mass.count()-1,self.remote_title);self.remote_status=QLabel("Aucune découverte active");self.remote_status.setWordWrap(True);mass.addWidget(self.remote_status);self.remote_progress=QProgressBar();self.remote_progress.setRange(0,100);self.remote_progress.setValue(0);mass.addWidget(self.remote_progress);self.remote_cancel=QPushButton("Annuler la découverte");self.remote_cancel.setEnabled(False);mass.addWidget(self.remote_cancel,0,Qt.AlignmentFlag.AlignLeft)
        self.discovery_mode.setItemData(0,"~20 artistes · ~8 images/artiste",Qt.ItemDataRole.ToolTipRole);self.discovery_mode.setItemData(1,"~60 artistes · ~16 images/artiste",Qt.ItemDataRole.ToolTipRole);self.discovery_mode.setItemData(2,"~120 artistes · ~30 images/artiste",Qt.ItemDataRole.ToolTipRole)
        self.library_index.clicked.connect(self._choose_library_roots);self.library_files.clicked.connect(self._choose_library_files);self.library_resume.clicked.connect(self.library_resume_requested);self.library_pause.clicked.connect(self.library_pause_requested);self.library_cancel.clicked.connect(self.library_cancel_requested);self.local_duplicates.clicked.connect(self.local_duplicates_requested);self.discover_remote.clicked.connect(lambda:self.remote_discovery_requested.emit(str(self.discovery_mode.currentData()),str(self.discovery_source.currentData())));self.remote_cancel.clicked.connect(self.remote_cancel_requested)
        self.only_new.toggled.connect(self._refilter)
        self.purge_remote.clicked.connect(lambda:self.remote_purge_requested.emit(self.purge_days.value()))
        self._advanced_widgets=(self.mode,self.backend,self.minimum_images,self.limit,self.artist_search,self.artist_list,self.artist_go,self.item_label,self.item_id,self.item_go,self.corpus,self.update_profiles,self.purge_days,self.purge_remote)
        self._mode_changed();self._set_advanced_visible(controls,False); self.retranslate()

    def retranslate(self) -> None:
        self.title.setText(self.catalog.text("nav.similar_artists"))

    def set_artists(self, options: list[dict]) -> None:
        self.artist_options=list(options); self._filter_artists(self.artist_search.text())

    def set_backend_available(self, backend: str, available: bool, reason: str = "") -> None:
        index=self.backend.findData(backend)
        if index>=0:
            item=self.backend.model().item(index);item.setEnabled(available);item.setToolTip(reason)
            if not available and self.backend.currentIndex()==index:
                replacement=next((value for value in range(self.backend.count()) if self.backend.model().item(value).isEnabled()),-1)
                if replacement>=0:self.backend.setCurrentIndex(replacement)

    def _filter_artists(self, text: str) -> None:
        folded=text.casefold().strip(); self.artist_list.clear()
        for option in self.artist_options:
            artist=option["artist"]
            if folded and folded not in artist.tag.casefold(): continue
            suffix="" if option["profiled"] else " — profil non construit"
            item=QListWidgetItem(f"{artist.tag} — {artist.site.title()} — {option['image_count']} images{suffix}")
            item.setData(Qt.ItemDataRole.UserRole,artist); self.artist_list.addItem(item)
            if self.artist_list.count()>=100: break

    def _selected_artist(self) -> ArtistIdentity | None:
        item=self.artist_list.currentItem() or (self.artist_list.item(0) if self.artist_list.count() else None)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _emit_artist(self) -> None:
        artist=self._selected_artist()
        if artist:self.artist_search_requested.emit(artist)

    def _choose(self) -> None:
        value,_=QFileDialog.getOpenFileName(self,"Choisir une image","","Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if value:self.local_path.setText(value)
    def _choose_many(self)->None:
        values,_=QFileDialog.getOpenFileNames(self,"Choisir des références","","Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;Tous les fichiers (*)")
        if values:self.references_added.emit(values)
    def _choose_library_roots(self)->None:
        dialog=QFileDialog(self,"Indexer des dossiers");dialog.setFileMode(QFileDialog.FileMode.Directory);dialog.setOption(QFileDialog.Option.ShowDirsOnly,True);dialog.setOption(QFileDialog.Option.DontUseNativeDialog,True)
        for view_type in (QListView,QTreeView):
            for view in dialog.findChildren(view_type):view.setSelectionMode(view.SelectionMode.ExtendedSelection)
        if dialog.exec():self.library_index_requested.emit(dialog.selectedFiles())
    def _choose_library_files(self)->None:
        values,_=QFileDialog.getOpenFileNames(self,"Indexer des fichiers","","Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if values:self.library_index_requested.emit(values)
    def _emit_local(self) -> None:
        if self.local_path.text().strip():self.local_image_requested.emit(self.local_path.text().strip())
    def _mode_changed(self) -> None:
        for widget in (self.artist_search,self.artist_list,self.artist_go,self.item_id,self.item_go):widget.setVisible(True)
        for widget in (self.local_path,self.choose_file,self.image_go):widget.hide()

    def _set_advanced_visible(self,_group,visible:bool)->None:
        _group.setMaximumHeight(16777215 if visible else 30)
        for widget in getattr(self,"_advanced_widgets",()):widget.setVisible(visible)

    def show_references(self,entries:list[dict],summary:str,quality:str,warning:str="",active_item_id:int|None=None)->None:
        self.references.clear();self.references_group.setTitle(f"1. Références — {len(entries)} image(s) unique(s)")
        for entry in entries:
            text=f"#{entry['item_id']}"+("\nRequête active" if entry["item_id"]==active_item_id else "")
            if entry.get("similarity") is not None:text+=f"\ncohérence {entry['similarity']:.3f}"
            item=QListWidgetItem(QIcon(str(entry["path"])),text);item.setData(Qt.ItemDataRole.UserRole,entry["item_id"]);item.setToolTip(entry.get("provenance",str(entry["path"])));self.references.addItem(item)
            if entry["item_id"]==active_item_id:item.setSelected(True)
        self.state.setText(" · ".join(value for value in (summary,f"Qualité : {quality}",warning) if value));self.continue_button.setVisible(len(entries)==1)
    def _remove_selected_reference(self)->None:
        item=self.references.currentItem()
        if item:self.reference_removed.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def show_results(self, query: str, rows: list[dict], identification: dict | None=None) -> None:
        self.query_summary.setText(query); self.result_rows=list(rows); self.identification.setVisible(bool(identification))
        if identification:
            one=identification.get("top1"); two=identification.get("top2"); margin=identification.get("margin")
            self.identification.setText("Artistes probables — " + "; ".join(filter(None,(
                f"1. {one.artist.tag} {one.centroid_similarity:.4f} ({one.image_count} images)" if one else "",
                f"2. {two.artist.tag} {two.centroid_similarity:.4f}" if two else "",
                f"marge {margin:.4f}" if margin is not None else "",
            ))))
        self._refilter()

    def set_single_reference_mode(self,enabled:bool,count:int)->None:
        self.use_corpus.setText(f"← Utiliser les {count} références");self.use_corpus.setVisible(enabled and count>1)

    def _refilter(self,*_args) -> None:
        rows=[row for row in self.result_rows if row["image_count"]>=self.minimum_images.value() and (not self.only_new.isChecked() or row.get("is_new",False))][:self.limit.value()]
        self.results.setRowCount(len(rows)); self._visible_rows=rows
        self.results_title.setText(f"3. Artistes similaires — {len(rows)} résultat"+("s" if len(rows)!=1 else ""))
        for index,row in enumerate(rows):
            values=("#1 · Meilleur résultat" if index==0 else f"#{index+1}",row["artist"].tag,row["artist"].site,
                    "—" if row.get("author_id") is None else f"{row['author_id']:.4f}",
                    "—" if row.get("openclip") is None else f"{row['openclip']:.4f}",
                    "—" if row.get("palette_distance") is None else f"{row['palette_distance']:.4f}",
                    row["image_count"],confidence_label(row["confidence"]),
                    "—" if row.get("coherence") is None else f"{row['coherence']:.4f}")
            for column,value in enumerate(values):self.results.setItem(index,column,QTableWidgetItem(str(value)))
            if row.get("representative"):
                self.results.item(index,1).setIcon(QIcon(str(row["representative"])))
            gallery=QPushButton("Œuvres proches");compare=QPushButton("Comparer");artist=row["artist"]
            gallery.clicked.connect(lambda _checked=False,value=artist:self.gallery_requested.emit(value));compare.clicked.connect(lambda _checked=False,value=artist:self.compare_requested.emit(value));self.results.setCellWidget(index,9,gallery);self.results.setCellWidget(index,10,compare)
            if artist.site in {"gelbooru","e621"}:
                remote=QPushButton("Ouvrir");remote.clicked.connect(lambda _checked=False,value=artist:self.artist_open_requested.emit(value));self.results.setCellWidget(index,11,remote)
            if row.get("remote_discovery"):
                gallery.setText("Œuvres / Booru");gallery.setToolTip("Clic : œuvres proches. L’artiste peut aussi être ouvert depuis le menu contextuel.")
        self.results.resizeColumnsToContents()
        if rows:self.results.selectRow(0)

    def _emit_result(self, signal) -> None:
        row=self.results.currentRow()
        if row>=0 and row<len(getattr(self,"_visible_rows",[])):signal.emit(self._visible_rows[row]["artist"])

    def dragEnterEvent(self,event:QDragEnterEvent)->None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):event.acceptProposedAction()
    def dropEvent(self,event:QDropEvent)->None:
        paths=[url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:self.references_added.emit(paths);event.acceptProposedAction()


class ImageGalleryDialog(QDialog):
    def __init__(self,title:str,images:list[dict],parent=None,pixel_resolver=None,all_images:list[dict]|None=None)->None:
        super().__init__(parent);self.setWindowTitle(title);self.resize(1000,700)
        root=QVBoxLayout(self);mode=QComboBox();mode.addItem("Les plus proches","closest");mode.addItem("Toutes les œuvres","all");root.addWidget(mode);split=QSplitter(); thumbnails=QListWidget(); thumbnails.setViewMode(QListWidget.ViewMode.IconMode);thumbnails.setResizeMode(QListWidget.ResizeMode.Adjust);thumbnails.setIconSize(QtCoreSize(120,120));preview_host=QWidget();preview_layout=QVBoxLayout(preview_host);preview=ScaledImageLabel();self.details=QTextBrowser();self.details.setOpenExternalLinks(True);self.details.setMaximumHeight(150);preview_layout.addWidget(preview,1);preview_layout.addWidget(self.details);split.addWidget(thumbnails);split.addWidget(preview_host);split.setStretchFactor(1,1);root.addWidget(split,1)
        zoom=QComboBox();
        for label,value in (("Fit",0),("100 %",100),("200 %",200),("400 %",400)):zoom.addItem(label,value)
        zoom.currentIndexChanged.connect(lambda:preview.set_zoom(int(zoom.currentData())));root.addWidget(zoom)
        collections={"closest":list(images),"all":list(all_images if all_images is not None else images)};loaded=0
        load_more=QPushButton("Charger 24 images supplémentaires");root.addWidget(load_more,0,Qt.AlignmentFlag.AlignLeft)
        def populate(reset=False):
            nonlocal loaded
            if reset:thumbnails.clear();loaded=0
            values=collections[str(mode.currentData())];end=min(len(values),loaded+24)
            for image in values[loaded:end]:
                score=image.get("score");suffix=f" · {score:.4f}" if score is not None and str(mode.currentData())=="closest" else "";path=Path(str(image["path"]));item=QListWidgetItem(QIcon(str(path)) if path.is_file() else QIcon(),f"#{image['item_id']}{suffix}\nChargement à la sélection" if not path.is_file() else f"#{image['item_id']}{suffix}");item.setData(Qt.ItemDataRole.UserRole,image);thumbnails.addItem(item)
            loaded=end;load_more.setVisible(loaded<len(values))
            if thumbnails.count() and thumbnails.currentRow()<0:thumbnails.setCurrentRow(0)
        mode.currentIndexChanged.connect(lambda:populate(True));load_more.clicked.connect(populate)
        def selected(item,_old=None):
            if not item:return
            value=item.data(Qt.ItemDataRole.UserRole);path=Path(value["path"])
            if not path.is_file() and pixel_resolver:
                try:path=Path(pixel_resolver(int(value["item_id"])));value["path"]=str(path)
                except Exception as exc:self.details.setPlainText(f"Image distante actuellement indisponible : {exc}");return  # noqa: BLE001
            preview.set_image(path);item.setIcon(QIcon(str(path)));item.setText(item.text().replace("\nChargement à la sélection", ""));self._show_provenances(value)
        thumbnails.currentItemChanged.connect(selected);thumbnails.itemDoubleClicked.connect(lambda item:self._open_entry(item.data(Qt.ItemDataRole.UserRole)))
        populate()

    def _show_provenances(self,entry:dict)->None:
        lines=["<b>Sources</b>"]
        for row in entry.get("provenances",[]):
            if row.get("local_path"):
                path=str(row["local_path"]);state="" if Path(path).is_file() else " — fichier introuvable";lines.append(f"Local : {path}{state}")
            elif row.get("site") and row.get("post_id"):
                url=post_page_url(str(row["site"]),str(row["post_id"]));lines.append(f'<a href="{url}">{row["site"].title()} #{row["post_id"]}</a>')
        self.details.setHtml("<br>".join(lines))

    @staticmethod
    def _open_entry(entry:dict)->None:
        for row in entry.get("provenances",[]):
            if row.get("local_path") and Path(str(row["local_path"])).is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(row["local_path"])));return
        for row in entry.get("provenances",[]):
            if row.get("site") and row.get("post_id"):
                QDesktopServices.openUrl(QUrl(post_page_url(str(row["site"]),str(row["post_id"]))));return


def QtCoreSize(width:int,height:int):
    from PySide6.QtCore import QSize
    return QSize(width,height)
