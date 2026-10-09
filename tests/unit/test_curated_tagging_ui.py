import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest

from booruflow.application.curated_profiles import load_curated_profiles
from booruflow.domain.auto_organize import PostMetadata
from booruflow.domain.image_analysis import ObservationSource
from booruflow.infrastructure.entity_profile_repository import EntityProfileRepository
from booruflow.infrastructure.wd14 import WD14Result, WD14Tag
from booruflow.presentation.pyside6.tagging_suggestions import (
    CURATED_KEY_ROLE,
    CURATED_PARENT_ROLE,
    CURATED_PENDING_ROLE,
    ID_COLUMN,
    ROW_KIND_ROLE,
)
from tests.unit.test_curated_profiles import catalogue, pack  # noqa: F401
from tests.unit.test_tagging_navigation_prefetch import navigation  # noqa: F401


def find(table, token):
    return next(row for row in range(table.rowCount())
                if table.item(row, ID_COLUMN).text() == str(token))


def focus(table, token):
    table.selectRow(find(table, token))
    table.setFocus()


def tokens(table):
    return [table.item(row, ID_COLUMN).text() for row in range(table.rowCount())]


def capture(n, name):
    directory = os.environ.get("BOORUFLOW_CURATED_CAPTURE_DIR")
    if directory:
        n.app.processEvents()
        assert n.app.platformName() == "windows"
        assert n.page.windowHandle().isExposed()
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        assert n.page.grab().save(str(path / f"{name}.png"))


@pytest.fixture
def curated(navigation, catalogue, tmp_path):  # noqa: F811
    n = navigation
    tags, aliases = catalogue
    directory = tmp_path / "packs"
    pack(directory, [{"trigger": "panties", "top_count": 2, "groups": [
        {"id": "shape", "label": {"en": "Shape", "fr": "Forme"},
         "tags": ["thong", "old_thong", "highleg_panties"]},
        {"id": "color", "label": {"en": "Color / Pattern", "fr": "Couleur / motif"},
         "tags": ["white_panties", "leopard_print_panties", "thong"]}]},
        {"trigger": "censored", "groups": [{"id": "type", "tags": ["bar_censor"]}]},
        {"trigger": "gyaru", "groups": [{"id": "skin", "tags": ["tan"]}]}])
    n.controller._settings.update(gelbooru_tag_database=str(tags),
                                  gelbooru_alias_database=str(aliases))
    n.analysis.settings.update(n.controller._settings)
    n.controller._curated_profiles = load_curated_profiles([directory], tags, aliases)
    n.backend.analyze = lambda path: WD14Result(tuple(WD14Tag(name, "general", score) for
        name, score in [("panties", .94), ("highleg_panties", .81),
                       ("censored", .73), ("gyaru", .68)]))
    n.show(2)
    n.open(1)
    item_id = n.resolve(1)
    assert n.worker.process_one()
    n.controller.refresh_local_review()
    n.page.decision_filter.setCurrentIndex(n.page.decision_filter.findData("all"))
    n.item_id = item_id
    n.ids = {obs.name: oid for oid, obs in n.analysis.repository.observations(item_id)}
    n.page.activateWindow()
    n.page.review_splitter.setSizes([450, 750])
    n.app.processEvents()
    return n


def test_groups_keyboard_pending_shared_state_no_observations_or_cascade(curated):
    n = curated
    table = n.page.suggestions
    assert {"group:panties", "group:censored", "group:gyaru"} <= set(tokens(table))
    before = n.analysis.repository.observations(n.item_id)
    assert not any(token.startswith("curated:") for token in tokens(table))
    capture(n, "closed-en")
    focus(table, "group:panties")
    QTest.keyClick(table, Qt.Key.Key_Right)
    assert "curated:panties:thong" in tokens(table)
    assert "group:panties/shape" in tokens(table)
    capture(n, "open-top-en")
    focus(table, "group:panties/shape")
    QTest.keyClick(table, Qt.Key.Key_Right)
    assert tokens(table).count("curated:panties:thong") == 2
    focus(table, "curated:panties:thong")
    QTest.keyClick(table, Qt.Key.Key_Space)
    n.app.processEvents()
    assert n.analysis.repository.observations(n.item_id) == before
    assert n.controller._curated_state(n.item_id).pending["panties"] == {"thong"}
    assert all(table.item(row, 0).data(CURATED_PENDING_ROLE) for row in range(table.rowCount())
               if table.item(row, ID_COLUMN).text() == "curated:panties:thong")
    capture(n, "pending-en")
    focus(table, "curated:panties:thong")
    QTest.keyClick(table, Qt.Key.Key_Left)
    assert table.item(table.currentRow(), 0).data(CURATED_KEY_ROLE) == "group:panties"
    QTest.keyClick(table, Qt.Key.Key_Left)
    assert "curated:panties:thong" not in tokens(table)
    focus(table, "group:panties")
    QTest.keyClick(table, Qt.Key.Key_Down)
    assert table.currentRow() > find(table, "group:panties")
    QTest.keyClick(table, Qt.Key.Key_Up)
    assert table.currentRow() == find(table, "group:panties")
    n.page.manual_tag.setText("abcd")
    n.page.manual_tag.setFocus()
    QTest.keyClick(n.page.manual_tag, Qt.Key.Key_Left)
    QTest.keyClick(n.page.manual_tag, Qt.Key.Key_Right)
    assert "group:panties" not in table._curated_expanded


