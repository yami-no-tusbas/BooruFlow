"""Per-item provisional refinements and atomic confirmation via normal observations."""
from dataclasses import dataclass, field

from booruflow.application.curated_profiles import configured_curated_profiles
from booruflow.application.tagging import normalize_booru_tag
from booruflow.domain.booru_sites import category_name
from booruflow.domain.curated_profiles import parse_refinement_token, refinement_token
from booruflow.runtime import resource_root


@dataclass
class CuratedReviewState:
    pending: dict[str, set[str]] = field(default_factory=dict)
    targets: dict[str, str | int] = field(default_factory=dict)


@dataclass(frozen=True)
class CuratedBundleOperation:
    item_id: int
    before: tuple[tuple[int, str, str | None], ...]
    after: tuple[tuple[int, str, str | None], ...]
    pending_before: dict[str, set[str]]
    pending_after: dict[str, set[str]]
    site: str
    canonical_before: tuple
    canonical_after: tuple


class CuratedTaggingMixin:
    def _init_curated(self, profiles=None):
        self._curated_validation_warnings = set()
        self._curated_resource_root = resource_root()
        self._curated_profiles = profiles if profiles is not None else configured_curated_profiles(
            self._project_root, resource_root(), self._settings,
            warn=self._warn_curated_once)
        self._curated_reviews = {}
        self.page.suggestions.refinement_requested.connect(self.select_curated_refinement)

    def reload_curated_catalogue(self):
        """Catalogue activation may follow first-run bootstrap; navigation never reloads JSON."""
        self._curated_profiles = configured_curated_profiles(
            self._project_root, self._curated_resource_root, self._settings,
            warn=self._warn_curated_once)
        if self._current_item_id() is not None:
            self.refresh_local_review()

    def _warn_curated_once(self, message):
        if message not in self._curated_validation_warnings:
            self._curated_validation_warnings.add(message)
            self._log(message, level='WARNING')

    def _curated_state(self, item_id):
        return self._curated_reviews.setdefault(item_id, CuratedReviewState())

    def _curated_canonical(self, name):
        index = getattr(self, "_curated_profiles", {})
        return index.canonical(name) if hasattr(index, "canonical") else name

    def _attach_curated(self, item_id, rows, source_tags):
        state = self._curated_state(item_id)
        repository = self.image_analysis.repository
        decisions = repository.canonical_review_decisions(
            item_id, self.page.active_site, canonicalize=self._curated_canonical
        ) if hasattr(repository, 'canonical_review_decisions') else {}
        blocked = {self._curated_canonical(normalize_booru_tag(tag)) for tag in source_tags}
        blocked.update(name for name, row in rows.items()
                       if row["decision"] in {"accepted", "keep"})
        state.targets = {name: row["id"] for name, row in rows.items()}
        available = set()
        for trigger, root in rows.items():
            profile = self._curated_profiles.get(trigger)
            if profile is None:
                continue
            if "wd14_score" in root and root["decision"] == "accepted":
                # Finishing the group hides its navigation, not its provenance.
                if "WD14" not in root["match"]:
                    root["match"] = "WD14 · " + root["match"]
                for name, tag in profile.tags.items():
                    row = rows.get(name)
                    if row is not None:
                        origin = f"Curated: {trigger} ({', '.join(tag.packs)})"
                        if origin not in row["match"]:
                            row["match"] += " · " + origin
                        if "wd14_score" in row and "WD14" not in row["match"]:
                            row["match"] = "WD14 · " + row["match"]
            if ("wd14_score" not in root
                    or root["decision"] != "unreviewed" or trigger in blocked):
                state.pending.pop(trigger, None)
                continue
            pending = state.pending.setdefault(trigger, set())
            pending.difference_update(blocked)
            candidates = {name: tag for name, tag in profile.tags.items() if name not in blocked}
            pending.intersection_update(candidates)
            if not candidates:
                continue
            available.add(trigger)
            if "WD14" not in root["match"]:
                root["match"] = "WD14 · " + root["match"]
            children = {}
            for name, tag in candidates.items():
                row = rows.get(name)
                origin = f"Curated: {trigger} ({', '.join(tag.packs)})"
                if row is not None:
                    if "wd14_score" in row and "WD14" not in row["match"]:
                        row["match"] = "WD14 · " + row["match"]
                    if origin not in row["match"]:
                        row["match"] += " · " + origin
                    # The group's representation addresses the same target, keeping its WD14 score.
                    child = dict(row)
                else:
                    child = {"tag": name, "confidence": "", "match": origin,
                             "category": self.catalog.text(
                                 f"tagging.category.{category_name('gelbooru', tag.category)}"),
                             "category_id": tag.category,
                             "decision": decisions.get(name, "unreviewed")}
                child.update(id=refinement_token(trigger, name), curated_trigger=trigger,
                             pending=name in pending, post_count=tag.post_count)
                children[name] = child
            root["curated"] = {"trigger": trigger, "packs": profile.packs,
                "top": [children[tag.name] for tag in profile.top if tag.name in children],
                "groups": [{"id": group.id, "label": group.label(self.catalog.code),
                            "labels": group.labels,
                            "tags": [children[tag.name] for tag in group.tags
                                     if tag.name in children]} for group in profile.groups
                           if any(tag.name in children for tag in group.tags)]}
        for trigger in set(state.pending) - available:
            state.pending.pop(trigger, None)

    def select_curated_refinement(self, trigger, tag, selected):
        item_id = self._current_item_id()
        if item_id is None:
            return
        state = self._curated_state(item_id)
        # Only the current validated presentation authorizes a provisional selection.
        root = next((row for row in self.page._all_suggestion_rows
                     if row.get("curated", {}).get("trigger") == trigger), None)
        if (root is None or tag not in {child["tag"] for group in root["curated"]["groups"]
                                       for child in group["tags"]}):
            return
        values = state.pending.setdefault(trigger, set())
        if selected:
            values.add(tag)
        else:
            values.discard(tag)
        self.refresh_local_review()

    def _decide_curated(self, tokens, value):
        values = tokens if isinstance(tokens, list) else [tokens]
        children = [str(token) for token in values if str(token).startswith("curated:")]
        for token in children:
            trigger, name = parse_refinement_token(token)
            self.select_curated_refinement(trigger, name, value == "accepted")
        values = [token for token in values if str(token) not in children]
        item_id = self._current_item_id()
        state = self._curated_state(item_id)
        triggers = [name for name, target in state.targets.items()
                    if name in state.pending and str(target) in {str(v) for v in values}]
        if not triggers:
            return bool(children) and not values
        repository = self.image_analysis.repository
        observations = dict(repository.observations(item_id))
        # A root bundle contains observation targets only. Other selected entries are
        # handled by the established path after the atomic root bundle succeeds.
        ids = {int(state.targets[t]) for t in triggers if str(state.targets[t]).isdigit()}
        if not ids:
            return False
        pending_before = {t: set(tags) for t, tags in state.pending.items()}
        manual = set()
        if value == "accepted":
            for trigger in triggers:
                for name in sorted(state.pending[trigger]):
                    target = state.targets.get(name)
                    if target is not None and str(target).isdigit():
                        ids.add(int(target))
                    else:
                        duplicate = next((oid for oid, obs in observations.items()
                            if normalize_booru_tag(obs.reviewed_name or obs.name) == name), None)
                        if duplicate is None:
                            manual.add(name)
                        else:
                            ids.add(duplicate)
        before = tuple((oid, observations[oid].decision.value, observations[oid].reviewed_name)
                       for oid in sorted(ids))
        after = tuple((oid, value, self._curated_canonical(
                      normalize_booru_tag(observations[oid].reviewed_name or observations[oid].name)))
                      for oid, _decision, reviewed in before)
        site = self.page.active_site
        canonical_names = {name for _oid, _decision, name in after} | manual
        previous = repository.canonical_review_decisions(
            item_id, site, canonicalize=self._curated_canonical)
        aliases = getattr(self._curated_profiles, 'canonical_names', {})
        canonical_before = tuple((name, previous.get(name, 'rejected' if name in manual else 'unreviewed'),
                                  tuple(alias for alias, target in aliases.items() if target == name))
                                 for name in sorted(canonical_names))
        canonical_after = tuple((name, value, names) for name, _decision, names in canonical_before)
        new_ids = repository.apply_review_observation_bundle(
            after, sorted(manual), item_id=item_id, site=site,
            canonical_decisions=canonical_after)
        before += tuple((oid, "rejected", None) for oid in new_ids)
        after += tuple((oid, "accepted", None) for oid in new_ids)
        for trigger in triggers:
            state.pending.pop(trigger, None)
        pending_after = {t: set(tags) for t, tags in state.pending.items()}
        self._undo_stack.append(CuratedBundleOperation(item_id, before, after,
                                                      pending_before, pending_after, site,
                                                      canonical_before, canonical_after))
        self._redo_stack.clear()
        self._log(f"Curated refinement bundle {value}: {len(after)} observations", item_id=item_id)
        remaining = [token for token in values if str(token) not in {str(i) for i in ids}]
        self.refresh_local_review()
        if remaining:
            self.decide(remaining, value)
        return True

    def _apply_curated_bundle(self, operation, *, undo):
        self.image_analysis.repository.apply_review_observation_bundle(
            operation.before if undo else operation.after, (), item_id=operation.item_id,
            site=operation.site,
            canonical_decisions=operation.canonical_before if undo else operation.canonical_after)
        self._curated_state(operation.item_id).pending = {
            t: set(tags) for t, tags in
            (operation.pending_before if undo else operation.pending_after).items()}
