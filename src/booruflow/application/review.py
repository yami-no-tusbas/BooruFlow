"""Validated review requests and command construction for review engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    queries: tuple[str, ...]
    sites: tuple[str, ...]
    entity_type: str
    pages: int
    start_page: int
    minimum_results: int
    maximum_results: int
    match_percent: int
    remember_queries: bool
    auto_continue: bool
    gelbooru_database: Path
    e621_database: Path
    output_root: Path
    grabber_directory: Path | None = None

    def __post_init__(self) -> None:
        if not self.queries:
            raise ValueError("at least one query is required")
        if not self.sites or any(site not in {"gelbooru", "e621"} for site in self.sites):
            raise ValueError("at least one supported site is required")
        if self.entity_type not in {"artists", "copyrights", "characters", "species"}:
            raise ValueError("unsupported entity type")
        if self.entity_type == "species" and "gelbooru" in self.sites:
            raise ValueError("species review is only available for e621")
        if self.pages < 1 or self.start_page < 1:
            raise ValueError("page values must be positive")
        if self.minimum_results < 0 or self.maximum_results < 0:
            raise ValueError("result limits must not be negative")
        if self.maximum_results and self.maximum_results < self.minimum_results:
            raise ValueError("maximum results must be zero or greater than minimum")
        if not 0 <= self.match_percent <= 100:
            raise ValueError("match percent must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class EngineCommand:
    site: str
    program: str
    arguments: tuple[str, ...]
    environment: dict[str, str] = field(default_factory=dict)
    output_directory: Path = Path(".")
    working_directory: Path = Path(".")


def build_review_commands(
    request: ReviewRequest,
    project_root: Path,
    python_executable: str,
    credentials: dict[str, object] | None = None,
) -> list[EngineCommand]:
    blacklist = (
        request.grabber_directory / "blacklist.txt"
        if request.grabber_directory
        else project_root / "config" / "blacklist.txt"
    )
    ignore = (
        request.grabber_directory / "ignore.txt"
        if request.grabber_directory
        else project_root / "config" / "ignore.txt"
    )
    common = (
        "--pages",
        str(request.pages),
        "--page-debut",
        str(request.start_page),
        "--min-artist-posts",
        str(request.minimum_results),
        "--max-artist-posts",
        str(request.maximum_results),
        "--min-match-percent",
        str(request.match_percent),
        "--cache-days",
        "30",
        "--blacklist",
        str(blacklist),
        "--ignore",
        str(ignore),
        "--autoriser-requetes-ignorees",
        "--entity-type",
        request.entity_type,
    )
    commands: list[EngineCommand] = []
    for site in request.sites:
        output = request.output_root / request.entity_type / site
        if site == "gelbooru":
            site_credentials = (credentials or {}).get("gelbooru", {})
            if not isinstance(site_credentials, dict):
                site_credentials = {}
            environment = {
                "GELBOORU_USER_ID": str(site_credentials.get("user_id", "")),
                "GELBOORU_API_KEY": str(site_credentials.get("api_key", "")),
            }
            arguments = (
                "-u",
                "-m",
                "booruflow.cli.gelbooru_scan",
                str(request.gelbooru_database),
                *request.queries,
                *common,
                "--min-hits",
                "1",
                "--sortie",
                str(output),
            )
            if request.remember_queries:
                arguments += ("--memoriser-requetes",)
        else:
            environment = {}
            arguments = (
                "-u",
                "-m",
                "booruflow.cli.e621_scan",
                str(request.e621_database),
                *request.queries,
                *common,
                "--sortie",
                str(output),
            )
        commands.append(
            EngineCommand(site, python_executable, arguments, environment, output, project_root)
        )
    return commands
