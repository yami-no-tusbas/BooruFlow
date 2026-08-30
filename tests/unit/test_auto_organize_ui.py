from __future__ import annotations

import os
import time
import weakref
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QThread, Signal
from PySide6.QtWidgets import QApplication, QMainWindow

from booruflow.domain.auto_organize import FilePlan, OrganizeMode, PlanStatus, PostMetadata
from booruflow.infrastructure.post_metadata_cache import PostMetadataCache
from booruflow.presentation.pyside6.auto_organize_controller import (
    AnalyzeWorker,
    AutoOrganizeController,
)
from booruflow.presentation.pyside6.auto_organize_page import AutoOrganizePage

NAME="anonymous - 9490613 - sensitive - 0f58173673bf35ef9e0fa7966ea18761.jpg"

def app(): return QApplication.instance() or QApplication([])

def wait_until(predicate, timeout=10):
    qt=app(); deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        qt.processEvents()
        if predicate(): return True
        time.sleep(0.005)
    return False

def controller_for(tmp_path):
    page=AutoOrganizePage(None); resources=tmp_path/"resources"; resources.mkdir(exist_ok=True)
    default=Path(__file__).resolve().parents[2]/"resources"/"auto_organize_rules.json"
    (resources/"auto_organize_rules.json").write_text(default.read_text(encoding="utf-8"),encoding="utf-8")
    logs=[]; return page,AutoOrganizeController(tmp_path,page,logs.append),logs

def test_sqlite_connection_is_created_and_closed_inside_worker_thread(tmp_path):
    qt=app(); folder=tmp_path/"Tags (gelbooru)"; folder.mkdir(); (folder/NAME).write_bytes(b"x")
    cache_path=tmp_path/"cache.sqlite"; seed=PostMetadataCache(cache_path)
    seed.put(PostMetadata("gelbooru","9490613",(),artists=("known",),rating="sensitive",md5="0f58173673bf35ef9e0fa7966ea18761")); seed.close()
    received=[]; updates=[]; worker=AnalyzeWorker(cache_path,lambda *_:None,(),tmp_path/"out",(folder,),OrganizeMode.REFRESH_ONLY,True,True,False)
    worker.progress.connect(updates.append); worker.completed.connect(lambda plans,error:received.append((plans,error))); worker.start(); worker.wait(5000); qt.processEvents()
    assert received and received[0][1]=="" and received[0][0][0].fetch_state=="cache"
    assert updates[-1]["processed"]==1 and updates[-1]["cache_hits"]==1

def test_folder_drop_list_normalizes_deduplicates_and_ignores_files(tmp_path):
    page=AutoOrganizePage(None); folder=tmp_path/"folder"; folder.mkdir(); file=tmp_path/"x.jpg"; file.write_bytes(b"x")
    page.folders.add_paths((folder,folder/".."/"folder",file)); assert page.folders.count()==1
    assert Path(page.folders.item(0).text())==folder.resolve()

def test_priority_change_invalidates_existing_plan(tmp_path):
    page=AutoOrganizePage(None); resources=tmp_path/"resources"; resources.mkdir()
    default=Path(__file__).resolve().parents[2]/"resources"/"auto_organize_rules.json"
    (resources/"auto_organize_rules.json").write_text(default.read_text(encoding="utf-8"),encoding="utf-8")
    controller=AutoOrganizeController(tmp_path,page,lambda _message:None); controller.plans=[object()]
    page._plans=[object()]; page._rules_modified()
    assert controller.plans==[] and page._plans==[] and not page.execute_button.isEnabled()

