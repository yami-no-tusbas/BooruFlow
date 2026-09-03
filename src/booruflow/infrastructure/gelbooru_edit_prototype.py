"""Visible, no-submit prototype for Gelbooru's real Edit workflow."""
from __future__ import annotations

import json
from dataclasses import dataclass

from booruflow.application.tagging import normalize_booru_tag


def _tokens(value: str) -> list[str]:
    return [normalize_booru_tag(token) for token in str(value).split() if token.strip()]


@dataclass(frozen=True, slots=True)
class EditDeltaPreview:
    current: tuple[str, ...]
    result: tuple[str, ...]
    additions_present: bool
    removals_absent: bool
    unrelated_preserved: bool


def apply_real_form_deltas(
    current_value: str, additions: tuple[str, ...], removals: tuple[str, ...],
) -> EditDeltaPreview:
    """Apply exact normalized tag tokens, retaining source order and external tags."""
    current = _tokens(current_value)
    removal_keys = set(_tokens(" ".join(removals)))
    addition_tokens = _tokens(" ".join(additions))
    kept: list[str] = []
    seen: set[str] = set()
    for tag in current:
        if tag not in removal_keys and tag not in seen:
            kept.append(tag); seen.add(tag)
    for tag in addition_tokens:
        if tag not in seen:
            kept.append(tag); seen.add(tag)
    unrelated = {tag for tag in current if tag not in removal_keys}
    result = tuple(kept)
    return EditDeltaPreview(
        current=tuple(current), result=result,
        additions_present=set(addition_tokens).issubset(result),
        removals_absent=not bool(set(result) & removal_keys),
        unrelated_preserved=unrelated.issubset(result),
    )


EDIT_WORKFLOW_STATE_SCRIPT = r"""(() => {
    const form = document.getElementById('edit_form');
    const visible = node => Boolean(node) && getComputedStyle(node).display !== 'none'
        && getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length > 0;
    const tags = form && form.querySelector('textarea#tags[name="tags"]');
    const save = form && form.querySelector('input[type="submit"][name="submit"][value="Save changes"]');
    const id = form && form.elements.namedItem('id');
    const expected = new URL(location.href).searchParams.get('id');
    return JSON.stringify({
        editFormExists: Boolean(form), editFormVisible: visible(form),
        tagsFieldPresent: Boolean(tags), tagsFieldDisabled: Boolean(tags && tags.disabled),
        tagsFieldReadonly: Boolean(tags && tags.readOnly), savePresent: Boolean(save),
        saveDisabled: Boolean(save && save.disabled),
        postIdMatches: Boolean(id) && String(id.value) === String(expected),
        tagCount: tags ? tags.value.trim().split(/\s+/).filter(Boolean).length : 0
    });
})()"""

EDIT_WORKFLOW_CLICK_EDIT_SCRIPT = r"""(() => {
    const form = document.getElementById('edit_form');
    const visible = node => Boolean(node) && getComputedStyle(node).display !== 'none'
        && getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length > 0;
    if (!form || visible(form)) return JSON.stringify({status: form ? 'already_visible' : 'form_missing'});
    const controls = Array.from(document.querySelectorAll('a, button, input[type="button"]'));
    const edit = controls.find(node => {
        const label = String(node.value || node.textContent || node.getAttribute('title') || '').trim();
        return visible(node) && /^edit(?:\s|$)/i.test(label) && !form.contains(node);
    });
    if (!edit) return JSON.stringify({status: 'edit_control_missing'});
    edit.click();
    return JSON.stringify({status: 'edit_clicked', control: String(edit.id || edit.className || edit.tagName).slice(0, 80)});
})()"""


def build_apply_real_form_deltas_script(
    additions: tuple[str, ...], removals: tuple[str, ...],
) -> str:
    """Prepare only the visible real textarea; this never submits or clicks Save."""
    return r"""((additions, removals) => {
        const form = document.getElementById('edit_form');
        const field = form && form.querySelector('textarea#tags[name="tags"]');
        const save = form && form.querySelector('input[type="submit"][name="submit"][value="Save changes"]');
        if (!form || !field) return JSON.stringify({status: 'tags_missing'});
        if (field.disabled || field.readOnly || !save || save.disabled) return JSON.stringify({status: 'not_writable'});
        const norm = value => String(value).trim().toLowerCase().split(/\s+/).join('_');
        const current = field.value.split(/\s+/).filter(Boolean).map(norm);
        const removalKeys = new Set(removals.map(norm));
        const result = []; const seen = new Set();
        for (const tag of current) if (!removalKeys.has(tag) && !seen.has(tag)) { result.push(tag); seen.add(tag); }
        for (const raw of additions) { const tag = norm(raw); if (tag && !seen.has(tag)) { result.push(tag); seen.add(tag); } }
        const unrelated = current.filter(tag => !removalKeys.has(tag));
        const additionsPresent = additions.map(norm).every(tag => !tag || seen.has(tag));
        const removalsAbsent = !result.some(tag => removalKeys.has(tag));
        const unrelatedPreserved = unrelated.every(tag => seen.has(tag));
        if (!additionsPresent || !removalsAbsent || !unrelatedPreserved) return JSON.stringify({status: 'invariant_failed'});
        field.value = result.join(' ');
        field.dispatchEvent(new Event('input', {bubbles: true}));
        field.dispatchEvent(new Event('change', {bubbles: true}));
        return JSON.stringify({status: 'prepared', tagCount: result.length, additionsPresent, removalsAbsent, unrelatedPreserved, saveDisabled: save.disabled});
    })(__ADDITIONS__, __REMOVALS__)""".replace("__ADDITIONS__", json.dumps(list(additions))).replace(
        "__REMOVALS__", json.dumps(list(removals))
    )
