# tools/knowledge.py
# LLM tools for searching and managing the personal knowledge base.

import tools
import db
import knowledge as _knowledge


def knowledge_search(query):
    """
    Search the personal knowledge base for information from uploaded documents.
    Returns relevant text chunks with their source document names.
    """
    results = _knowledge.search(query, limit=5)
    if not results:
        return (
            "No relevant information found in the knowledge base for that query. "
            "The knowledge base may be empty or this topic may not be covered. "
            "Use list_knowledge_docs to see what documents are available."
        )
    parts = [f'Knowledge base results for "{query}":\n']
    for r in results:
        parts.append(f"[Source: {r['filename']} — chunk {r['chunk_index']}]\n{r['content']}")
    return "\n\n---\n\n".join(parts)


def list_knowledge_docs():
    """List all documents stored in the personal knowledge base."""
    docs = db.knowledge_list_docs()
    if not docs:
        return (
            "The knowledge base is empty. No documents have been imported yet. "
            "Documents can be added by placing files in ~/knowledge/upload or "
            "using the 📚 button in the AURA interface."
        )
    lines = [f"Knowledge base — {len(docs)} document(s):"]
    for d in docs:
        added = d["added_at"][:10]
        lines.append(f"  • {d['filename']}  ({d['chunk_count']} chunks, added {added})")
    return "\n".join(lines)


tools.register(
    name="knowledge_search",
    description=(
        "Search the personal knowledge base for information from documents the user has "
        "uploaded (PDFs, Word docs, text files, etc.). Use this when the user asks about "
        "something that might be in their personal documents, reference materials, or any "
        "content they have imported. Also use when the user explicitly asks you to check "
        "their knowledge base or documents. Returns relevant text excerpts with source names."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search terms or a natural-language question to find relevant document content.",
            }
        },
        "required": ["query"],
    },
    function=knowledge_search,
    permission=tools.FREE,
)

tools.register(
    name="list_knowledge_docs",
    description=(
        "List all documents currently stored in the personal knowledge base. "
        "Use this when the user asks what documents are available, what has been imported, "
        "or before searching to confirm relevant documents exist."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    function=list_knowledge_docs,
    permission=tools.FREE,
)
