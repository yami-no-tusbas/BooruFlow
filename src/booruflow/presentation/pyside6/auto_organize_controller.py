"""QThread orchestration and persisted priorities for auto organization."""
from __future__ import annotations

import os
import re
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox

from booruflow.application.auto_organize import (
    AnalysisCancelled,
    AutoOrganizer,
    SystemicApiError,
    apply_plans,
    load_rules,
    rule_inventory,
    rule_node_to_dict,
    validate_batch,
    validation_summary,
)
from booruflow.domain.auto_organize import OrganizeMode, PlanStatus, RuleEngine
from booruflow.infrastructure.post_metadata_cache import PostMetadataCache
from booruflow.infrastructure.post_metadata_client import fetch_post
from booruflow.infrastructure.settings.json_repository import JsonSettingsRepository
from booruflow.infrastructure.tag_category_lookup import LocalTagCategoryLookup
from booruflow.presentation.pyside6.ui_logging import log_event

_SENSITIVE_LOG_VALUE = re.compile(
    r"(?i)\b(api_key|user_id|password|token)(\s*[=:]\s*)([^\s&]+)"
)


def _safe_log_value(value: object) -> str:
    return _SENSITIVE_LOG_VALUE.sub(r"\1\2<redacted>", str(value))


class AnalyzeWorker(QThread):
    completed=Signal(list,str)
    progress=Signal(dict)
    error_detail=Signal(dict)
    lifecycle=Signal(str)
    diagnostic=Signal(str)
    def __init__(self,cache_path,fetcher,rules,destination,roots,mode,recursive,use_cache,force_refresh):
        super().__init__(); self.cache_path=cache_path; self.fetcher=fetcher; self.rules=rules; self.destination=destination
        self.roots=roots; self.mode=mode; self.recursive=recursive; self.use_cache=use_cache; self.force_refresh=force_refresh
        self.cancel_event=Event(); self.result_plans=[]; self.result_error=""
    def _trace(self,message):
        self.lifecycle.emit(f"{message} pid={os.getpid()} thread_id={threading.get_ident()} native_thread_id={threading.get_native_id()}")
    def request_cancel(self):
        self.cancel_event.set(); self.requestInterruption()
    def run(self):
        cache=None; plans=[]; error=""; started=time.perf_counter(); self._trace("worker run entered")
        try:
            self._trace("cache open requested")
            cache=PostMetadataCache(self.cache_path)
            self._trace("cache opened; scan started")
            organizer=AutoOrganizer(cache,self.fetcher,RuleEngine(self.rules),self.destination,self.error_detail.emit)
            plans=organizer.plan(self.roots,self.mode,self.recursive,use_cache=self.use_cache,
                force_refresh=self.force_refresh,cancel_check=self.cancel_event.is_set,
                progress=self.progress.emit)
            self._trace(f"analysis computation finished results={len(plans)} elapsed_ms={(time.perf_counter()-started)*1000:.1f}")
            if self.cancel_event.is_set(): error="cancelled"
            else:
                self._trace("batch validation started")
                validate_batch(plans)
                self._trace("batch validation finished")
                if self.cancel_event.is_set(): error="cancelled"
        except AnalysisCancelled as exc:
            plans, error=exc.plans,"cancelled"
        except SystemicApiError as exc:
            plans, error=exc.plans,"systemic"
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to UI
            plans, error=[],str(exc); self.diagnostic.emit(traceback.format_exc())
        finally:
            if cache is not None:
                self._trace("cache close requested")
                try: cache.close()
                except Exception:  # noqa: BLE001 - cleanup must not abort QThread teardown
                    self.diagnostic.emit(traceback.format_exc())
                self._trace("cache closed")
        self.result_plans=list(plans); self.result_error=error
        self._trace(f"emitting results count={len(plans)} state={error or 'complete'}")
        self.completed.emit(self.result_plans,error)
        self._trace("worker run returning")