def test_cancel_during_remote_fetch_closes_sqlite_and_returns_no_executable_plan(tmp_path):
    qt=app(); folder=tmp_path/"Tags (gelbooru)"; folder.mkdir(); source=folder/NAME; source.write_bytes(b"x"); holder={}
    def fetch(_site,_post_id):
        holder["worker"].request_cancel()
        return PostMetadata("gelbooru","9490613",(),rating="sensitive",md5="0f58173673bf35ef9e0fa7966ea18761")
    cache_path=tmp_path/"cancel.sqlite"; received=[]
    worker=AnalyzeWorker(cache_path,fetch,(),tmp_path/"out",(folder,),OrganizeMode.REFRESH_ONLY,True,False,False); holder["worker"]=worker
    worker.completed.connect(lambda plans,error:received.append((plans,error))); worker.start(); worker.wait(5000); qt.processEvents()
    assert received and received[0][1]=="cancelled" and source.exists()
    moved=tmp_path/"closed.sqlite"; cache_path.rename(moved); moved.rename(cache_path)

def test_cancelled_result_restores_ui_and_never_enables_execution(tmp_path):
    page=AutoOrganizePage(None); resources=tmp_path/"resources"; resources.mkdir(); default=Path(__file__).resolve().parents[2]/"resources"/"auto_organize_rules.json"
    (resources/"auto_organize_rules.json").write_text(default.read_text(encoding="utf-8"),encoding="utf-8")
    controller=AutoOrganizeController(tmp_path,page,lambda _message:None)
    controller.worker=AnalyzeWorker(tmp_path/"x.sqlite",lambda *_:None,(),tmp_path,(),OrganizeMode.REFRESH_ONLY,True,True,False)
    partial=FilePlan(tmp_path/"image.jpg",status=PlanStatus.RENAME); controller._analyzed([partial],"cancelled")
    assert controller.plans==[] and partial.status is PlanStatus.IGNORED
    assert page.state.text()=="Analyse annulée" and page.analyze_button.isEnabled() and not page.execute_button.isEnabled()

def test_controller_passes_configured_gelbooru_credentials(tmp_path):
    page=AutoOrganizePage(None); resources=tmp_path/"resources"; resources.mkdir(); default=Path(__file__).resolve().parents[2]/"resources"/"auto_organize_rules.json"
    (resources/"auto_organize_rules.json").write_text(default.read_text(encoding="utf-8"),encoding="utf-8")
    controller=AutoOrganizeController(tmp_path,page,lambda _message:None,
        credential_provider=lambda:{"gelbooru":{"user_id":"7","api_key":"secret"}})
    metadata=PostMetadata("gelbooru","9",())
    with patch("booruflow.presentation.pyside6.auto_organize_controller.fetch_post",return_value=metadata) as fetch:
        assert controller.fetcher("gelbooru","9") is metadata
    fetch.assert_called_once_with("gelbooru","9","7","secret")

def test_visible_errors_are_grouped_and_copyable():
    page=AutoOrganizePage(None); detail={"signature":"same","site":"gelbooru","post_id":"9","stage":"remote_fetch","status":401,"exception_type":"HTTPError","message":"Unauthorized","endpoint":"https://gelbooru.com/index.php?page=dapi"}
    page.record_error(detail); page.record_error(detail)
    assert "[2] gelbooru" in page.error_summary.toPlainText() and "HTTP 401" in page.last_error.text()

def test_large_result_batch_avoids_content_resize_and_remains_available(tmp_path):
    page=AutoOrganizePage(None); page.table.resizeColumnsToContents=MagicMock()
    plans=[FilePlan(tmp_path/f"image-{index}.jpg",site="gelbooru",post_id=str(index),
        destination=tmp_path/"out"/f"image-{index}.jpg",winner_path=("Tags","Races","elf"),
        status=PlanStatus.MOVE) for index in range(366)]
    timings=page.show_plans(plans)
    assert page.table.rowCount()==366 and len(page._plans)==366
    assert not page.table.resizeColumnsToContents.called
    assert timings["total_ms"]>=0

