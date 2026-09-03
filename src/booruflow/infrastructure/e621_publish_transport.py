"""API-native e621 tag publication transport."""

from __future__ import annotations

from booruflow.application.publish_preparation import PublishPreparation
from booruflow.infrastructure.e621_client import E621Client


class E621PublishTransport:
    """Submit only BooruFlow's explicit additions/removals as an e621 tag diff."""

    def __init__(self, client: E621Client) -> None:
        self.client = client

    def submit_prepared(self, _session: object, prepared: PublishPreparation) -> None:
        self.client.update_post_tags(
            prepared.post_id,
            prepared.additions,
            prepared.removals,
        )
