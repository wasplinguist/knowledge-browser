import json
import os
import re
import time
from typing import Any, Callable

from .profiles import SearchProfile
from .search import hybrid_search, read_chunk_context


MODE_BUDGETS = {"fast": (3, 4), "deep": (12, 24)}
DEFAULT_MODEL = "gpt-4.1-mini"
SOURCES = ("confluence", "github", "jira", "slack")
TOOLS = [
    {
        "type": "function",
        "strict": True,
        "name": "hybrid_search",
        "description": "Search allowed evidence with the same hybrid pipeline as the result list.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source": {"type": ["string", "null"], "enum": [*SOURCES, None]},
            },
            "required": ["query", "source"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "strict": True,
        "name": "read_chunk",
        "description": "Open an allowed chunk and related document context before citing it.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": list(SOURCES)},
                "chunk_id": {"type": "string"},
            },
            "required": ["source", "chunk_id"],
            "additionalProperties": False,
        },
    },
]

ANSWER_TEXT_CONFIG = {
    "format": {
        "type": "json_schema",
        "name": "knowledge_browser_answer",
        "description": "A grounded answer with separate evidence metadata.",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "evidence_status": {
                    "type": "string",
                    "enum": ["complete", "incomplete", "conflicting"],
                },
                "citations": {"type": "array", "items": {"type": "string"}},
                "conflicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "citations": {
                                "type": "array", "items": {"type": "string"}
                            },
                        },
                        "required": ["description", "citations"],
                        "additionalProperties": False,
                    },
                },
                "missing_information": {
                    "type": "array", "items": {"type": "string"}
                },
                "follow_ups": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "answer", "evidence_status", "citations", "conflicts",
                "missing_information", "follow_ups",
            ],
            "additionalProperties": False,
        },
    }
}


class AnswerExecutionError(RuntimeError):
    def __init__(self, execution: dict[str, Any], trace: list[dict[str, Any]]):
        super().__init__("answer generation failed")
        self.execution = execution
        self.trace = trace


def route_mode(question: str, requested_mode: str = "auto") -> str:
    if requested_mode in MODE_BUDGETS:
        return requested_mode
    text = question.casefold()
    source_count = sum(source in text for source in SOURCES)
    complex_signal = bool(re.search(
        r"\b(latest|recent|before|after|during|between|timeline|compare|conflict)\b"
        r"|\b(find|list|show)\s+(all|every)\b|\b(all|every)\s+\w+",
        text,
    ))
    clauses = len(re.findall(r"\b(who|what|when|where|why|how)\b", text)) >= 2
    return "deep" if source_count >= 2 or complex_signal or clauses else "fast"


def _calls(response: Any) -> list[Any]:
    return [
        item for item in getattr(response, "output", [])
        if getattr(item, "type", None) == "function_call"
    ]


def _citation(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        key: chunk.get(key)
        for key in (
            "chunk_id", "external_id", "matched_external_id", "title", "source",
            "url", "container", "field", "excerpt", "author", "matched_author",
            "created_at", "updated_at", "matched_created_at", "matched_updated_at",
        )
    }


