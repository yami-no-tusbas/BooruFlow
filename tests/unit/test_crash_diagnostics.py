from __future__ import annotations

import json
import os
import subprocess
import sys

from booruflow.infrastructure.crash_diagnostics import CrashDiagnostics


def test_uncaught_exception_detail_and_clean_session_lifecycle(tmp_path):
    diagnostics=CrashDiagnostics(tmp_path); logs=[]; diagnostics.set_logger(logs.append)
    marker=diagnostics.marker
    try:
        raise RuntimeError("diagnostic sentinel")
    except RuntimeError:
        diagnostics.record_exception(*sys.exc_info(),thread="test-thread")
    diagnostics.close(clean=True)
    text=(tmp_path/"var"/"logs"/"booruflow-fatal.log").read_text(encoding="utf-8")
    assert "RuntimeError" in text and "diagnostic sentinel" in text and "test-thread" in text
    assert any("[ERROR] [Crash]" in line for line in logs) and not marker.exists()

def test_previous_unclean_session_is_reported_on_next_launch(tmp_path):
    state=tmp_path/"var"/"state"; state.mkdir(parents=True)
    (state/"booruflow-session-2147483647.json").write_text(json.dumps({"pid":2147483647,"started_at":"earlier"}),encoding="utf-8")
    diagnostics=CrashDiagnostics(tmp_path); logs=[]; diagnostics.set_logger(logs.append); diagnostics.close(clean=True)
    assert any("ended abnormally" in line and "2147483647" in line for line in logs)

def test_global_sys_exception_hook_uses_existing_log_callback(tmp_path):
    diagnostics=CrashDiagnostics(tmp_path); logs=[]; diagnostics.set_logger(logs.append)
    assert sys.excepthook==diagnostics._sys_exception
    try:
        raise ValueError("slot failure sentinel")
    except ValueError:
        details=sys.exc_info()
    diagnostics._sys_exception(*details); diagnostics.close(clean=True)
    assert any("[ERROR] [Crash]" in line and "slot failure sentinel" in line for line in logs)

def test_faulthandler_persists_native_abort_trace(tmp_path):
    code=("from pathlib import Path; import os; "
          "from booruflow.infrastructure.crash_diagnostics import CrashDiagnostics; "
          "CrashDiagnostics(Path(os.environ['BOORUFLOW_TEST_ROOT'])); os.abort()")
    environment=dict(os.environ); environment["BOORUFLOW_TEST_ROOT"]=str(tmp_path)
    completed=subprocess.run([sys.executable,"-c",code],env=environment,capture_output=True,text=True,timeout=10,check=False)
    fatal=tmp_path/"var"/"logs"/"booruflow-fatal.log"
    assert completed.returncode!=0 and fatal.is_file()
    assert "Fatal Python error" in fatal.read_text(encoding="utf-8",errors="replace")

def test_faulthandler_captures_qthread_destroyed_while_running(tmp_path):
    code=r'''
import os, time
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QThread, QTimer, Signal
from booruflow.infrastructure.crash_diagnostics import CrashDiagnostics
diagnostics=CrashDiagnostics(Path(os.environ["BOORUFLOW_TEST_ROOT"])); diagnostics.install_qt_message_handler()
class Worker(QThread):
    completed=Signal()
    def run(self):
        self.completed.emit(); time.sleep(0.4)
app=QCoreApplication([]); holder={"worker":Worker()}
def dispose_too_early():
    holder["worker"].deleteLater(); holder["worker"]=None
holder["worker"].completed.connect(dispose_too_early); holder["worker"].start()
QTimer.singleShot(2000,app.quit); app.exec()
'''
    environment=dict(os.environ); environment["QT_QPA_PLATFORM"]="offscreen"; environment["BOORUFLOW_TEST_ROOT"]=str(tmp_path)
    completed=subprocess.run([sys.executable,"-c",code],env=environment,capture_output=True,text=True,timeout=10,check=False)
    text=(tmp_path/"var"/"logs"/"booruflow-fatal.log").read_text(encoding="utf-8",errors="replace")
    assert completed.returncode!=0
    assert "QtFatalMsg" in text and "QThread" in text and "still running" in text
