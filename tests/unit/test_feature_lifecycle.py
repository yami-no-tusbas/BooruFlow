import importlib.util
import os
import unittest

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class FeatureLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def lifecycle(self):
        from booruflow.presentation.pyside6.feature_lifecycle import FeatureLifecycle

        scheduled = []
        return FeatureLifecycle(schedule=scheduled.append), scheduled

    def drain_one(self, scheduled) -> None:
        scheduled.pop(0)()

    def test_user_activation_precedes_remaining_sequential_warmups(self) -> None:
        from booruflow.presentation.pyside6.feature_lifecycle import FeaturePolicy, FeatureState

        lifecycle, scheduled = self.lifecycle()
        events = []
        states = []
        lifecycle.state_changed.connect(
            lambda key, state, _error: states.append((key, state))
        )
        first_done = []
        lifecycle.register(
            "first",
            FeaturePolicy.WARMUP,
            lambda done, _failed: (events.append("first"), first_done.append(done)),
        )
        lifecycle.register(
            "second",
            FeaturePolicy.WARMUP,
            lambda done, _failed: (events.append("second"), done()),
        )
        lifecycle.register(
            "wanted",
            FeaturePolicy.LAZY,
            lambda done, _failed: (events.append("wanted"), done()),
        )

        lifecycle.start_warmups()
        self.drain_one(scheduled)
        self.assertEqual(lifecycle.state("first"), FeatureState.LOADING)
        lifecycle.request("wanted")
        self.drain_one(scheduled)
        first_done[0]()
        while scheduled:
            self.drain_one(scheduled)

        self.assertEqual(events, ["first", "wanted", "second"])
        self.assertEqual(lifecycle.state("wanted"), FeatureState.READY)
        self.assertIn(("wanted", FeatureState.LOADING.value), states)
        self.assertIn(("wanted", FeatureState.READY.value), states)

    def test_ready_feature_runs_once_and_failed_feature_can_retry(self) -> None:
        from booruflow.presentation.pyside6.feature_lifecycle import FeaturePolicy, FeatureState

        lifecycle, scheduled = self.lifecycle()
        attempts = []

        def prepare(done, failed):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                failed("temporary failure")
            else:
                done()

        lifecycle.register("lazy", FeaturePolicy.LAZY, prepare)
        lifecycle.request("lazy")
        while scheduled:
            self.drain_one(scheduled)
        self.assertEqual(lifecycle.state("lazy"), FeatureState.FAILED)
        self.assertEqual(lifecycle.error("lazy"), "temporary failure")

        lifecycle.retry("lazy")
        while scheduled:
            self.drain_one(scheduled)
        lifecycle.request("lazy")
        while scheduled:
            self.drain_one(scheduled)
        lifecycle.start_warmups()
        while scheduled:
            self.drain_one(scheduled)
        self.assertEqual(attempts, [1, 2])
        self.assertEqual(lifecycle.state("lazy"), FeatureState.READY)

    def test_warmup_runs_once_even_if_start_is_requested_again(self) -> None:
        from booruflow.presentation.pyside6.feature_lifecycle import FeaturePolicy

        lifecycle, scheduled = self.lifecycle()
        calls = []
        lifecycle.register(
            "warm",
            FeaturePolicy.WARMUP,
            lambda done, _failed: (calls.append("warm"), done()),
        )
        lifecycle.start_warmups()
        while scheduled:
            self.drain_one(scheduled)
        lifecycle.start_warmups()
        while scheduled:
            self.drain_one(scheduled)
        self.assertEqual(calls, ["warm"])

    def test_user_requested_dependency_finishes_before_dependent_feature(self) -> None:
        from booruflow.presentation.pyside6.feature_lifecycle import FeaturePolicy

        lifecycle, scheduled = self.lifecycle()
        calls = []
        lifecycle.register(
            "database",
            FeaturePolicy.WARMUP,
            lambda done, _failed: (calls.append("database"), done()),
        )
        lifecycle.register(
            "similar",
            FeaturePolicy.LAZY,
            lambda done, _failed: (calls.append("similar"), done()),
            dependencies=("database",),
        )

        lifecycle.request("similar")
        while scheduled:
            self.drain_one(scheduled)

        self.assertEqual(calls, ["database", "similar"])

    def test_prepare_runs_on_lifecycle_gui_thread(self) -> None:
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QWidget

        from booruflow.presentation.pyside6.feature_lifecycle import FeaturePolicy

        lifecycle, scheduled = self.lifecycle()
        observed = []
        widgets = []

        def prepare(done, _failed):
            observed.append(QThread.currentThread())
            widgets.append(QWidget())
            done()

        lifecycle.register(
            "gui",
            FeaturePolicy.LAZY,
            prepare,
        )
        lifecycle.request("gui")
        while scheduled:
            self.drain_one(scheduled)
        self.assertIs(observed[0], lifecycle.thread())
        widgets[0].close()

    def test_loading_overlay_is_non_modal_localized_and_retryable(self) -> None:
        from pathlib import Path
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QWidget

        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.feature_lifecycle import FeatureState
        from booruflow.presentation.pyside6.feature_loading import FeaturePageHost

        languages = Path(__file__).resolve().parents[2] / "resources" / "i18n"
        host = FeaturePageHost(QWidget(), LanguageCatalog(languages, "fr"), "nav.cleanup")
        retried = MagicMock()
        host.retry_requested.connect(retried)
        host.set_feature_state(FeatureState.LOADING)
        self.assertIn("Chargement", host.loading_label.text())
        self.assertTrue(host.spinner._timer.isActive())
        self.assertFalse(host.loading.isHidden())
        host.set_feature_state(FeatureState.FAILED, "database is locked")
        self.assertFalse(host.spinner._timer.isActive())
        self.assertEqual(host.details.toPlainText(), "database is locked")
        host.retry_button.click()
        retried.assert_called_once_with()
        host.set_feature_state(FeatureState.READY)
        self.assertTrue(host.loading.isHidden())
        self.assertTrue(host.failure.isHidden())
        host.close()