def test_controller_sends_each_structured_error_to_existing_log(tmp_path):
    page=AutoOrganizePage(None); resources=tmp_path/"resources"; resources.mkdir(); default=Path(__file__).resolve().parents[2]/"resources"/"auto_organize_rules.json"
    (resources/"auto_organize_rules.json").write_text(default.read_text(encoding="utf-8"),encoding="utf-8"); logs=[]
    controller=AutoOrganizeController(tmp_path,page,logs.append); detail={"signature":"401","file":"image.jpg","site":"gelbooru","post_id":"9490613","stage":"remote_fetch","status":401,"exception_type":"HTTPError","message":"Unauthorized","endpoint":"https://gelbooru.com/index.php?page=dapi","attempt":1}
    controller._on_error_detail(detail)
    assert "[ERROR] [AutoOrganize]" in logs[-1] and "HTTP 401" in logs[-1] and "post 9490613" in logs[-1]

def test_auto_organize_error_log_redacts_credentials(tmp_path):
    page=AutoOrganizePage(None); resources=tmp_path/"resources"; resources.mkdir(); default=Path(__file__).resolve().parents[2]/"resources"/"auto_organize_rules.json"
    (resources/"auto_organize_rules.json").write_text(default.read_text(encoding="utf-8"),encoding="utf-8"); logs=[]
    controller=AutoOrganizeController(tmp_path,page,logs.append)
    controller._on_error_detail({"file":"x.jpg","site":"gelbooru","post_id":"9","stage":"remote_fetch",
        "status":404,"exception_type":"PostNotFoundError","message":"api_key=secret user_id=7 Post introuvable",
        "endpoint":"https://gelbooru.com/index.php?page=dapi","attempt":1})
    assert "secret" not in logs[-1] and "user_id=7" not in logs[-1]
    assert "api_key=<redacted>" in logs[-1] and "Post introuvable" in logs[-1]

def test_priority_tree_filter_keeps_matching_leaf_and_ancestors(tmp_path):
    page,controller,_logs=controller_for(tmp_path)
    page.rule_filter.setText("cat_ears"); app().processEvents()
    visible=[]
    def visit(item):
        if not item.isHidden(): visible.append(item.text(0))
        for index in range(item.childCount()): visit(item.child(index))
    for index in range(page.priority_tree.topLevelItemCount()): visit(page.priority_tree.topLevelItem(index))
    assert "Tags" in visible and "Animal Ears" in visible and "cat_ears" in visible
    assert "Weapons" not in visible
    assert "Feuilles Tags: 476" in page.rules_inventory.text()
    controller.shutdown()

def test_plan_details_separate_route_winner_fallback_and_destination(tmp_path):
    page=AutoOrganizePage(None); plan=FilePlan(tmp_path/"image.jpg",route="Tags C&L",
        winner="bikini",winner_path=("Tags","Styles vestimentaires","Swimsuits","bikini"),
        candidates=("Tags / Styles vestimentaires / Swimsuits / bikini",),fallback="",
        destination=tmp_path/"Tags C&L (gelbooru)"/"Styles vestimentaires"/"Swimsuits"/"bikini"/"image.jpg",
        status=PlanStatus.MOVE)
    page.show_plans([plan]); page.table.selectRow(0); app().processEvents(); detail=page.details.toPlainText()
    assert "Route:\nTags C&L" in detail and "Winner:" in detail and "Fallback:\naucun" in detail
    assert "Destination:" in detail and "Swimsuits" in detail

def test_qt_complete_analysis_366_results_waits_for_thread_finished_before_disposal(tmp_path):
    page,controller,logs=controller_for(tmp_path); folder=tmp_path/"batch (gelbooru)"; folder.mkdir()
    md5="0f58173673bf35ef9e0fa7966ea18761"
    for index in range(366): (folder/f"anonymous - {1000000+index} - sensitive - {md5}.jpg").write_bytes(b"x")
    controller.fetcher=lambda site,post_id:PostMetadata(site,post_id,("elf",),artists=("anonymous",),rating="sensitive",md5=md5)
    controller.analyze((folder,),"refresh_only",True,False,False,""); worker=controller.worker; reference=weakref.ref(worker)
    assert wait_until(lambda:controller.worker is None,15)
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete); app().processEvents()
    assert page.table.rowCount()==366 and page.analyze_button.isEnabled()
    assert reference() is None or not shiboken6.isValid(reference())
    joined="\n".join(logs); assert "Results received by UI count=366" in joined
    assert joined.index("QThread finished")<joined.index("Worker cleanup requested")

