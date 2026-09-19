"""Present terminal dialogs for accepting or undoing selected edit hunks."""

from prompt_toolkit.shortcuts import (
    button_dialog,
    checkboxlist_dialog,
    radiolist_dialog,
)
from rich.syntax import Syntax


def review_changes(store, console):
    """Choose an edition then exact changed blocks; stale content is refused."""
    from gemma_agents.terminal import safe_text

    choices = [(edit["id"], f"{edit['path']} · {edit['status']} · #{edit['id']}")
               for edit in store.list() if edit["status"] in {"applied", "proposed"}]
    if not choices:
        console.print("Aucune édition à examiner.")
        return None
    edit_id = radiolist_dialog(title="Éditions de cette session",
                               text="Flèches pour naviguer · Entrée pour choisir",
                               values=choices).run()
    if edit_id is None:
        return None
    edit = store.get(edit_id)
    hunks = store.hunks(edit_id)
    for hunk in hunks:
        console.print(f"Bloc {hunk['index']} · {edit['path']}", markup=False)
        console.print(Syntax(safe_text(hunk["diff"]), "diff", word_wrap=True))
    proposed = edit["status"] == "proposed"
    actions = ([("Appliquer des blocs", "accept"), ("Rejeter", "reject")]
               if proposed else [("Annuler des blocs", "undo")])
    action = button_dialog(title="Revue", text="Choisis l'action sur cette édition.",
                            buttons=[*actions, ("Conserver / retour", None)]).run()
    if action is None:
        return None
    if action == "reject":
        return store.review(edit_id, action)
    selected = checkboxlist_dialog(
        title="Blocs à traiter", text="Espace pour sélectionner · Tab puis Valider. "
        "Les blocs non sélectionnés laissent le fichier inchangé.",
        values=[(h["index"], safe_text(h["diff"])) for h in hunks]).run()
    if not selected:
        return None
    return store.review(edit_id, action, selected)
