# dream.py
# AURA's sleep/dream cycle.
# Consolidates raw profile facts AND warm conversation summaries
# into a clean canonical user profile.

import db
import requests
import json
from datetime import datetime

DREAM_PROMPT = """You are a memory consolidation assistant. You will be given:
1. Raw facts collected about a user during conversations
2. Summaries of recent conversation history

Your job is to:
1. Consolidate duplicate or related facts into single canonical entries
2. Resolve contradictions (prefer more specific/complete information)
3. Complete partial dates using duration information where possible
   - "birthday: April 10" + "age: 52" + current year 2026 = "birthday: April 10 1974"
   - "anniversary: April 23" + "years_married: 21" + current year 2026 = "anniversary: April 23 2005"
4. Standardise key names to canonical snake_case:
   - birthday, spouse_name, anniversary, birth_year, age, job_title,
     job_start, location, hobbies, children, pets, and any other relevant keys
5. Extract any additional facts mentioned in the conversation summaries
6. Remove redundant entries captured in consolidated entries
7. OMIT any entry whose value is "Not specified", "Unknown", or equivalent — do not carry forward placeholder values

Return ONLY a valid JSON object where keys are canonical fact names and values are clean strings.
Dates must be in "Month DD YYYY" format where year is known, or "Month DD" where only day/month known.
Return only the JSON object — no explanation, no markdown."""

def dream(endpoint=None):
    """
    Consolidate raw profile facts + warm summaries into clean canonical entries.
    Returns dict of consolidated facts or None on failure.
    """
    if endpoint is None:
        endpoint = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"

    # Get raw facts
    raw_facts = db.profile_get_all()
    seen = {}
    for f in raw_facts:
        seen[f['key']] = f['value']

    # Get warm summaries
    warm = db.warm_get_all()

    if not seen and not warm:
        return None

    today = datetime.now().strftime("%B %d %Y")

    prompt_parts = [f"Today's date is {today}.\n"]

    if seen:
        facts_text = "\n".join([f"{k}: {v}" for k, v in seen.items()])
        prompt_parts.append(f"Raw facts collected:\n{facts_text}\n")

    if warm:
        summaries_text = "\n".join([f"- {w['summary']}" for w in warm])
        prompt_parts.append(f"Conversation summaries:\n{summaries_text}\n")

    prompt_parts.append("Please consolidate into a clean canonical profile.")
    prompt = "\n".join(prompt_parts)

    try:
        response = requests.post(endpoint, json={
            "messages": [
                {"role": "system", "content": DREAM_PROMPT},
                {"role": "user",   "content": prompt}
            ],
            "max_tokens": 512
        }, timeout=120)

        data = response.json()
        if "choices" not in data:
            print(f"[Dream: LLM error: {str(data)[:100]}]")
            return None

        result = data["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if present
        if "```" in result:
            lines = result.split("\n")
            result = "\n".join(l for l in lines if not l.startswith("```")).strip()

        consolidated = json.loads(result)

        # Clear old dream entries so stale/placeholder facts don't persist
        with db.get_connection() as conn:
            conn.execute("DELETE FROM user_profile WHERE source = 'dream'")
            conn.commit()

        # Write fresh consolidated facts tagged as source="dream"
        for key, value in consolidated.items():
            if key and value:
                db.profile_set(key, str(value), source="dream", confidence="high")

        print(f"\r[Dream: consolidated {len(consolidated)} facts]")
        print("\nYou: ", end="", flush=True)
        return consolidated

    except json.JSONDecodeError as e:
        print(f"\r[Dream: parse error]")
        print("\nYou: ", end="", flush=True)
        return None
    except Exception as e:
        print(f"\r[Dream: error]")
        print("\nYou: ", end="", flush=True)
        return None

if __name__ == "__main__":
    db.init_db()
    print("Running dream cycle...")
    result = dream()
    if result:
        print("\nConsolidated profile:")
        for k, v in result.items():
            print(f"  {k}: {v}")
    else:
        print("Nothing to consolidate or dream failed.")