def _citation_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def answer_question(
    conn,
    user_id: str,
    question: str,
    embed: Callable[[str], list[float] | None],
    client: Any,
    source: str | None = None,
    mode: str = "auto",
    include_trace: bool = False,
    profile: SearchProfile | None = None,
) -> dict[str, Any]:
    profile = profile or SearchProfile(name="released")
    model = os.environ.get("ANSWER_MODEL", DEFAULT_MODEL)
    selected_mode = route_mode(question, mode)
    max_tool_calls, max_reads = MODE_BUDGETS[selected_mode]
    started_at = time.perf_counter()
    trace: list[dict[str, Any]] = []
    llm_loops = tool_calls = 0
    opened: dict[tuple[str, str], dict[str, Any]] = {}
    discovered: dict[tuple[str, str], str | None] = {}

    def remember(results: list[dict[str, Any]]) -> None:
        for item in results:
            source_name = item.get("source")
            chunk_id = item.get("chunk_id")
            if not isinstance(source_name, str) or not isinstance(chunk_id, str):
                continue
            for external_id in (
                item.get("external_id"), item.get("matched_external_id")
            ):
                if not isinstance(external_id, str):
                    continue
                key = (source_name, external_id)
                previous = discovered.get(key, chunk_id)
                discovered[key] = chunk_id if previous == chunk_id else None

    try:
        embedding = embed(question)
    except Exception:
        embedding = None
    initial_results = hybrid_search(
        conn, user_id, question, embedding, source, profile
    )
    found_results = bool(initial_results)
    remember(initial_results)
    instructions = (
        "Use the initial allowed hybrid results first. Call hybrid_search again "
        "only when they do not contain enough evidence. Use only opened chunks "
        "for company facts. Read a chunk before citing it. "
        "If evidence is missing, say incomplete. If opened evidence disagrees, "
        "say conflicting and cite both sides. Keep the answer concise and readable. "
        "Put [1], [2], and so on after supported claims in citation order. Never "
        "put raw chunk IDs or evidence field names in the answer text. In the "
        "structured citations field, return only exact chunk_id values from "
        "successfully opened chunks, never URLs or external IDs. Return the "
        "remaining evidence data only in its structured fields."
    )

    def execution() -> dict[str, Any]:
        return {
            "mode": selected_mode,
            "model": model,
            "llm_loops": llm_loops,
            "tool_calls": tool_calls,
            "opened_chunks": len(opened),
            "latency_ms": max(1, int((time.perf_counter() - started_at) * 1000)),
        }

    def create_response(**request):
        nonlocal llm_loops
        llm_loops += 1
        request.setdefault("text", ANSWER_TEXT_CONFIG)
        try:
            return client.responses.create(**request)
        except Exception as error:
            raise AnswerExecutionError(execution(), trace) from error

    response = create_response(
        model=model,
        input=[{
            "role": "user",
            "content": question + "\n\nInitial allowed hybrid results:\n" + json.dumps(
                initial_results, default=str
            ),
        }],
        tools=TOOLS,
        tool_choice={"type": "function", "name": "read_chunk"}
        if initial_results else "auto",
        instructions=instructions,
        parallel_tool_calls=False,
    )
    while _calls(response) and tool_calls < max_tool_calls:
        outputs = []
        for call in _calls(response):
            if tool_calls >= max_tool_calls:
                break
            tool_calls += 1
            before = len(opened)
            try:
                arguments = json.loads(call.arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = None
            if not isinstance(arguments, dict):
                trace.append({
                    "step": tool_calls,
                    "llm_loop": llm_loops,
                    "tool": call.name,
                    "source": source,
                    "status": "failed",
                    "result_count": 0,
                    "new_chunks": 0,
                })
                raise AnswerExecutionError(execution(), trace)
            requested_source = source or arguments.get("source")
            try:
                if call.name == "read_chunk" and (
                    requested_source in SOURCES
                    and isinstance(arguments.get("chunk_id"), str)
                    and len(opened) < max_reads
                ):
                    requested_id = arguments["chunk_id"]
                    result = read_chunk_context(
                        conn, user_id, requested_source, requested_id,
                        max_reads - len(opened),
                    )
                    if not result:
                        resolved_id = discovered.get((requested_source, requested_id))
                        if resolved_id:
                            result = read_chunk_context(
                                conn, user_id, requested_source, resolved_id,
                                max_reads - len(opened),
                            )
                    if result:
                        for chunk in result:
                            opened[(chunk["source"], chunk["chunk_id"])] = chunk
                elif call.name == "hybrid_search" and isinstance(arguments.get("query"), str):
                    query = arguments["query"].strip()
                    try:
                        query_embedding = embed(query)
                    except Exception:
                        query_embedding = None
                    result = hybrid_search(
                        conn, user_id, query, query_embedding, requested_source, profile
                    ) if query else []
                    found_results = found_results or bool(result)
                    remember(result)
                else:
                    result = {"error": "invalid arguments or tool limit reached"}
            except Exception as error:
                trace.append({
                    "step": tool_calls,
                    "llm_loop": llm_loops,
                    "tool": call.name,
                    "source": requested_source,
                    "status": "failed",
                    "result_count": 0,
                    "new_chunks": len(opened) - before,
                })
                raise AnswerExecutionError(execution(), trace) from error
            trace.append({
                "step": tool_calls,
                "llm_loop": llm_loops,
                "tool": call.name,
                "source": requested_source,
                "status": "success" if result else "empty",
                "result_count": len(result) if isinstance(result, list) else int(bool(result)),
                "new_chunks": len(opened) - before,
            })
            outputs.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, default=str),
            })
        limited = tool_calls >= max_tool_calls or len(opened) >= max_reads
        response = create_response(
            model=model,
            input=outputs,
            previous_response_id=getattr(response, "id", None),
            tools=[] if limited else TOOLS,
            tool_choice="none" if limited else (
                {"type": "function", "name": "read_chunk"}
                if found_results and not opened else "auto"
            ),
            instructions=instructions,
            parallel_tool_calls=False,
        )
        if limited:
            break

    raw = getattr(response, "output_text", "") or ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise AnswerExecutionError(execution(), trace)
    if not isinstance(payload, dict):
        raise AnswerExecutionError(execution(), trace)

    citeable: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for chunk in opened.values():
        chunk_id = chunk["chunk_id"]
        if chunk_id in citeable and citeable[chunk_id]["source"] != chunk["source"]:
            citeable.pop(chunk_id)
            ambiguous.add(chunk_id)
        elif chunk_id not in ambiguous:
            citeable[chunk_id] = chunk
    aliases: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for chunk_id, chunk in citeable.items():
        for alias in (
            chunk.get("external_id"), chunk.get("matched_external_id"),
            chunk.get("url"),
        ):
            if not isinstance(alias, str) or alias in ambiguous_aliases:
                continue
            if alias in aliases and aliases[alias] != chunk_id:
                aliases.pop(alias)
                ambiguous_aliases.add(alias)
            else:
                aliases[alias] = chunk_id

    def resolve_citation_id(value: str) -> str | None:
        return value if value in citeable else aliases.get(value)

    citation_ids = []
    for value in _citation_ids(payload.get("citations")):
        resolved = resolve_citation_id(value)
        if resolved and resolved not in citation_ids:
            citation_ids.append(resolved)
    citations = [
        _citation(citeable[chunk_id]) for chunk_id in citation_ids
    ]
    if found_results and payload.get("answer") and not citations:
        raise AnswerExecutionError(execution(), trace)
    conflicts = []
    for conflict in payload.get("conflicts", []) if isinstance(payload.get("conflicts"), list) else []:
        if not isinstance(conflict, dict):
            continue
        ids = list(dict.fromkeys(
            resolved for value in _citation_ids(conflict.get("citations"))
            if (resolved := resolve_citation_id(value))
        ))
        if len(ids) >= 2:
            conflicts.append({
                "description": str(conflict.get("description", "Evidence disagrees")),
                "citations": ids,
            })
    evidence_status = payload.get("evidence_status", "incomplete")
    if evidence_status not in {"complete", "incomplete", "conflicting"}:
        evidence_status = "incomplete"
    if conflicts:
        evidence_status = "conflicting"
    elif evidence_status == "conflicting" or evidence_status == "complete" and not citations:
        evidence_status = "incomplete"
    result = {
        "answer": payload.get("answer", raw),
        "mode": selected_mode,
        "evidence_status": evidence_status,
        "citations": citations,
        "conflicts": conflicts,
        "missing_information": payload.get("missing_information", []) if isinstance(payload.get("missing_information", []), list) else [],
        "follow_ups": payload.get("follow_ups", []) if isinstance(payload.get("follow_ups", []), list) else [],
        "execution": execution(),
    }
    if include_trace:
        result["trace"] = trace
    return result
