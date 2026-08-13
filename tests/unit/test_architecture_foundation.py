import ast
import tempfile
import unittest
from pathlib import Path

from booruflow.domain import EntityType, SearchRequest, Site
from booruflow.infrastructure.grabber import GrabberInstallation


def legacy_imports(source_root: Path) -> set[tuple[str, str]]:
    imports: set[tuple[str, str]] = set()
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(source_root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module == "legacy" or (node.module or "").startswith("legacy.")
            ):
                imports.add((relative, node.module or ""))
            elif isinstance(node, ast.Import):
                imports.update(
                    (relative, alias.name)
                    for alias in node.names
                    if alias.name == "legacy" or alias.name.startswith("legacy.")
                )
    return imports


class ArchitectureFoundationTests(unittest.TestCase):
    def test_search_request_rejects_invalid_percent(self) -> None:
        with self.assertRaises(ValueError):
            SearchRequest(
                query="rating:general",
                sites=(Site.GELBOORU,),
                entity_type=EntityType.ARTISTS,
                minimum_match_percent=101,
            )

    def test_grabber_is_an_optional_capability(self) -> None:
        missing = GrabberInstallation(None).availability()
        with tempfile.TemporaryDirectory() as directory:
            configured_but_missing = GrabberInstallation(Path(directory)).availability()

        self.assertFalse(missing.available)
        self.assertFalse(configured_but_missing.available)
        self.assertIn("configured", missing.reason)
        self.assertIn("Grabber.exe", configured_but_missing.reason)

    def test_no_new_legacy_dependency_enters_the_modern_package(self) -> None:
        source_root = Path(__file__).resolve().parents[2] / "src" / "booruflow"
        self.assertEqual(legacy_imports(source_root), set())


if __name__ == "__main__":
    unittest.main()
