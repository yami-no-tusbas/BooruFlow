from artist_by_tag_gui import App


class _Status:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


def test_undo_restores_document_and_keeps_twenty_actions():
    app = App.__new__(App)
    app.tag_organization = {"boards": {"gelbooru": {"Group": {}}}}
    app.organizer_undo_history = []
    for index in range(25):
        app._organizer_push_undo(f"action {index}")
        app.tag_organization["boards"]["gelbooru"]["Group"][str(index)] = []
    assert len(app.organizer_undo_history) == 20

    app.organizer_path = ["Group"]
    app.organizer_selected = (0, "Group", False)
    app.organizer_update_var = _Status()
    app._save_tag_organization = lambda: None
    app._organizer_render = lambda: None
    app._organizer_refresh_search = lambda: None
    app._organizer_undo()

    group = app.tag_organization["boards"]["gelbooru"]["Group"]
    assert "24" not in group
    assert "23" in group
    assert len(app.organizer_undo_history) == 19
    assert app.organizer_update_var.value == "Action annulée : action 24."
