import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from booruflow.application.review import ReviewRequest, build_review_commands


class ReviewRequestTests(unittest.TestCase):
    def request(self, root: Path, **changes) -> ReviewRequest:
        values = {
            "queries": ("rating:general",),
            "sites": ("gelbooru",),
            "entity_type": "artists",
            "pages": 10,
            "start_page": 1,
            "minimum_results": 50,
            "maximum_results": 0,
            "match_percent": 25,
            "remember_queries": True,
            "gelbooru_database": root / "gel.db",
            "e621_database": root / "e621.db",
            "output_root": root / "results",
            "grabber_directory": root / "grabber",
        }
        values.update(changes)
        return ReviewRequest(**values)

    def test_species_is_rejected_for_gelbooru(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.request(Path(directory), entity_type="species")

    def test_command_keeps_credentials_out_of_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            commands = build_review_commands(
                request,
                root,
                "python",
                {"gelbooru": {"user_id": "user-secret", "api_key": "key-secret"}},
            )
            self.assertEqual(len(commands), 1)
            command = commands[0]
            self.assertNotIn("user-secret", command.arguments)
            self.assertNotIn("key-secret", command.arguments)
            self.assertEqual(command.environment["GELBOORU_USER_ID"], "user-secret")
            self.assertEqual(command.environment["GELBOORU_API_KEY"], "key-secret")
            self.assertEqual(command.working_directory, root)

    def test_legacy_parser_has_no_hardcoded_credentials(self) -> None:
        from legacy.gelbooru_artistes_par_tags_ignore import parse_args

        environment = {"GELBOORU_USER_ID": "", "GELBOORU_API_KEY": ""}
        with patch.dict(os.environ, environment), patch.object(sys, "argv", ["scanner"]):
            arguments = parse_args()
        self.assertEqual(arguments.user_id, "")
        self.assertEqual(arguments.api_key, "")
