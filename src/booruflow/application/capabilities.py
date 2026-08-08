"""Resolve optional application capabilities without importing a GUI."""

from __future__ import annotations

from dataclasses import dataclass

from booruflow.application.ports import GrabberGateway
from booruflow.domain import ToolAvailability


@dataclass(frozen=True, slots=True)
class ApplicationCapabilities:
    grabber: ToolAvailability


def resolve_capabilities(grabber: GrabberGateway) -> ApplicationCapabilities:
    return ApplicationCapabilities(grabber=grabber.availability())

