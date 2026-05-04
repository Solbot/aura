# tools/express.py
# Lets AURA show any Fluent Emoji on the character panel as an expressive reaction.
# The emoji is held for the duration of the next TTS utterance, then reverts to idle.

import tools
import aura_socket


def express_emotion(emoji: str) -> str:
    """Show a Fluent Emoji 3D image on the character panel."""
    if not emoji or not emoji.strip():
        return "No emoji provided."
    char = emoji.strip()
    aura_socket.send({"type": "set_expression", "emoji": char})
    return f"Expression set: {char}"


tools.register(
    name        = "express_emotion",
    description = (
        "Show any emoji on the character panel as a visual reaction — "
        "the Fluent Emoji 3D version of whatever character you pass. "
        "Use this when you genuinely react to something: surprised by news, "
        "laughing at a joke, showing a fox 🦊 when talking about foxes, "
        "a guitar 🎸 when discussing music, etc. "
        "Pass the actual Unicode emoji character (e.g. '😂', '🦊', '🎉', '🔥'). "
        "The expression is held while you speak, then reverts automatically. "
        "Do NOT narrate the emotion in words as well — show it OR say it, not both. "
        "Use sparingly; only when it genuinely adds to the moment."
    ),
    parameters  = {
        "type": "object",
        "properties": {
            "emoji": {
                "type": "string",
                "description": (
                    "A single emoji character to display (e.g. '😂', '🦊', '🎉'). "
                    "Any standard Unicode emoji works."
                ),
            }
        },
        "required": ["emoji"],
    },
    function    = express_emotion,
    permission  = tools.FREE,
)