@pytest.mark.parametrize("pending", [(), ("thong", "leopard_print_panties"),
                                    ("highleg_panties", "thong")])
def test_bundle_atomic_undo_redo_root_hidden_and_wd14_score_preserved(curated, pending):
    n = curated
    for name in pending:
        n.controller.select_curated_refinement("panties", name, True)
    before_count = len(n.analysis.repository.observations(n.item_id))
    focus(n.page.suggestions, n.ids["panties"])
    QTest.keyClick(n.page.suggestions, Qt.Key.Key_A)
    n.app.processEvents()
    observations = n.analysis.repository.observations(n.item_id)
    accepted = {obs.reviewed_name or obs.name for _, obs in observations
                if obs.decision.value == "accepted"}
    assert accepted == {"panties", *pending}
    assert len(n.controller._undo_stack) == 1
    assert len(observations) == before_count + len(set(pending) - {"highleg_panties"})
    assert "group:panties" not in tokens(n.page.suggestions)
    assert next(obs.confidence for _, obs in observations if obs.name == "highleg_panties") == .81
    for name in pending:
        row = next(row for row in n.page._all_suggestion_rows if row["tag"] == name)
        assert "Curated: panties" in row["match"]
        if name == "highleg_panties":
            assert "WD14" in row["match"] and row["confidence"] == "0.810"
    capture(n, "confirmed-bundle")
    n.controller.undo()
    assert not any(obs.decision.value == "accepted" for _, obs in
                   n.analysis.repository.observations(n.item_id))
    assert "group:panties" in tokens(n.page.suggestions)
    assert n.controller._curated_state(n.item_id).pending["panties"] == set(pending)
    n.controller.redo()
    assert {obs.reviewed_name or obs.name for _, obs in n.analysis.repository.observations(n.item_id)
            if obs.decision.value == "accepted"} == {"panties", *pending}
    assert "group:panties" not in tokens(n.page.suggestions)


def test_reject_discards_pending_and_undo_restores(curated):
    n = curated
    n.controller.select_curated_refinement("panties", "thong", True)
    n.controller.decide(n.ids["panties"], "rejected")
    assert "panties" not in n.controller._curated_state(n.item_id).pending
    assert "group:panties" not in tokens(n.page.suggestions)
    assert not any(obs.source is ObservationSource.MANUAL for _, obs in
                   n.analysis.repository.observations(n.item_id))
    n.controller.undo()
    assert n.controller._curated_state(n.item_id).pending["panties"] == {"thong"}
    n.controller.redo()
    assert "panties" not in n.controller._curated_state(n.item_id).pending


def test_dynamic_dedup_source_confirmed_empty_and_multiple_provenances(curated, tmp_path):
    n = curated
    n.controller.current_post["tags"] = "solo old_thong white_panties"
    # Persisted source takes precedence, so update the synthetic source catalogue too.
    repo = n.analysis.repository
    repo.connection.execute("UPDATE source_tags SET tag_name='old_thong' WHERE item_id=?", (n.item_id,))
    repo.connection.commit()
    n.controller.refresh_local_review()
    root = next(row for row in n.page._all_suggestion_rows if row["tag"] == "panties")
    assert "thong" not in {tag["tag"] for group in root["curated"]["groups"] for tag in group["tags"]}
    n.controller.decide(n.ids["highleg_panties"], "accepted")
    root = next(row for row in n.page._all_suggestion_rows if row["tag"] == "panties")
    assert "highleg_panties" not in {tag["tag"] for group in root["curated"]["groups"]
                                   for tag in group["tags"]}
    n.controller.select_curated_refinement("censored", "bar_censor", True)
    n.controller.decide(n.ids["censored"], "accepted")
    assert "group:censored" not in tokens(n.page.suggestions)


