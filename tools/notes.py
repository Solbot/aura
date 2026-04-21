# tools/notes.py
# Note-taking tools — create, edit, delete freeform notes with optional list items.

import tools
import db


def _fmt_note(note):
    lines = [f"Note id={note['id']}: {note['title']}"]
    if note.get("body"):
        lines.append(f"  {note['body']}")
    items = note.get("items", [])
    if items:
        for item in items:
            mark = "[x]" if item["checked"] else "[ ]"
            lines.append(f"  {mark} item id={item['id']}: {item['text']}")
    return "\n".join(lines)


def create_note(title, body=""):
    note_id = db.note_create(title, body)
    return f"Note created (id={note_id}): '{title}'."


def list_notes():
    notes = db.note_list()
    if not notes:
        return "No notes yet."
    lines = []
    for n in notes:
        lines.append(f"  id={n['id']}: {n['title']} (updated {n['updated_at'][:10]})")
    return "Notes:\n" + "\n".join(lines)


def get_note(note_id):
    note = db.note_get(note_id)
    if not note:
        return f"No note found with id={note_id}."
    return _fmt_note(note)


def update_note(note_id, title=None, body=None):
    if db.note_get(note_id) is None:
        return f"No note found with id={note_id}."
    db.note_update(note_id, title=title, body=body)
    parts = []
    if title is not None:
        parts.append(f"title → '{title}'")
    if body is not None:
        parts.append("body updated")
    return f"Note {note_id} updated: {', '.join(parts)}."


def delete_note(note_id):
    if db.note_get(note_id) is None:
        return f"No note found with id={note_id}."
    db.note_delete(note_id)
    return f"Note {note_id} deleted."


def add_list_item(note_id, text):
    if db.note_get(note_id) is None:
        return f"No note found with id={note_id}."
    item_id = db.note_item_add(note_id, text)
    return f"Item added (item id={item_id}) to note {note_id}: '{text}'."


def update_list_item(item_id, text=None, checked=None):
    db.note_item_update(item_id, text=text, checked=checked)
    parts = []
    if text is not None:
        parts.append(f"text → '{text}'")
    if checked is not None:
        parts.append("checked" if checked else "unchecked")
    return f"Item {item_id} updated: {', '.join(parts)}." if parts else f"Item {item_id} unchanged."


def remove_list_item(item_id):
    db.note_item_delete(item_id)
    return f"Item {item_id} removed."


# ---------------------------------------------------------------------------
# Self-register
# ---------------------------------------------------------------------------

tools.register(
    name="create_note",
    description="Create a new note with a title and optional freeform body text.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title of the note."},
            "body":  {"type": "string", "description": "Optional freeform text body."},
        },
        "required": ["title"],
    },
    function=lambda title, body="": create_note(title, body),
    permission=tools.FREE,
)

tools.register(
    name="list_notes",
    description="List all notes with their IDs and titles.",
    parameters={"type": "object", "properties": {}, "required": []},
    function=lambda: list_notes(),
    permission=tools.FREE,
)

tools.register(
    name="get_note",
    description="Get the full content of a note including its body and list items.",
    parameters={
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "ID of the note to retrieve."},
        },
        "required": ["note_id"],
    },
    function=lambda note_id: get_note(note_id),
    permission=tools.FREE,
)

tools.register(
    name="update_note",
    description="Update a note's title and/or body text. Omit a field to leave it unchanged.",
    parameters={
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "ID of the note to update."},
            "title":   {"type": "string",  "description": "New title (omit to keep existing)."},
            "body":    {"type": "string",  "description": "New body text (omit to keep existing)."},
        },
        "required": ["note_id"],
    },
    function=lambda note_id, title=None, body=None: update_note(note_id, title=title, body=body),
    permission=tools.FREE,
)

tools.register(
    name="delete_note",
    description="Delete a note and all its list items permanently.",
    parameters={
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "ID of the note to delete."},
        },
        "required": ["note_id"],
    },
    function=lambda note_id: delete_note(note_id),
    permission=tools.CONFIRM,
)

tools.register(
    name="add_list_item",
    description="Add a list item to a note.",
    parameters={
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "ID of the note to add the item to."},
            "text":    {"type": "string",  "description": "Text of the list item."},
        },
        "required": ["note_id", "text"],
    },
    function=lambda note_id, text: add_list_item(note_id, text),
    permission=tools.FREE,
)

tools.register(
    name="update_list_item",
    description=(
        "Edit a list item's text and/or toggle its checked state. "
        "Omit fields to leave them unchanged."
    ),
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "integer", "description": "ID of the list item."},
            "text":    {"type": "string",  "description": "New text (omit to keep existing)."},
            "checked": {"type": "boolean", "description": "True to check, false to uncheck."},
        },
        "required": ["item_id"],
    },
    function=lambda item_id, text=None, checked=None: update_list_item(item_id, text=text, checked=checked),
    permission=tools.FREE,
)

tools.register(
    name="remove_list_item",
    description="Remove a specific list item from a note.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "integer", "description": "ID of the list item to remove."},
        },
        "required": ["item_id"],
    },
    function=lambda item_id: remove_list_item(item_id),
    permission=tools.FREE,
)