def test_results_signal_does_not_dispose_a_thread_still_finishing(tmp_path):
    class TailWorker(QThread):
        completed=Signal(list,str)
        def __init__(self): super().__init__(); self.result_plans=[]; self.result_error=""
        def run(self): self.completed.emit([],""); time.sleep(0.15)
    _page,controller,_logs=controller_for(tmp_path); worker=TailWorker(); controller.worker=worker
    worker.completed.connect(controller._results_received); worker.finished.connect(controller._analysis_thread_finished); worker.start()
    assert wait_until(lambda:controller._pending_analysis_result is not None)
    assert controller.worker is worker and worker.isRunning() and shiboken6.isValid(worker)
    assert wait_until(lambda:controller.worker is None)

def test_cancel_error_and_close_paths_leave_no_analysis_thread(tmp_path):
    page,controller,_logs=controller_for(tmp_path); folder=tmp_path/"cancel (gelbooru)"; folder.mkdir(); md5="0f58173673bf35ef9e0fa7966ea18761"
    for index in range(40): (folder/f"anonymous - {index+1} - sensitive - {md5}.jpg").write_bytes(b"x")
    def fetch(site,post_id):
        time.sleep(0.01); return PostMetadata(site,post_id,(),rating="sensitive",md5=md5)
    controller.fetcher=fetch; controller.analyze((folder,),"refresh_only",True,False,False,"")
    assert wait_until(lambda:controller.worker is not None and controller.worker.isRunning())
    controller.stop(); assert wait_until(lambda:controller.worker is None)
    assert page.state.text()=="Analyse annulée" and not page.execute_button.isEnabled()
    with patch("booruflow.presentation.pyside6.auto_organize_controller.AutoOrganizer.plan",side_effect=RuntimeError("synthetic failure")):
        controller.analyze((folder,),"refresh_only",True,False,False,""); assert wait_until(lambda:controller.worker is None)
    assert "synthetic failure" in page.state.text()
    controller.fetcher=fetch; controller.analyze((folder,),"refresh_only",True,False,False,"")
    assert controller.shutdown() is True and controller.worker is None

def test_close_immediately_after_results_waits_for_tail_then_cleans(tmp_path):
    class TailWorker(AnalyzeWorker):
        completed=Signal(list,str)
        def __init__(self): QThread.__init__(self); self.result_plans=[]; self.result_error=""; self.cancel_event=__import__("threading").Event()
        def request_cancel(self): self.cancel_event.set()
        def run(self): self.completed.emit([],""); time.sleep(0.1)
    _page,controller,_logs=controller_for(tmp_path); worker=TailWorker(); controller.worker=worker
    worker.completed.connect(controller._results_received); worker.finished.connect(controller._analysis_thread_finished); worker.start()
    assert wait_until(lambda:controller._pending_analysis_result is not None)
    assert controller.shutdown() is True and controller.worker is None and not worker.isRunning()

def test_window_close_during_analysis_waits_for_cooperative_cleanup(tmp_path):
    page,controller,_logs=controller_for(tmp_path); folder=tmp_path/"window-close (gelbooru)"; folder.mkdir(); md5="0f58173673bf35ef9e0fa7966ea18761"
    for index in range(20): (folder/f"anonymous - {index+1} - sensitive - {md5}.jpg").write_bytes(b"x")
    def fetch(site,post_id):
        time.sleep(0.02); return PostMetadata(site,post_id,(),rating="sensitive",md5=md5)
    class Host(QMainWindow):
        def closeEvent(self,event):
            if controller.shutdown(): super().closeEvent(event)
            else: event.ignore()
    host=Host(); host.setCentralWidget(page); host.show(); controller.fetcher=fetch
    controller.analyze((folder,),"refresh_only",True,False,False,"")
    assert wait_until(lambda:controller.worker is not None and controller.worker.isRunning())
    host.close(); app().processEvents()
    assert not host.isVisible() and controller.worker is None