def test_pending_never_seeds_entity_and_confirmed_tags_join_normal_workflow(curated, tmp_path):
    n = curated
    path = tmp_path / "var/state/entity_profiles.sqlite"
    with EntityProfileRepository(path) as profiles:
        profile_id = profiles.create("thong", "thong")
        profiles.add_posts(profile_id, [PostMetadata("gelbooru", "10", ("thong", "tan"), {})])
        for row in profiles.tags(profile_id):
            profiles.set_status(profile_id, row["tag"], "keep")
        before = profiles.tags(profile_id)
    n.controller.select_curated_refinement("panties", "thong", True)
    assert not any(row.get("profile_scores") for row in n.page._all_suggestion_rows)
    n.controller.decide(n.ids["panties"], "accepted")
    assert any(row["tag"] == "tan" and row.get("profile_scores")
               for row in n.page._all_suggestion_rows)
    with EntityProfileRepository(path) as profiles:
        assert profiles.tags(profile_id) == before
        assert profiles.review_decisions(n.item_id) == {}
    n.controller.undo()
    assert not any(row.get("profile_scores") for row in n.page._all_suggestion_rows)


def test_entity_wd14_curated_identity_and_score_coexist(curated, tmp_path):
    n = curated
    path = tmp_path / "var/state/entity_profiles.sqlite"
    with EntityProfileRepository(path) as profiles:
        profile_id = profiles.create("solo", "solo")
        profiles.add_posts(profile_id, [PostMetadata(
            "gelbooru", "10", ("solo", "highleg_panties", "thong"), {})])
        for row in profiles.tags(profile_id):
            profiles.set_status(profile_id, row["tag"], "keep")
        before = profiles.tags(profile_id)
    n.controller.refresh_local_review()
    row = next(row for row in n.page._all_suggestion_rows if row["tag"] == "highleg_panties")
    assert row["wd14_score"] == .81 and row["confidence"] == "0.810"
    assert row["profile_scores"] and "Curated: panties" in row["match"]
    assert sum(row["tag"] == "highleg_panties" for row in n.page._all_suggestion_rows) == 1
    n.controller.select_curated_refinement("panties", "highleg_panties", True)
    n.controller.select_curated_refinement("panties", "thong", True)
    n.controller.decide(n.ids["panties"], "accepted")
    assert sum(obs.name == "thong" for _, obs in n.analysis.repository.observations(n.item_id)) == 1
    with EntityProfileRepository(path) as profiles:
        assert profiles.tags(profile_id) == before
        assert profiles.review_decisions(n.item_id) == {}


def test_alias_wd14_child_reuses_observation_and_empty_group_disappears(curated):
    n = curated
    repo = n.analysis.repository
    repo.connection.execute("UPDATE tag_observations SET tag_name='old_thong' WHERE id=?",
                            (n.ids["highleg_panties"],))
    repo.connection.commit()
    n.controller.refresh_local_review()
    assert sum(row["tag"] == "thong" for row in n.page._all_suggestion_rows) == 1
    n.controller.select_curated_refinement("panties", "thong", True)
    n.controller.decide(n.ids["panties"], "accepted")
    obs = dict(repo.observations(n.item_id))[n.ids["highleg_panties"]]
    assert obs.reviewed_name == "thong" and obs.confidence == .81
    assert len(repo.observations(n.item_id)) == 4
    n.controller.add_manual_tag("bar_censor")
    assert "group:censored" not in tokens(n.page.suggestions)


def test_manual_accept_root_also_confirms_pending_and_items_are_isolated(curated):
    n = curated
    repo = n.analysis.repository
    repo.connection.execute("UPDATE tag_observations SET tag_name='old_panties' WHERE id=?",
                            (n.ids["panties"],))
    repo.connection.commit()
    n.controller.refresh_local_review()
    n.controller.select_curated_refinement("panties", "thong", True)
    n.controller.add_manual_tag("panties")
    assert {obs.reviewed_name or obs.name for _, obs in n.analysis.repository.observations(n.item_id)
            if obs.decision.value == "accepted"} == {"panties", "thong"}
    assert len(repo.observations(n.item_id)) == 5
    n.controller.undo()
    n.open(2)
    second_id = n.resolve(2, "blue")
    assert n.worker.process_one()
    n.controller.refresh_local_review()
    assert n.controller._curated_state(second_id).pending["panties"] == set()
    assert not n.page.suggestions._curated_expanded
    n.open(1)
    n.controller.refresh_local_review()
    assert n.controller._curated_state(n.item_id).pending["panties"] == {"thong"}


def test_bundle_failure_rolls_back_every_observation_and_pending(curated):
    n = curated
    n.controller.select_curated_refinement("panties", "thong", True)
    repo = n.analysis.repository
    before = repo.observations(n.item_id)
    with pytest.raises(KeyError):
        repo.apply_review_observation_bundle(
            [(n.ids["panties"], "accepted", None), (-1, "accepted", None)], ["thong"],
            item_id=n.item_id)
    assert repo.observations(n.item_id) == before
    with patch.object(repo, "apply_review_observation_bundle", side_effect=RuntimeError("failure")):
        n.controller.decide(n.ids["panties"], "accepted")
    assert repo.observations(n.item_id) == before
    assert n.controller._curated_state(n.item_id).pending["panties"] == {"thong"}
    assert n.controller._undo_stack == []


