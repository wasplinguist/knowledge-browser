from dataclasses import dataclass, field
import json
from pathlib import Path
import re


@dataclass(frozen=True, slots=True)
class SearchProfile:
    name: str
    keyword_limit: int = 20
    semantic_limit: int = 20
    rrf_k: int = 60
    keyword_weight: float = 1.0
    semantic_weight: float = 1.0
    query_expansions: dict[str, str] = field(default_factory=dict)
    embedding_model: str = "text-embedding-3-small"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("profile name must not be empty")
        for name, value in (
            ("keyword_limit", self.keyword_limit),
            ("semantic_limit", self.semantic_limit),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
                raise ValueError(f"{name} must be between 1 and 100")
        if isinstance(self.rrf_k, bool) or not isinstance(self.rrf_k, int) or self.rrf_k < 1:
            raise ValueError("rrf_k must be at least 1")
        for name, value in (
            ("keyword_weight", self.keyword_weight),
            ("semantic_weight", self.semantic_weight),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be at least 0")
        if self.keyword_weight == self.semantic_weight == 0:
            raise ValueError("at least one retrieval weight must be positive")
        if not isinstance(self.query_expansions, dict) or not all(
            isinstance(alias, str) and alias
            and isinstance(expansion, str) and expansion
            for alias, expansion in self.query_expansions.items()
        ):
            raise ValueError("query_expansions must map non-empty strings")


def load_profile(path: Path) -> SearchProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("profile JSON must be an object")
    return SearchProfile(**data)


def expand_query(query: str, profile: SearchProfile) -> str:
    if not profile.query_expansions:
        return query
    sensitive = {
        alias: replacement
        for alias, replacement in profile.query_expansions.items()
        if len(alias) <= 2 and alias.isupper()
    }
    insensitive = {
        alias.casefold(): replacement
        for alias, replacement in profile.query_expansions.items()
        if alias not in sensitive
    }
    alternatives = "|".join(
        re.escape(alias) if alias in sensitive else f"(?i:{re.escape(alias)})"
        for alias in sorted(profile.query_expansions, key=len, reverse=True)
    )
    pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w|-\d)")
    return pattern.sub(
        lambda match: sensitive.get(
            match.group(0), insensitive.get(match.group(0).casefold(), match.group(0))
        ),
        query,
    )

