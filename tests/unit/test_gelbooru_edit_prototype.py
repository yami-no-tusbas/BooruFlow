from booruflow.infrastructure.gelbooru_edit_prototype import (
    EDIT_WORKFLOW_CLICK_EDIT_SCRIPT,
    EDIT_WORKFLOW_STATE_SCRIPT,
    apply_real_form_deltas,
    build_apply_real_form_deltas_script,
)


def test_removal_is_exact_token_not_a_substring():
    preview = apply_real_form_deltas("cat cat_girl bobcat", (), ("cat",))
    assert preview.result == ("cat_girl", "bobcat")
    assert preview.removals_absent and preview.unrelated_preserved


def test_external_tags_and_existing_order_are_preserved_when_deltas_apply():
    preview = apply_real_form_deltas("a b external_x", ("d",), ("b",))
    assert preview.result == ("a", "external_x", "d")
    assert preview.additions_present and preview.removals_absent and preview.unrelated_preserved


def test_deltas_deduplicate_exact_tokens_without_reordering_existing_tags():
    preview = apply_real_form_deltas("a a B external_x", ("b", "d", "d"), ())
    assert preview.result == ("a", "b", "external_x", "d")


def test_real_edit_scripts_scope_the_hidden_form_and_never_submit_automatically():
    script = build_apply_real_form_deltas_script(("d",), ("b",))
    assert "document.getElementById('edit_form')" in EDIT_WORKFLOW_STATE_SCRIPT
    assert "edit.click()" in EDIT_WORKFLOW_CLICK_EDIT_SCRIPT
    assert "form.style.display" not in EDIT_WORKFLOW_CLICK_EDIT_SCRIPT
    assert "textarea#tags[name=\"tags\"]" in script
    assert "tagsSearched" not in script and "csrf-token" not in script
    assert "requestSubmit" not in script and ".submit(" not in script and ".click(" not in script
    assert "new Event('input', {bubbles: true})" in script