def test_main_window_loads_before_lazy_tagging_and_passes_prepared_index(
    curated, catalogue, tmp_path,  # noqa: F811
):
    from booruflow.application.capabilities import ApplicationCapabilities
    from booruflow.domain import ToolAvailability
    from booruflow.infrastructure.settings import JsonSettingsRepository
    from booruflow.presentation.pyside6.main_window import MainWindow

    tags, aliases = catalogue
    root = tmp_path / "startup"
    pack(root / "resources/curated_tag_profiles")
    settings = JsonSettingsRepository(root / "config/settings.json")
    settings.save({"gelbooru_tag_database": str(tags), "gelbooru_alias_database": str(aliases),
                   "image_analysis_wd14_enabled": False, "first_run_wizard_completed": True})
    window = MainWindow(ApplicationCapabilities(ToolAvailability(False, "test")),
        curated.page.catalog, project_root=root, resource_root=root,
        settings_repository=settings, start_image_worker=False)
    try:
        assert window.tagging_controller is None
        assert "panties" in window.curated_profiles
        with patch("booruflow.presentation.pyside6.curated_tagging.configured_curated_profiles",
                   side_effect=AssertionError("lazy Tagging must reuse the startup index")):
            page = window._build_tagging()
        assert window.tagging_controller._curated_profiles is window.curated_profiles
        assert page is window.tagging_page
    finally:
        window.close()
        curated.app.processEvents()


def test_live_language_change_keeps_pending_expansion_and_text_focus(curated):
    n = curated
    table = n.page.suggestions
    focus(table, "group:panties")
    QTest.keyClick(table, Qt.Key.Key_Right)
    n.controller.select_curated_refinement("panties", "thong", True)
    before = n.analysis.repository.observations(n.item_id)
    n.page.manual_tag.setFocus()
    n.app.processEvents()
    n.page.catalog.set_language("fr")
    n.page.retranslate()
    assert "Forme" in table.item(find(table, "group:panties/shape"), 0).text()
    assert "group:panties" in table._curated_expanded
    assert n.controller._curated_state(n.item_id).pending["panties"] == {"thong"}
    assert n.analysis.repository.observations(n.item_id) == before
    assert n.page.manual_tag.hasFocus()


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("dark", [False, True])
def test_native_themes_locales_and_subgroups(curated, language, dark, request):
    from booruflow.presentation.pyside6.theme import refresh_widget_tree
    n = curated
    original_palette = QPalette(n.app.palette())
    request.addfinalizer(lambda: n.app.setPalette(original_palette))
    n.page.catalog.set_language(language)
    palette = QPalette(n.page.palette())
    for role, light, dark_color in (
        (QPalette.ColorRole.Window, "#ffffff", "#202020"),
        (QPalette.ColorRole.Base, "#ffffff", "#252525"),
        (QPalette.ColorRole.AlternateBase, "#eeeeee", "#353535"),
        (QPalette.ColorRole.Text, "#000000", "#ffffff"),
        (QPalette.ColorRole.WindowText, "#000000", "#ffffff"),
        (QPalette.ColorRole.Button, "#eeeeee", "#353535"),
        (QPalette.ColorRole.ButtonText, "#000000", "#ffffff"),
    ):
        palette.setColor(role, QColor(dark_color if dark else light))
    n.app.setPalette(palette)
    n.page.setPalette(palette)
    n.page.suggestions.setPalette(palette)
    refresh_widget_tree(n.page)
    n.page.retranslate()
    n.controller.refresh_local_review()
    table = n.page.suggestions
    focus(table, "group:panties")
    QTest.keyClick(table, Qt.Key.Key_Right)
    focus(table, "group:panties/shape")
    QTest.keyClick(table, Qt.Key.Key_Right)
    n.controller.select_curated_refinement("panties", "thong", True)
    label = table.item(find(table, "group:panties/shape"), 0).text()
    assert ("Forme" if language == "fr" else "Shape") in label
    assert all(not table.item(row, 0).data(CURATED_PARENT_ROLE).count("/") > 1
               for row in range(table.rowCount()) if table.item(row, 0).data(CURATED_PARENT_ROLE))
    assert table.item(find(table, "group:panties"), 0).data(ROW_KIND_ROLE) == "CURATED_GROUP"
    capture(n, f"{'dark' if dark else 'light'}-{language}")
