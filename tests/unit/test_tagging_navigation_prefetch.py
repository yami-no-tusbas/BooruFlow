"""Individual navigation uses the normal queue, cache and one worker."""
import io
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStatusBar

from booruflow.domain.image_analysis import AnalysisState, InputKind, ModelIdentity
from booruflow.infrastructure.image_sources import GelbooruPostProvider
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.wd14 import WD14Result, WD14Tag
from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage, ScaledImageLabel
from booruflow.presentation.pyside6.status_bar import StatusBarController
from booruflow.presentation.pyside6.tagging_controller import TaggingController
from booruflow.presentation.pyside6.tagging_page import TaggingPage
from booruflow.worker.image_analysis import ImageAnalysisWorker


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(output, format="PNG")
    return output.getvalue()


def wait_for(app, condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(2)
    assert condition()


@pytest.fixture
def navigation(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources/i18n", "en")
    analysis_page = ImageAnalysisPage(catalog)
    analysis = ImageAnalysisController(
        tmp_path, sys.executable, analysis_page, {}, dict, lambda *_: None,
        auto_start_worker=False,
    )
    analysis.timer.stop()
    analysis._start_source_preparation = Mock()
    analysis.ensure_worker_available = Mock()
    page = TaggingPage(catalog, {"tagging_hide_queued_results": False})
    controller = TaggingController(catalog, page, dict, lambda *_args, **_kwargs: None)
    controller.bind_image_analysis(analysis)
    bar = QStatusBar()
    footer = StatusBarController(bar, catalog, lambda *_: None)
    footer._memory_timer.stop()
    footer.bind(page.page_status)
    footer.activate_page("tagging")
    page.resize(1200, 850)
    page.show()

    class Backend:
        identity = ModelIdentity("wd14", "fake-model", "v1", "config1", "cpu")
        calls = 0

        def analyze(self, path):
            self.calls += 1
            with Image.open(path) as image:
                name = "red_tag" if image.getpixel((0, 0))[0] == 255 else "blue_tag"
            return WD14Result((WD14Tag(name, "general", .9),))

    backend = Backend()
    worker = ImageAnalysisWorker(analysis.repository, [backend])

    def show(count=4):
        page.show_results([
            {"id": i, "tags": f"tag_{count-i}", "tag_count": 1, "priority": "low"}
            for i in range(1, count + 1)
        ])

    def resolve(post_id, color="red"):
        repo = analysis.repository
        item = repo.item_by_remote_source("gelbooru", str(post_id))
        item_id = item.id if item else repo.add_unresolved_remote(
            InputKind.GELBOORU_POST, str(post_id), priority=0,
        )
        analysis.sources.bytes_fetcher = lambda *_: png(color)
        analysis.sources.resolve_post_data(
            item_id, GelbooruPostProvider(json_fetcher=lambda *_: []),
            {"id": post_id, "file_url": f"https://test.invalid/{post_id}.png", "tags": "solo"},
            str(post_id),
        )
        analysis.sources_changed.emit()
        app.processEvents()
        return repo.item_by_remote_source("gelbooru", str(post_id)).id

    def open_post(post_id):
        page.result_buttons[post_id].open_requested.emit(page.result_buttons[post_id].post)
        app.processEvents()

    yield SimpleNamespace(
        app=app, page=page, controller=controller, analysis=analysis, footer=footer,
        worker=worker, backend=backend, show=show, resolve=resolve, open=open_post,
    )
    controller.shutdown()
    page.preview._decode_pool.waitForDone(3000)
    QThreadPool.globalInstance().waitForDone(3000)
    app.processEvents()
    worker.close()
    analysis.shutdown()
    page.close()
    analysis_page.close()
    bar.close()
    page.deleteLater()
    analysis_page.deleteLater()


def test_position_uses_real_sequence_and_existing_footer(navigation):
    n = navigation
    n.show(107)
    for post_id, expected in ((1, "1 / 107"), (50, "50 / 107")):
        n.open(post_id)
        assert expected in n.footer.page_label.text()
    n.page.next_button.click()
    assert "51 / 107" in n.footer.page_label.text()
    n.page.previous_button.click()
    assert "50 / 107" in n.footer.page_label.text()
    n.open(107)
    assert "107 / 107" in n.footer.page_label.text()
    n.page.next_button.click()  # Existing cyclic boundary semantics.
    assert "1 / 107" in n.footer.page_label.text()
    n.page.set_result_sort("tags")
    index = next(i for i, post in enumerate(n.page.result_posts) if post["id"] == 1)
    assert f"{index+1} / 107" in n.footer.page_label.text()
    n.page.next_button.click()
    assert n.page.current_post_id == n.page.result_posts[(index + 1) % 107]["id"]
    n.page._remove_result_cards({50})
    assert "/ 106" in n.footer.page_label.text()
    n.page.show_search()
    assert " / " not in n.footer.page_label.text()


def test_prefetch_finishes_image_and_wd14_before_next(navigation):
    n = navigation
    n.show()
    n.open(1)
    repo = n.analysis.repository
    assert repo.item_by_remote_source("gelbooru", "2") is not None
    assert repo.item_by_remote_source("gelbooru", "3") is None
    n.resolve(1)
    assert n.worker.process_one()
    n.analysis._handle_worker_line("[DEBUG] [Worker] [item:1] Item ready")
    second_id = n.resolve(2, "blue")
    assert n.worker.process_one()
    assert n.backend.calls == 2
    n.controller._prepare_next_review()
    wait_for(n.app, lambda: not n.page.preview._decoding)
    item = repo.get_item(second_id)
    data = n.controller._preview_data(item)
    image = n.page.preview._decoded_cache.get(n.page.preview._image_key(data))
    assert image is not None
    with patch.object(n.analysis.sources, "cached_bytes", wraps=n.analysis.sources.cached_bytes) as read:
        n.page.next_button.click()
        assert n.page.current_post_id == 2
        assert n.page.preview._source.toImage().pixelColor(0, 0).name() == "#0000ff"
        assert "blue_tag" in [n.page.suggestions.item(i, 0).text()
                              for i in range(n.page.suggestions.rowCount())]
        read.assert_not_called()
    assert n.backend.calls == 2
    n.app.processEvents()
    assert repo.item_by_remote_source("gelbooru", "3") is not None
    assert repo.item_by_remote_source("gelbooru", "4") is None
    n.page.previous_button.click()
    assert n.page.current_post_id == 1
    assert n.backend.calls == 2


def test_next_attaches_to_processing_job_and_late_result_never_changes_other_post(navigation):
    n = navigation
    n.show()
    n.open(1)
    repo = n.analysis.repository
    n.resolve(1)
    n.worker.process_one()
    second_id = n.resolve(2, "blue")
    claimed = repo.claim_next(2)
    assert claimed.id == second_id
    n.page.next_button.click()
    n.app.processEvents()
    assert repo.get_item(second_id).state is AnalysisState.PROCESSING
    assert repo.claim_next(2) is None  # N+2 is unresolved; N+1 cannot be claimed twice.
    assert repo.connection.execute(
        "SELECT attempt_count,priority FROM analysis_items WHERE id=?", (second_id,),
    ).fetchone()[:] == (1, 200)
    n.open(4)
    repo.transition(second_id, AnalysisState.READY_FOR_REVIEW)
    n.analysis._handle_worker_line(f"[DEBUG] [Worker] [item:{second_id}] Item ready")
    assert n.page.current_post_id == n.page._displayed_post_id == 4
    assert "blue_tag" not in [n.page.suggestions.item(i, 0).text()
                              for i in range(n.page.suggestions.rowCount())]


def test_ready_cache_hit_and_error_fallback_do_not_start_duplicate_analysis(navigation):
    n = navigation
    n.show()
    second_id = n.resolve(2, "blue")
    n.worker.process_one()
    calls = n.backend.calls
    n.open(1)
    assert n.analysis.repository.get_item(second_id).state is AnalysisState.READY_FOR_REVIEW
    n.page.next_button.click()
    assert n.backend.calls == calls
    n.app.processEvents()
    third = n.analysis.repository.item_by_remote_source("gelbooru", "3")
    n.analysis.repository.fail_source(third.id, "test prefetch failure")
    n.page.next_button.click()
    assert n.page.current_post_id == 3
    assert "test prefetch failure" in n.page.analysis_state.text()
    assert n.backend.calls == calls


def test_query_change_withdraws_only_owned_pending_prefetch(navigation):
    n = navigation
    n.show()
    n.open(1)
    second = n.analysis.repository.item_by_remote_source("gelbooru", "2")
    n.page.set_running(True)
    n.app.processEvents()
    row = n.analysis.repository.connection.execute(
        "SELECT analysis_requested FROM analysis_items WHERE id=?", (second.id,),
    ).fetchone()
    assert row[0] == 0
    assert " / " not in n.footer.page_label.text()
    assert n.controller._navigation_prefetch is None


def test_decoded_preview_inflight_is_reused_and_cache_is_bounded():
    app = QApplication.instance() or QApplication([])
    preview = ScaledImageLabel()
    preview.enable_preparation()
    red, blue = png("red"), png("blue")
    # Hold the task in a fake pool, then deliver exactly its prepared result.
    tasks = []
    with patch.object(preview._decode_pool, "start", side_effect=tasks.append):
        preview.prepare_image_bytes(red)
        preview.prepare_image_bytes(red)
        preview.set_image_bytes(red)
        assert len(tasks) == 1
        tasks[0].run()
        app.processEvents()
        assert preview._source.toImage().pixelColor(0, 0).name() == "#ff0000"
        preview.prepare_image_bytes(blue)
        preview.set_image_bytes(png("green"))
        tasks[1].run()
        app.processEvents()
        assert preview._source.toImage().pixelColor(0, 0).name() == "#008000"
    for color in ("yellow", "pink", "purple", "black"):
        preview.set_image_bytes(png(color))
    assert len(preview._decoded_cache) == 3
    preview.cancel_preparation()
    preview.close()


@pytest.mark.parametrize("hide_queued", [False, True])
def test_space_uses_prepared_next_image_after_local_validation(navigation, hide_queued):
    n = navigation
    n.show()
    n.page.hide_queued_results.setChecked(hide_queued)
    n.open(1)
    n.resolve(1)
    n.worker.process_one()
    n.resolve(2, "blue")
    n.worker.process_one()
    n.analysis._handle_worker_line("[DEBUG] [Worker] [item:1] Item ready")
    wait_for(n.app, lambda: not n.page.preview._decoding)
    calls = n.backend.calls
    QTest.keyClick(n.page.suggestions, Qt.Key.Key_Space)
    assert n.page.current_post_id == 2
    assert n.backend.calls == calls
    assert n.page.preview._source.toImage().pixelColor(0, 0).name() == "#0000ff"


def test_current_request_precedes_explicit_then_prefetch_and_cancellation_is_safe(navigation):
    n = navigation
    n.show()
    repo = n.analysis.repository
    n.open(1)
    first = n.resolve(1)
    second = n.resolve(2, "blue")
    n.open(4)
    fourth = n.resolve(4, "green")
    # The previous speculative request is withdrawn, but the former current
    # request remains explicit. The new current wins even though it is newer.
    assert repo.claim_next(1).id == fourth
    assert repo.claim_next(1).id == first
    assert repo.claim_next(1) is None
    assert repo.get_item(second).state is AnalysisState.PENDING
    n.open(2)
    repo.cancel_navigation_prefetch(second)
    assert repo.claim_next(1).id == second


def test_wd14_inference_gate_serializes_existing_processes():
    from booruflow.infrastructure.onnx_runtime import serialized_wd14_execution

    code = (
        "from booruflow.infrastructure.onnx_runtime import serialized_wd14_execution\n"
        "print('waiting', flush=True)\n"
        "with serialized_wd14_execution():\n"
        " print('acquired', flush=True)\n"
    )
    process = None
    try:
        with serialized_wd14_execution():
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", code], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            assert process.stdout.readline().strip() == "waiting"
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=.15)
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
        assert "acquired" in output
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
