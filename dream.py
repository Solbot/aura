# dream.py
# Aether's sleep/dream cycle — runs at session end to consolidate raw facts
# into a clean canonical user profile. The biological analogy is intentional:
# during sleep, the brain consolidates short-term memories into long-term ones.
#
# Architecture:
#   - During conversation: Aether stores raw facts freely (fast, no normalisation)
#   - At session end: dream() runs a single focused LLM call
#   - LLM receives all raw facts and returns a clean canonical JSON profile
#   - Clean profile overwrites raw entries in the database
#   - Raw entries are archived, not deleted

import db
import requests
import json
from datetime import datetime

DREAM_PROMPT = """You are a memory consolidation assistant. You will be given a list of raw facts
that an AI assistant has collected about a user during conversations. Your job is to:

1. Consolidate duplicate or related facts into single canonical entries
2. Resolve any contradictions (prefer more specific/complete information)
3. Complete partial dates using duration information where possible
   - "birthday: April 10" + "age: 52" + current year 2026 = "birthday: April 10 1974"
   - "anniversary: April 23" + "years_married: 21" + current year 2026 = "anniversary: April 23 2005"
4. Standardise key names to canonical snake_case forms:
   - birthday, spouse_name, anniversary, birth_year, age, job_title,
     job_start, location, hobbies, children, pets, and any other relevant keys
5. Remove clearly redundant entries that are now captured in a consolidated entry

Return ONLY a valid JSON object where keys are canonical fact names and values are clean strings.
Dates must be in "Month DD YYYY" format where year is known, or "Month DD" where only day/month known.
Do not include any explanation or other text — only the JSON object."""

def dream(endpoint=None):
    """
    Consolidate raw profile facts into clean canonical entries.
    Called at session end. Returns dict of consolidated facts or None on failure.
    """
    if endpoint is None:
        endpoint = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"

    # Get all raw facts
    raw_facts = db.profile_get_all()
    if not raw_facts:
        return None

    # Deduplicate for display — show most recent per key
    seen = {}
    for f in raw_facts:
        seen[f['key']] = f['value']

    if len(seen) == 0:
        return None

    # Build the facts summary
    facts_text = "\n".join([f"{k}: {v}" for k, v in seen.items()])
    today = datetime.now().strftime("%B %d %Y")

    prompt = (
        f"Today's date is {today}.\n\n"
        f"Raw facts collected:\n{facts_text}\n\n"
        f"Please consolidate these into a clean canonical profile."
    )

    try:
        response = requests.post(endpoint, json={
            "messages": [
                {"role": "system", "content": DREAM_PROMPT},
                {"role": "user",   "content": prompt}
            ],
            "max_tokens": 512
        }, timeout=60)

        data = response.json()
        if "choices" not in data:
            print(f"[Dream: LLM error: {str(data)[:100]}]")
            return None

        result = data["choices"][0]["message"]["content"].strip()

        # Strip markdown code fences if present
        if "```" in result:
            result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        consolidated = json.loads(result)

        # Write consolidated facts back to database
        # Mark them as source="dream" so we can distinguish them
        for key, value in consolidated.items():
            if key and value:
                db.profile_set(key, str(value), source="dream", confidence="high")

        print(f"[Dream complete: consolidated {len(consolidated)} facts]")
        return consolidated

    except json.JSONDecodeError as e:
        print(f"[Dream: failed to parse LLM response: {e}]")
        return None
    except Exception as e:
        print(f"[Dream: error: {e}]")
        return None

def dream_summary():
    """Return a readable summary of the current consolidated profile."""
    facts = db.profile_get_all()
    # Prefer dream-sourced facts
    dream_facts = {f['key']: f['value'] for f in facts if f['source'] == 'dream'}
    all_facts = {f['key']: f['value'] for f in facts}
    # Merge — dream facts take priority
    merged = {**all_facts, **dream_facts}
    if not merged:
        return "No profile information stored yet."
    return "\n".join([f"{k}: {v}" for k, v in merged.items()])

if __name__ == "__main__":
    import sys
    print("Running dream cycle...")
    db.init_db()
    result = dream()
    if result:
        print("\nConsolidated profile:")
        for k, v in result.items():
            print(f"  {k}: {v}")
    else:
        print("Nothing to consolidate or dream failed.")
