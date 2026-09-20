from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from booruflow.application.targeted_wd14 import (
    TargetedWD14Analyzer,
    TargetedWD14Progress,
    TargetedWD14Result,
    confidence_bucket,
    resolve_wd14_target,
)
from booruflow.domain.image_analysis import AnalysisItem, InputKind, ModelIdentity, SourceReference
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.wd14 import WD14Config, WD14Result, WD14Tag, wd14_config_identity


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, "90-100"), (0.95, "90-100"), (0.90, "90-100"),
        (0.899, "80-89"), (0.80, "80-89"), (0.799, "70-79"),
        (0.10, "10-19"), (0.099, "under-10"),
    ],
)
def test_confidence_bucket_boundaries(score: float, expected: str) -> None:
    assert confidence_bucket(score) == expected


def test_resolve_wd14_target_rejects_unknown_tag(tmp_path: Path) -> None:
    (tmp_path / "selected_tags.csv").write_text(
        "name,category\n1girl,0\nsolo,0\n", encoding="utf-8"
    )
    assert resolve_wd14_target("1girl", tmp_path) == ("1girl",)
    assert resolve_wd14_target("unknown_tag", tmp_path) == ()


def test_targeted_analyzer_reuses_compatible_vector_and_reanalyzes_old_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "new-version", "config", "cpu")

    class FakeBackend:
        analyzed = 0

        def __init__(self, _config) -> None:
            self.identity = identity
            self.runtime = "test"; self.device = "cpu"

        def prepare(self) -> None:
            return

        def analyze(self, _path: Path) -> WD14Result:
            type(self).analyzed += 1
            return WD14Result((WD14Tag("1girl", "general", 0.81),))

        def close(self) -> None:
            return

    with ImageAnalysisRepository(database) as repository:
        item_ids = {}
        for post_id in ("1", "2"):
            image = tmp_path / f"{post_id}.jpg"; image.write_bytes(b"image")
            item_ids[post_id] = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=post_id),
                    cached_path=image, content_sha256=post_id * 64,
                ),
                request_analysis=False,
            )
        current = repository.begin_model_run(
            item_ids["1"], "wd14", "model", "new-version", "config"
        )
        repository.save_wd14_score_vector(current, {"1girl": 0.97})
        repository.complete_model_run(current)
        old = repository.begin_model_run(
            item_ids["2"], "wd14", "model", "old-version", "config"
        )
        repository.save_wd14_score_vector(old, {"1girl": 0.99})
        repository.complete_model_run(old)

    progress = []
    analyzer = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend,
    )
    result = analyzer.analyze(
        "1girl", ("1girl",), [{"id": 1}, {"id": 2}], progress.append
    )

    assert result.scores == {1: 0.97, 2: 0.81}
    assert FakeBackend.analyzed == 1
    assert result.progress.reused == 1
    assert result.progress.analyzed == 1
    assert result.progress.failed == 0
    assert result.progress.reused + result.progress.analyzed + result.progress.failed == 2
    assert progress[-1].completed == 2


def test_fully_cached_target_does_not_load_onnx_model(tmp_path: Path) -> None:
    model_id = "model"
    (tmp_path / "metadata.json").write_text(json.dumps({
        "model_id": model_id, "model_sha256": "a" * 64,
    }), encoding="utf-8")
    identity = wd14_config_identity(WD14Config(tmp_path, model_id, 0.0))
    database = tmp_path / "cached.sqlite"
    image = tmp_path / "cached.jpg"; image.write_bytes(b"image")
    with ImageAnalysisRepository(database) as repository:
        item_id = repository.add_item(AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="7"),
            cached_path=image, content_sha256="7" * 64,
        ), request_analysis=False)
        run_id = repository.begin_model_run(
            item_id, identity.backend, identity.name, identity.version,
            identity.configuration_hash,
        )
        repository.save_wd14_score_vector(run_id, {"1girl": 0.93})
        repository.complete_model_run(run_id)

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, model_id, None, {}, None,
    ).analyze("1girl", ("1girl",), [{"id": 7}])
    assert result.scores == {7: 0.93}
    assert result.progress.reused == 1
    assert result.progress.analyzed == 0


def test_targeted_groups_keep_selection_and_analyze_only_visible_results(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.tagging_page import TaggingPage

    app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    page = TaggingPage(catalog, {})
    page.show_results([
        {"id": 1, "tags": "a", "priority": "low", "tag_count": 1},
        {"id": 2, "tags": "b", "priority": "low", "tag_count": 1},
        {"id": 3, "tags": "c", "priority": "low", "tag_count": 1},
    ])
    page.set_batch_queue_entries([{
        "site": "gelbooru", "post_id": "3", "additions": ["x"], "removals": [],
        "publish_state": "pending_publish",
    }])
    requested = QSignalSpy(page.targeted_wd14_requested)
    status_messages = QSignalSpy(page.page_status.message_changed)
    status_progress = QSignalSpy(page.page_status.progress_changed)
    status_progress_cleared = QSignalSpy(page.page_status.progress_cleared)
    assert not hasattr(page, "wd14_status")
    page.bulk_add.add_tag("1girl")
    page.bulk_add._chips["1girl"].analysis.click()
    assert [post["id"] for post in requested.at(0)[1]] == [1, 2]
    assert page.bulk_add._chips["1girl"].analysis.text() == "⏳"

    result = TargetedWD14Result(
        "1girl", {1: 0.95, 2: 0.82}, frozenset(),
        TargetedWD14Progress(2, 2, 1, 1, 0), 0.5,
    )
    page.show_targeted_wd14_result(result)
    assert status_messages.at(status_messages.count() - 1)[1] == (
        "2 images · 1 cached · 1 new · 0 errors"
    )
    assert status_progress.count() >= 1
    assert status_progress_cleared.count() == 1
    assert [group.title for group in page.result_groups] == [
        "90–100 % — 1", "80–89 % — 1",
    ]
    page.result_groups[0].select_all.click()
    assert page.result_groups[0].select_all.checkState() == Qt.CheckState.Checked
    assert [post["id"] for post in page.selected_posts()] == [1]
    assert "WD14 95 %" in page.result_buttons[1].text()
    page.bulk_add.add_tag("child")
    page.bulk_add._chips["child"].analysis.click()
    assert requested.count() == 2
    page.show_targeted_wd14_result(TargetedWD14Result(
        "child", {1: 0.20, 2: 0.95}, frozenset(),
        TargetedWD14Progress(2, 2, 2, 0, 0), 0.1,
    ))
    assert page.result_groups[0].cards[0].post["id"] == 2
    page.bulk_add.remove_tag("child"); app.processEvents()
    assert page._confidence_tag == ""
    assert len(page.result_groups) == 3
    page.close()
