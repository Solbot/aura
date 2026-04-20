# memory.py
# Three-tier memory system for AURA.
#
# HOT   - Last N messages in RAM (the LLM's live context window)
# WARM  - SQLite summaries of pruned conversation chunks; searched by keyword
# COLD  - Append-only raw archive; searched as a last resort when warm has nothing
#
# Flow:
#   User speaks -> add_message() -> stored in hot + cold
#   Hot exceeds HOT_MAX -> oldest chunk summarised -> written to warm -> dropped from hot
#   On each interaction -> warm searched by keyword -> relevant summaries injected
#   If warm has no hits -> cold searched as fallback
#   Dream cycle -> flush remaining hot -> warm; consolidate warm -> profile; clear warm

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

Write in third person. Be brief - 3-6 sentences maximum.
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
    except Exception:
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
    db.cold_append(role, content, _session_id)
    _prune_if_needed()

def _prune_if_needed():
    """If hot exceeds HOT_MAX, summarise oldest chunk and move to warm."""
    non_system = [m for m in _hot if m["role"] != "system"]
    if len(non_system) <= HOT_MAX:
        return

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

    chunk_text = "\n".join([
        f"{'User' if m['role']=='user' else db.get('assistant_name')}: {m['content']}"
        for m in pruned
        if m.get("content")
    ])
    summary = _llm_summarise(chunk_text)
    if summary:
        db.warm_append(summary, len(pruned))
        print(f"[Memory: pruned {len(pruned)} messages to warm storage]")
        for i in reversed(pruned_indices):
            _hot.pop(i)

def get_context(system_prompt):
    """
    Return the full message list for the LLM:
      [system prompt] + [relevant warm/cold context if any] + [hot messages]

    Warm summaries are searched by keyword against the last user message rather
    than injected wholesale.  Cold archive is searched only when warm returns
    nothing, acting as a deeper fallback.
    """
    messages = [{"role": "system", "content": system_prompt}]

    user_msgs = [m for m in _hot if m["role"] == "user"]
    query = user_msgs[-1]["content"] if user_msgs else None

    if query:
        warm_hits = db.warm_search(query, limit=3)
    else:
        warm_hits = db.warm_get_recent(limit=2)

    if warm_hits:
        combined = " | ".join([w["summary"] for w in warm_hits])
        messages.append({
            "role":    "system",
            "content": f"[Relevant past context: {combined}]"
        })
    elif query:
        cold_hits = db.cold_search(query, limit=5)
        if cold_hits:
            cold_text = "\n".join(
                f"{r['role']}: {r['content'][:300]}" for r in reversed(cold_hits)
            )
            messages.append({
                "role":    "system",
                "content": f"[Relevant archived context:\n{cold_text}]"
            })

    messages.extend([m for m in _hot if m["role"] != "system"])
    return messages


def flush_hot_to_warm():
    """Summarise all remaining hot messages into warm storage (called by dream)."""
    non_system = [m for m in _hot if m["role"] in ("user", "assistant") and m.get("content")]
    if not non_system:
        return
    chunk_text = "\n".join([
        f"{'User' if m['role'] == 'user' else db.get('assistant_name')}: {m['content']}"
        for m in non_system
    ])
    summary = _llm_summarise(chunk_text)
    if summary:
        db.warm_append(summary, len(non_system))
    _hot.clear()


def get_hot():
    """Return current hot messages (non-system only)."""
    return [m for m in _hot if m["role"] != "system"]

def add_hot_raw(msg):
    """Append a message directly to _hot without cold-archiving or pruning.
    For tool-call scaffolding messages (content may be None)."""
    _hot.append(msg)

def pop_hot():
    """Remove the last message from _hot."""
    if _hot:
        _hot.pop()

def clear_hot():
    """Clear hot memory (call on session end if needed)."""
    _hot.clear()

def hot_count():
    """Return count of non-system messages in hot."""
    return len([m for m in _hot if m["role"] != "system"])
