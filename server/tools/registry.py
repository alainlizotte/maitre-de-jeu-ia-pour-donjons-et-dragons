"""Registre des tools : auto-discovery, schéma JSON pour tool-calling natif
et résumé lisible pour mode prompt-based (Gemma).

Importer ce module déclenche l'enregistrement de tous les tools. L'attribut
`_TOOL_REGISTRY` du module `base` est rempli par effet de bord de `@tool`.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import textwrap
from typing import Any

from .base import _TOOL_REGISTRY, ToolSpec


# --------------------------------------------------------------------------- #
#  Découverte automatique : importe tous les sous-modules de server.tools
# --------------------------------------------------------------------------- #
def discover_tools(package: str = "server.tools") -> dict[str, ToolSpec]:
    """Importe tous les modules du package pour enregistrer les @tool.

    À appeler une fois au démarrage (dans main.py). Renvoie le registre
    ainsi peuplé.
    """
    pkg = importlib.import_module(package)
    pkg_path = package.replace(".", "/")
    for module_info in pkgutil.iter_modules(pkg.__path__):
        if module_info.name in ("base", "registry"):
            continue
        importlib.import_module(f"{package}.{module_info.name}")
    return _TOOL_REGISTRY


# --------------------------------------------------------------------------- #
#  Parsing de docstring -> description d'un paramètre
# --------------------------------------------------------------------------- #
_PARAM_DOC_RE = re.compile(
    r":param\s+(?P<name>\w+)\s*(?:\([^)]*\))?\s*:\s*(?P<desc>.+?)(?=\n\s*:|\Z)",
    re.DOTALL,
)
_ARG_DOC_RE = re.compile(
    r"\s+(?P<name>\w+)\s*(?:\([^)]*\))?\s*:\s*(?P<desc>.+?)(?=\n\s*[A-Za-z]|\Z)",
    re.DOTALL,
)


def _doc_summary(doc: str) -> str:
    """Premier paragraphe de la docstring : sert de description du tool."""
    if not doc:
        return ""
    cleaned = textwrap.dedent(doc).strip("\n")
    # On prend tout texte avant la première ligne Args:/Parameters:/Returns:
    stoppers = re.compile(
        r"\n^\s*(Args|Parameters|Returns|Raises|Note|Warning|:param|:return)\b",
        re.MULTILINE,
    )
    m = stoppers.search(cleaned)
    head = cleaned[:m.start()] if m else cleaned
    return " ".join(part.strip() for part in head.split("\n\n")).strip()


def _doc_param_doc(spec: ToolSpec, pname: str) -> str:
    """Récupère la description d'un param depuis la docstring, plusieurs formats."""
    doc = spec.docstring
    if not doc:
        return ""
    cleaned = textwrap.dedent(doc)
    # Sphinx/NumPy style
    for m in _PARAM_DOC_RE.finditer(cleaned):
        if m.group("name") == pname:
            return " ".join(m.group("desc").split()).strip()
    # Google style "Args:\n  name: desc"
    args_section = re.search(
        r"(?:Args|Parameters)\s*:\s*\n(?P<body>.*?)(?:\n\s{0,3}\S|\Z)",
        cleaned,
        re.DOTALL,
    )
    if args_section:
        for m in _ARG_DOC_RE.finditer(args_section.group("body")):
            if m.group("name") == pname:
                return " ".join(m.group("desc").split()).strip()
    return ""


# (Regex définie plus haut.)


# --------------------------------------------------------------------------- #
#  Schéma JSON OpenAI function-calling
# --------------------------------------------------------------------------- #
def tool_schema(spec: ToolSpec) -> dict[str, Any]:
    """Construit l'objet `tools[].function` attendu par l'API OpenAI.

    Le LLM peut alors « commander » un appel d'outil dans `tool_calls`.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, p in spec.expected_args.items():
        jtype = _param_json_type(_resolved_annotation(spec, pname))
        desc = _doc_param_doc(spec, pname)
        prop: dict[str, Any] = {"type": jtype}
        if desc:
            prop["description"] = desc
        properties[pname] = prop
        if p.default is inspect.Parameter.empty:
            required.append(pname)

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": _doc_summary(spec.docstring) or spec.name,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def tools_schemas_all(registry: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    return [tool_schema(spec) for spec in registry.values()]


def _param_json_type(ann) -> str:
    """Mappe une annotation Python résolue vers un type JSON Schema."""
    json_map = {int: "integer", float: "number", str: "string", bool: "boolean"}
    if ann in json_map:
        return json_map[ann]
    if ann is inspect.Parameter.empty:
        return "string"
    # Optional[X] / Union[X, None]
    origin = getattr(ann, "__origin__", None)
    if origin is not None:
        from typing import Union  # local pour éviter clash def de module
        if origin is Union:
            args = [a for a in getattr(ann, "__args__", []) if a is not type(None)]
            if args and args[0] in json_map:
                return json_map[args[0]]
    return "string"


def _resolved_annotation(spec, pname: str):
    """Retourne l'annotation résolue d'un param via spec.resolved_hints."""
    if pname in spec.resolved_hints:
        return spec.resolved_hints[pname]
    p = spec.expected_args.get(pname)
    return p.annotation if p else inspect.Parameter.empty


# --------------------------------------------------------------------------- #
#  Résumé lisible pour mode prompt-based (Gemma)
# --------------------------------------------------------------------------- #
def tools_prompt_section(registry: dict[str, ToolSpec]) -> str:
    """Bloc de prompt décrivant tous les tools (mode prompt-based).

    Utilisé par l'orchestrateur comme instruction système + liste concise.
   """
    lines = [
        "## Outils disponibles (D&D 3.5)",
        "",
        "Tu peux appeler un outil en émettant EXACTEMENT ce format sur une ligne",
        "dediée, et rien d'autre sur cette ligne :",
        "",
        '    <tool name="nom_du_tool" key="value" autre="123">',
        "",
        "Règles :",
        "1. Un seul tool-call par ligne, encadré par les balises. Rien d'autre",
        "   sur la ligne de l'appel.",
        "2. Les valeurs string : guillemets doubles, JSON-échappées.",
        "3. Pour un argument JSON structuré (carac, sauvegardes, etc.), passe une",
        "   chaîne JSON valide comme value.",
        "4. Le résultat te revient ensuite comme message `tool`. Continue à",
        "   narrer / appeler jusqu'à produire ta réponse au joueur.",
        "5. **JAMAIS écrire** « *(Simulation de l'appel ...)* » ou",
        "   « *(Appel de l'outil ...)* » : appelle réellement le tool via la",
        "   balise `<tool ...>` ci-dessus, sans quoi le jet est nul.",
        "",
        "Toolbox :",
    ]
    for spec in registry.values():
        args_sig = []
        for pname, p in spec.expected_args.items():
            ann = _arg_label(_resolved_annotation(spec, pname))
            required = "" if p.default is inspect.Parameter.empty else "?"
            args_sig.append(f"{pname}{required}:{ann}")
        lines.append(f"- `{spec.name}({', '.join(args_sig)})` — {_doc_summary(spec.docstring)}")
    return "\n".join(lines)


def _arg_label(ann) -> str:
    if ann is inspect.Parameter.empty:
        return "str"
    if ann in (int, float, str, bool):
        return ann.__name__
    return "str"
