import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from booruflow.application.review import ReviewRequest, build_review_commands
from booruflow.presentation.pyside6.review_controller import ReviewOutputState


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
            "auto_continue": True,
            "gelbooru_database": root / "gel.db",
            "e621_database": root / "e621.db",
            "output_root": root / "results",
            "blacklist_file": root / "chosen-blacklist.txt",
        }
        values.update(changes)
        return ReviewRequest(**values)

    def test_species_is_rejected_for_gelbooru(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
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
            self.assertEqual(
                command.arguments[:3],
                ("-u", "-m", "booruflow.cli.gelbooru_scan"),
            )
            self.assertFalse(any("legacy" in argument for argument in command.arguments))

    def test_e621_command_uses_the_packaged_cli_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root, sites=("e621",))
            command = build_review_commands(request, root, "python")[0]

        self.assertEqual(
            command.arguments[:3],
            ("-u", "-m", "booruflow.cli.e621_scan"),
        )
        self.assertFalse(any("legacy" in argument for argument in command.arguments))

    def test_command_uses_selected_blacklist_file_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            command = build_review_commands(request, root, "python")[0]

        blacklist_index = command.arguments.index("--blacklist")
        ignore_index = command.arguments.index("--ignore")
        self.assertEqual(command.arguments[blacklist_index + 1], str(request.blacklist_file))
        self.assertEqual(
            command.arguments[ignore_index + 1], str(root / "config" / "ignore.txt")
        )

    def test_legacy_parser_has_no_hardcoded_credentials(self) -> None:
        from booruflow.cli.gelbooru_scan import parse_args

        environment = {"GELBOORU_USER_ID": "", "GELBOORU_API_KEY": ""}
        with patch.dict(os.environ, environment), patch.object(sys, "argv", ["scanner"]):
            arguments = parse_args()
        self.assertEqual(arguments.user_id, "")
        self.assertEqual(arguments.api_key, "")

    def test_review_output_state_accepts_accented_and_mojibake_continuation_markers(self) -> None:
        accented = ReviewOutputState()
        mojibake = ReviewOutputState()

        accented.consume("Aucun résultat, prochain départ page 12", "translated")
        mojibake.consume("Aucun résultat, prochain dÃ©part page 27", "translated")

        self.assertEqual(accented.next_page, 12)
        self.assertEqual(mojibake.next_page, 27)

    def test_review_output_state_accumulates_e621_retained_results(self) -> None:
        state = ReviewOutputState()
        state.consume("768 artistes e621 retenus.", "768 artists retained")

        self.assertEqual(state.retained, 768)
        self.assertEqual(state.summary, ["768 artists retained"])
