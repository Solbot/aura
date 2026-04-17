# memory.py
# Three-tier memory system for AURA.
#
# HOT   â Last N messages in RAM (the LLM's context window)
# WARM  â SQLite summaries of pruned conversation chunks
# COLD  â Append-only raw archive of every message ever sent
#
# Flow:
#   User speaks â add_message() â stored in hot + cold
#   Hot exceeds HOT_MAX â oldest chunk summarised â written to warm â dropped from hot
#   On startup â warm summaries injected between system prompt and recent messages
#   Dream cycle â consolidates warm + profile facts into clean profile

import db
import requests
import json
from datetime import datetime

HOT_MAX    = 20   # Max messages in context window
CHUNK_SIZE = 10   # Messages to summarise when pruning

SUMMARISE_PROMPT = """You are a conversation summariser. Given a sequence of conversation messages,
produce a concise factual summary of what was discussed. Focus on:
- Facts shared about the user (name, family, dates, preferences, health, work)
- Topics discussed
- Decisions or plans made
- Anything the assistant committed to remembering

Write in third person. Be brief â 3-6 sentences maximum.
Return only the summary, no preamble."""

def _get_endpoint():
    return db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"

def _llm_summarise(messages_text):
    """Call LLM to summarise a chunk of conversation."""
    try:
        r = requests.post(_get_endpoint(), json={
            "messages": [
                {"role": "system", "content": SUMMARISE_PROMPT},
                {"role": "user",   "content": messages_text}
            ],
            "max_tokens": 200
        }, timeout=60)
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"\r[Memory: summarise error]")
        print("\nYou: ", end="", flush=True)
    return None

# --- In-memory hot tier ---
_hot = []          # List of {"role": ..., "content": ...}
_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

def add_message(role, content):
    """Add a message to hot memory and archive to cold."""
    if content is None:
        return
    msg = {"role": role, "content": content}
    _hot.append(msg)
    # Archive to cold immediately
    db.cold_append(role, content, _session_id)
    # Prune if over limit
    _prune_if_needed()

def _prune_if_needed():
    """If hot exceeds HOT_MAX, summarise oldest chunk and move to warm."""
    # Only count user/assistant messages, not system
    non_system = [m for m in _hot if m["role"] != "system"]
    if len(non_system) <= HOT_MAX:
        return

    # Find the oldest CHUNK_SIZE non-system messages to prune
    pruned = []
    pruned_indices = []
    for i, m in enumerate(_hot):
        if m["role"] in ("user", "assistant"):
            pruned.append(m)
            pruned_indices.append(i)
        if len(pruned) >= CHUNK_SIZE:
            break

    if not pruned:
        return

    # Summarise the chunk
    chunk_text = "\n".join([
        f"{'User' if m['role']=='user' else db.get('assistant_name')}: {m['content']}"
        for m in pruned
    ])
    summary = _llm_summarise(chunk_text)
    if summary:
        db.warm_append(summary, len(pruned))
        print(f"[Memory: pruned {len(pruned)} messages to warm storage]")

    # Remove pruned messages from hot (remove in reverse order to preserve indices)
    for i in reversed(pruned_indices):
        _hot.pop(i)

def get_context(system_prompt):
    """
    Return the full message list for the LLM:
    [system prompt] + [warm summary injection if any] + [hot messages]
    """
    messages = [{"role": "system", "content": system_prompt}]

    # Inject warm summaries if they exist
    warm = db.warm_get_recent(limit=5)
    if warm:
        combined = " | ".join([w["summary"] for w in reversed(warm)])
        messages.append({
            "role":    "system",
            "content": f"[Earlier in our conversation: {combined}]"
        })

    # Add hot messages (skip any system messages â already have system prompt)
    messages.extend([m for m in _hot if m["role"] != "system"])
    return messages

def get_hot():
    """Return current hot messages (non-system only)."""
    return [m for m in _hot if m["role"] != "system"]

def clear_hot():
    """Clear hot memory (call on session end if needed)."""
    _hot.clear()

def hot_count():
    """Return count of non-system messages in hot."""
    return len([m for m in _hot if m["role"] != "system"])
