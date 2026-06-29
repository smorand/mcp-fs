"""Tree-sitter helpers for find_definition / find_references (lazy per language).

Parsers are loaded on demand through ``tree_sitter_language_pack``. That package
ships a method-style binding (``node.kind()``, ``node.named_child(i)``,
``node.byte_range().start`` ...) and no query API, so definitions are found by a
manual tree walk over a small set of definition node kinds per language, and
references by matching identifier nodes. Node text is sliced from the source
bytes using the node byte range.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = logging.getLogger(__name__)

EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".rb": "ruby",
}

# Node kinds that introduce a named definition, per language. The identifier is
# read from the node's ``name`` field, which is consistent across these grammars.
_DEFINITION_KINDS: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition", "class_definition"}),
    "javascript": frozenset({"function_declaration", "class_declaration", "method_definition", "variable_declarator"}),
    "typescript": frozenset(
        {"function_declaration", "class_declaration", "method_definition", "interface_declaration"}
    ),
    "tsx": frozenset({"function_declaration", "class_declaration", "method_definition", "interface_declaration"}),
    "go": frozenset({"function_declaration", "method_declaration", "type_spec"}),
    "rust": frozenset({"function_item", "struct_item", "enum_item", "trait_item"}),
    "java": frozenset({"method_declaration", "class_declaration", "interface_declaration"}),
    "c": frozenset({"function_definition", "struct_specifier"}),
    "cpp": frozenset({"function_definition", "class_specifier", "struct_specifier"}),
    "ruby": frozenset({"method", "class", "module"}),
}


@dataclass(slots=True)
class CodeMatch:
    """A definition or reference hit within a file."""

    path: str
    name: str
    kind: str
    line: int


def language_for(path: str) -> str | None:
    """Return the tree-sitter language id for a file path, or ``None``."""
    for extension, language in EXTENSION_LANGUAGE.items():
        if path.endswith(extension):
            return language
    return None


def find_definitions(path: str, source: bytes, name: str | None, kind: str | None) -> list[CodeMatch]:
    """Return definitions in ``source`` optionally filtered by ``name``/``kind``."""
    language = language_for(path)
    definition_kinds = _DEFINITION_KINDS.get(language or "")
    root = _parse(language, source)
    if root is None or not definition_kinds:
        return []
    matches: list[CodeMatch] = []
    for node in _walk(root):
        node_kind = node.kind()
        if node_kind not in definition_kinds:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        found = _text(source, name_node)
        if (name is None or found == name) and (kind is None or kind in node_kind):
            matches.append(CodeMatch(path=path, name=found, kind=node_kind, line=name_node.start_position().row + 1))
    return matches


def find_references(path: str, source: bytes, name: str) -> list[CodeMatch]:
    """Return identifier references to ``name`` in ``source``."""
    language = language_for(path)
    root = _parse(language, source)
    if root is None:
        return []
    matches: list[CodeMatch] = []
    for node in _walk(root):
        kind = node.kind()
        if kind.endswith("identifier") and _text(source, node) == name:
            matches.append(CodeMatch(path=path, name=name, kind=kind, line=node.start_position().row + 1))
    return matches


def _parse(language: str | None, source: bytes) -> Any | None:
    if language is None:
        return None
    parser = _load(language)
    if parser is None:
        return None
    tree = parser.parse(source.decode("utf-8", errors="replace"))
    root = tree.root_node
    return root() if callable(root) else root


def _walk(root: Any) -> Iterator[Any]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for index in range(node.named_child_count()):
            stack.append(node.named_child(index))


def _text(source: bytes, node: Any) -> str:
    span = node.byte_range()
    return source[span.start : span.end].decode("utf-8", errors="replace")


def _load(language: str) -> Any | None:
    try:
        from tree_sitter_language_pack import get_parser  # noqa: PLC0415 - lazy per language

        return get_parser(language)
    except (ImportError, LookupError) as exc:  # pragma: no cover - missing parser
        logger.warning("tree-sitter parser unavailable for %s: %s", language, exc)
        return None


def iter_supported_extensions() -> Iterable[str]:
    """Return the file extensions that have a language mapping."""
    return EXTENSION_LANGUAGE.keys()
