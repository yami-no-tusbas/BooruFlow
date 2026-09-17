import tempfile
import unittest
from pathlib import Path

from PIL import Image

from booruflow.application.image_analysis import ImageAnalysisWorkflow, QueuePolicy
from booruflow.domain.image_analysis import AnalysisState, InputKind
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService


class ImageAnalysisWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.repository = ImageAnalysisRepository(self.root / "state.sqlite")
        self.sources = ImageSourceService(self.repository, self.root / "cache")
        self.workflow = ImageAnalysisWorkflow(
            self.repository, self.sources, QueuePolicy(download_prefetch=2, analysis_prefetch=2)
        )

    def tearDown(self) -> None:
        self.repository.close(); self.temporary.cleanup()

    def test_download_prefetch_claims_only_configured_depth(self) -> None:
        self.workflow.add_remote_ids(InputKind.E621_POST, ["1", "2", "3"])
        self.assertEqual(len(self.workflow.sources_to_resolve()), 2)
        self.assertEqual(self.workflow.sources_to_resolve(), [])

    def test_active_review_does_not_consume_analysis_prefetch(self) -> None:
        paths = []
        for index in range(4):
            path = self.root / f"{index}.png"
            Image.new("RGB", (4, 4), (index, index, index)).save(path)
            paths.append(path)
        ids = self.workflow.add_local_files(paths)
        self.repository.transition(ids[0], AnalysisState.PROCESSING)
        self.repository.transition(ids[0], AnalysisState.READY_FOR_REVIEW)
        active = self.workflow.next_for_review()
        self.assertEqual(active.id, ids[0])
        for item_id in ids[1:3]:
            claimed = self.repository.claim_next(2)
            self.repository.transition(claimed.id, AnalysisState.READY_FOR_REVIEW)
        self.assertIsNone(self.repository.claim_next(2))
        next_item = self.workflow.complete_review(active.id)
        self.assertEqual(next_item.id, ids[1])
        self.assertIsNotNone(self.repository.claim_next(2))


if __name__ == "__main__":
    unittest.main()