class ExecuteWorker(QThread):
    completed=Signal(dict)
    def __init__(self,plans): super().__init__(); self.plans=plans; self.result={}
    def run(self): self.result=apply_plans(self.plans); self.completed.emit(self.result)

class AutoOrganizeController(QObject):
    def __init__(self,root:Path,page,log,parent=None,gelbooru_tag_database:Path|None=None,
                 credential_provider=None):
        super().__init__(parent); self.root=root; self.page=page; self.log=log; self.plans=[]; self.worker=None
        self._pending_analysis_result=None; self._pending_execution_result=None; self._progress_phase=""; self._closing=False
        self.cache_path=root/"var"/"cache"/"post_metadata.sqlite"; self.default_rules_path=root/"resources"/"auto_organize_rules.json"
        self.override_path=root/"config"/"auto_organize_rules_override.json"; self.override_repository=JsonSettingsRepository(self.override_path)
        lookup=LocalTagCategoryLookup(gelbooru_tag_database) if gelbooru_tag_database else None
        def enriched_fetch(site,post_id):
            credentials=credential_provider() if credential_provider else {}; gel=credentials.get("gelbooru",{}) if isinstance(credentials,dict) else {}
            gel=gel if isinstance(gel,dict) else {}
            metadata=fetch_post(site,post_id,str(gel.get("user_id","")),str(gel.get("api_key","")))
            if site!="gelbooru" or lookup is None: return metadata
            categories=lookup(metadata.tags); artists=tuple(tag for tag in metadata.tags if categories.get(tag)=="artist")
            return replace(metadata,categories=categories,artists=artists,
                copyrights=tuple(tag for tag in metadata.tags if categories.get(tag)=="copyright"),
                characters=tuple(tag for tag in metadata.tags if categories.get(tag)=="character"))
        self.fetcher=enriched_fetch; self.rules=self._load_rules(); self._show_rules()
        self.page.rules_save_requested.connect(self.save_rules); self.page.rules_reset_requested.connect(self.reset_rules)
        self.page.rules_changed.connect(self.invalidate_plan)
    def _load_rules(self): return load_rules(self.default_rules_path,self.override_path)
    def _show_rules(self):
        self.page.set_rules(self.rules); inventory=rule_inventory(self.rules); self.page.set_rule_inventory(inventory)
        counts=" · ".join(f"{name}={count}" for name,count in inventory["branches"].items())
        self.log(log_event("AutoOrganize",f"Canonical rules loaded: {counts}; sites gelbooru={inventory['gelbooru']} e621={inventory['e621']} shared={inventory['shared']}"))
    def analyze(self,roots,mode,recursive,use_cache,force_refresh,destination):
        if self.worker is not None:
            self.log(log_event("AutoOrganize","Analysis request ignored: worker cleanup is still active",level="WARNING")); return
        self.log(log_event("AutoOrganize",f"Analysis requested roots={len(roots)} mode={mode} recursive={recursive} pid={os.getpid()} thread_id={threading.get_ident()}"))
        target=Path(destination) if destination else self.root/"var"/"organized"; self.page.set_running(True)
        self.worker=AnalyzeWorker(self.cache_path,self.fetcher,self.rules,target,roots,OrganizeMode(mode),recursive,use_cache,force_refresh)
        self._pending_analysis_result=None; self._progress_phase=""
        self.worker.progress.connect(self._on_progress); self.worker.error_detail.connect(self._on_error_detail)
        self.worker.lifecycle.connect(self._worker_lifecycle)
        self.worker.diagnostic.connect(self._worker_diagnostic)
        self.worker.completed.connect(self._results_received); self.worker.started.connect(self._analysis_thread_started)
        self.worker.finished.connect(self._analysis_thread_finished)
        self.log(log_event("AutoOrganize","Worker created; thread start requested",level="DEBUG")); self.worker.start()
    def _analysis_thread_started(self):
        self.log(log_event("AutoOrganize",f"Worker thread started ui_thread_id={threading.get_ident()}",level="DEBUG"))
    def _worker_lifecycle(self,message):
        self.log(log_event("AutoOrganize",message,level="DEBUG"))
    def _worker_diagnostic(self,detail):
        self.log(log_event("AutoOrganize",detail,level="ERROR"))
    def _on_progress(self,values):
        self.page.set_analysis_progress(values); phase=str(values.get("phase", ""))
        if phase!=self._progress_phase:
            if phase=="scan": self.log(log_event("AutoOrganize","Scan started",level="DEBUG"))
            elif phase=="analyze": self.log(log_event("AutoOrganize",f"Scan finished count={values.get('total',0)}",level="DEBUG"))
            self._progress_phase=phase
        if phase=="analyze":
            processed=int(values.get("processed",0)); total=int(values.get("total",0))
            if processed in {1,total} or (processed and processed%25==0):
                self.log(log_event("AutoOrganize",f"Processing {processed}/{total}",level="DEBUG"))
    def _results_received(self,plans,error):
        self._pending_analysis_result=(list(plans),str(error))
        self.log(log_event("AutoOrganize",f"Results received by UI count={len(plans)} state={error or 'complete'}; waiting for QThread.finished"))
    def _analysis_thread_finished(self):
        worker=self.worker
        if worker is None: return
        self.log(log_event("AutoOrganize",f"QThread finished; run returned and quit is not required; isRunning={worker.isRunning()} ui_thread_id={threading.get_ident()}"))
        result=self._pending_analysis_result or (list(worker.result_plans),worker.result_error)
        self._pending_analysis_result=None
        self._analyzed(*result)
        self.log(log_event("AutoOrganize","Worker cleanup requested after QThread.finished",level="DEBUG"))
        worker.destroyed.connect(lambda *_:self.log(log_event("AutoOrganize","Worker disposed",level="DEBUG")))
        worker.deleteLater(); self.worker=None
    def stop(self):
        if isinstance(self.worker,AnalyzeWorker): self.worker.request_cancel(); self.page.set_stopping()
    def _analyzed(self,plans,error):
        started=time.perf_counter(); self.log(log_event("AutoOrganize",f"Analysis finished; results={len(plans)} state={error or 'complete'}"))
        self.page.set_running(False); self.plans=[] if error in {"cancelled","systemic"} else plans
        self.log(log_event("AutoOrganize","Preparing results..."))
        if error=="cancelled":
            for plan in plans: plan.status=PlanStatus.IGNORED; plan.message="Résultat partiel — analyse annulée"
            timings=self.page.show_plans(plans); self.page.execute_button.setEnabled(False); self.page.state.setText("Analyse annulée")
            self.log(log_event("AutoOrganize","Analyse annulée",level="WARNING"))
        elif error=="systemic":
            for plan in plans: plan.status=PlanStatus.IGNORED; plan.message="Résultat partiel — erreur API systématique"
            timings=self.page.show_plans(plans); self.page.execute_button.setEnabled(False)
            self.page.state.setText("Analyse interrompue : erreur API systématique")
            self.log(log_event("AutoOrganize","Analyse interrompue : erreur API systématique",level="ERROR"))
        elif error:
            timings={}; self.page.state.setText(f"Échec : {error}"); self.log(log_event("AutoOrganize",str(error),level="ERROR"))
        else:
            timings=self.page.show_plans(plans); summary=validation_summary(plans)
            labels={"analyzed":"Analysés","exact":"Identiques","divergences":"À modifier","ambiguous":"Ambigus","errors":"Erreurs","tag_matches":"Avec match Tags","by_tags":"Classés Tags","by_species":"Classés Species","by_copyright":"Classés Copyright","by_artist":"Classés Artist","routed_cl":"Routés C&L","routed_yl":"Routés Y&L"}
            self.page.state.setText(" — ".join(f"{labels.get(k,k)}: {v}" for k,v in summary.items()))
            self.page.execute_button.setEnabled(any(p.status in {PlanStatus.RENAME,PlanStatus.MOVE,PlanStatus.RENAME_MOVE} for p in plans))
        if timings:
            self.log(log_event("AutoOrganize",f"Table populated in {timings['populate_ms']:.1f} ms; final table update {timings['finalize_ms']:.1f} ms"))
        self.log(log_event("AutoOrganize",f"UI ready in {(time.perf_counter()-started)*1000:.1f} ms"))
    def _on_error_detail(self,detail):
        status=detail.get("status"); status_text=f"HTTP {status}" if status is not None else str(detail.get("exception_type", "Erreur"))
        headline=(f"{str(detail.get('site') or '?').capitalize()} post {detail.get('post_id') or '?'} — "
                  f"{status_text} {detail.get('message') or ''} — fichier: {detail.get('file') or '?'}")
        technical=" ".join((f"stage={detail.get('stage','')}",f"endpoint={detail.get('endpoint','')}",
            f"exception={detail.get('exception_type','')}",f"attempt={detail.get('attempt',1)}"))
        self.log(log_event("AutoOrganize",_safe_log_value(f"{headline} · {technical}"),level="ERROR"))
        self.page.record_error(detail)
    def invalidate_plan(self):
        if self.plans: self.plans=[]; self.page.clear_plan("Priorités modifiées : relancez l’analyse.")
    def save_rules(self):
        roots=self.page.rules(); self.override_repository.save({"version":2,"roots":[rule_node_to_dict(node) for node in roots]})
        self.rules=self._load_rules(); self._show_rules(); self.invalidate_plan(); self.page.rules_state.setText("Priorités enregistrées.")
    def reset_rules(self):
        self.override_repository.save({}); self.rules=self._load_rules(); self._show_rules()
        self.invalidate_plan(); self.page.rules_state.setText("Priorités par défaut restaurées.")
    def execute(self):
        if QMessageBox.question(self.page,"Confirmer les opérations","Appliquer uniquement les renommages/déplacements non ambigus de cet aperçu ?") != QMessageBox.StandardButton.Yes: return
        self.page.execute_button.setEnabled(False); self.page.state.setText("Exécution en cours…"); self.worker=ExecuteWorker(self.plans)
        self._pending_execution_result=None; self.worker.completed.connect(self._execution_result_received)
        self.worker.finished.connect(self._execution_thread_finished); self.worker.start()
    def _execution_result_received(self,result): self._pending_execution_result=dict(result)
    def _execution_thread_finished(self):
        worker=self.worker
        if not isinstance(worker,ExecuteWorker): return
        self._executed(self._pending_execution_result or worker.result)
        worker.deleteLater(); self.worker=None; self._pending_execution_result=None
    def _executed(self,result):
        self.page.show_plans(self.plans); self.page.state.setText(" — ".join(f"{k}: {v}" for k,v in result.items())); self.log(f"Rangement auto: {result}"); self.worker.deleteLater(); self.worker=None
    def shutdown(self):
        self._closing=True; worker=self.worker
        if isinstance(worker,AnalyzeWorker) and worker.isRunning():
            self.log(log_event("AutoOrganize","Window closing: cooperative cancellation requested",level="WARNING")); worker.request_cancel()
            if not worker.wait(12000):
                self.log(log_event("AutoOrganize","Window close deferred: analysis thread did not stop within 12 seconds",level="ERROR")); return False
        if isinstance(worker,AnalyzeWorker):
            self.log(log_event("AutoOrganize",f"Shutdown observed stopped thread isRunning={worker.isRunning()}",level="DEBUG"))
            worker.deleteLater(); self.worker=None; self._pending_analysis_result=None
        elif isinstance(worker,ExecuteWorker) and worker.isRunning():
            self.log(log_event("AutoOrganize","Window close deferred: filesystem operation is still active",level="WARNING")); return False
        return True
