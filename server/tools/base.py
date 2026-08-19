"""Base des tools D&D 3.5 : décorateur + contexte + résultat structuré.

Adaptation du pattern OpenWebUI (`class Tools` + méthodes publiques) vers des
fonctions Python isolées, callable par l'orchestrateur de function-calling.
Plus de pydantic Valves, plus d'`__event_emitter__` : un tool reçoit un
`ToolContext` (partie_id, joueur courant, data_dir) et renvoie un
`ToolResult` (texte Markdown + éventuels events à émettre aux clients).
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import re
import typing
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Union


# --------------------------------------------------------------------------- #
#  Contexte passé à chaque tool
# --------------------------------------------------------------------------- #
@dataclass
class ToolContext:
    """Au runtime, l'orchestrateur passe ce contexte à chaque appel d'outil.

    Il porte l'identité de la partie (pour la persistance), le joueur à
    l'origine de l'action (pour le marquage multijoueur) et le `data_dir`
    (équivalent de l'ancienne Valve `data_dir`).
    """

    partie_id: str = "partie_defaut"
    joueur: str = ""
    data_dir: str = "./server/data"
    # Callback temps-réel optionnel — si l'orchestrateur le peuple avant
    # l'appel d'un tool, le tool peut émettre des events en live (ex :
    # « ⏳ Génération d'image en cours » pendant qu'on attend ComfyUI),
    # sans attendre la fin du ToolResult. La signature est :
    #   async def callback(event: dict[str, Any]) -> None
    # `event` typiquement : {"type": "image_pending", "usage": "monstre", ...}
    on_event: Any = None  # Optional[Callable[[dict], Awaitable[None]]]


# --------------------------------------------------------------------------- #
#  Résultat produit par un tool
# --------------------------------------------------------------------------- #
@dataclass
class ToolResult:
    """Résultat structuré d'un tool.

    - `text`        : texte renvoyé au LLM (Markdown, comme avant).
    - `events`      : events à émettre aux clients connectés en WebSocket
                      (images monstre, cartes donjon, etc.) — l'orchestrateur
                      les relaie à la session.
    - `state_patch` : patch optionnel à appliquer à l'état de la partie après
                      exécution (utile pour synchro UI). `None` = sans effet.
    """

    text: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    state_patch: Optional[dict[str, Any]] = None

    def __str__(self) -> str:
        return self.text


# --------------------------------------------------------------------------- #
#  Décorateur @tool
# --------------------------------------------------------------------------- #
_TOOL_REGISTRY: dict[str, "ToolSpec"] = {}


@dataclass
class ToolSpec:
    name: str
    func: Callable[..., Union[ToolResult, Awaitable[ToolResult]]]
    signature: inspect.Signature
    docstring: str
    is_async: bool

    # Args attendus (hors `ctx`), avec leur type Python et leur doc par param.
    expected_args: dict[str, inspect.Parameter] = field(default_factory=dict)
    # Annotations résolues via typing.get_type_hints — utilisables même si
    # le tool a `from __future__ import annotations` (int apparaît alors comme
    # la chaîne "int" sans cette résolution).  Vide si la résolution échoue.
    resolved_hints: dict[str, Any] = field(default_factory=dict)


def tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Décore une fonction pour qu'elle devienne un tool enregistré.

    La fonction doit accepter un `ToolContext` en premier paramètre (`ctx`)
    et renvoyer un `ToolResult`. La signature inspectée sert à générer le
    schéma JSON envoyé au LLM (function-calling natif).
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    if not params or params[0].name != "ctx":
        raise TypeError(
            f"@tool '{func.__name__}' doit accepter un premier paramètre "
            f"nommé 'ctx: ToolContext'."
        )

    # Args exposés au LLM = toute la signature, sauf `ctx`.
    expected = {
        p.name: p
        for p in params[1:]
        if p.name not in ("_user_", "_event_emitter_")
    }

    # Résoudre les annotations forwardées (from __future__ import annotations).
    resolved: dict[str, Any] = {}
    try:
        hints = typing.get_type_hints(func, include_extras=True)
        resolved = {
            pname: hints[pname]
            for pname in expected
            if pname in hints
        }
    except Exception:
        # Si la résolution échoue (ForwardRef non résolu etc.), on retombe
        # sur les annotations brutes (chances de mal fonctionner).
        resolved = {}

    doc = (func.__doc__ or "").strip()
    spec = ToolSpec(
        name=func.__name__,
        func=func,
        signature=sig,
        docstring=doc,
        is_async=asyncio.iscoroutinefunction(func),
        expected_args=expected,
        resolved_hints=resolved,
    )
    _TOOL_REGISTRY[func.__name__] = spec
    return func


# --------------------------------------------------------------------------- #
#  Méta : typage Python -> JSON Schema (OpenAI function calling)
# --------------------------------------------------------------------------- #
def _py_type_to_json(param: inspect.Parameter) -> dict[str, Any]:
    """Mappe les annotations Python vers un item du schéma JSON.

    Convention : entiers et floats -> "integer"/"number", str -> "string",
    bool -> "boolean", Optional[T] -> type originel + nullable. List/dict
    (chaîne JSON à parser côté tool) -> "string" avec note de parsing.
    """
    ann = param.annotation
    schema: dict[str, Any] = {"type": "string"}  # défaut sûr
    desc = _extract_param_doc(param)
    if desc:
        schema["description"] = desc

    json_map = {
        int: "integer",
        float: "number",
        str: "string",
        bool: "boolean",
    }
    if ann in json_map:
        schema["type"] = json_map[ann]
    elif ann is inspect.Parameter.empty:
        schema["type"] = "string"
    # Optional[X] / Union[X, None] -> on garde la même type
    elif hasattr(ann, "__origin__") and ann.__origin__ is Union:
        args = [a for a in getattr(ann, "__args__", []) if a is not type(None)]
        if args and args[0] in json_map:
            schema["type"] = json_map[args[0]]
    # Pour les structures complexes recues en JSON string (dict/list), on
    # reste sur "string" — le tool parse lui-même json.loads().
    return schema


_PARAM_DOC_RE = re.compile(
    r":param\s+(?P<name>\w+)\s*(?:\([^)]*\))?\s*:\s*(?P<desc>.+?)(?=\n\s*:|\Z)",
    re.DOTALL,
)


def _extract_param_doc(param: inspect.Parameter) -> str:
    """Extrait la description d'un paramètre depuis la docstring Sphinx/NumPy.

    Format reconnu : « :param name (type): description » et « :param name: ... ».
    Retourne "" si non documenté.
    """
    spec = _TOOL_REGISTRY.get("__scanning__")  # placeholder non utilisé ici
    # _TOOL_REGISTRY ne contient pas encore le tool au moment où _py_type_to_json
    # est appelé : on évite donc de dépendre du registre.
    # On extraye la doc depuis la fonction enveloppe — passe par l'appelant.
    return ""  # Docstring parsing plus fin reporté au builder de schéma


# --------------------------------------------------------------------------- #
#  Helpers slug / normalisation nom (réutilisables par tous les tools)
# --------------------------------------------------------------------------- #
def slugify(text: str, maxlen: int = 60) -> str:
    nf = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only.strip())
    return ascii_only[:maxlen].strip("_").lower() or "perso"


def normalize_key(name: str) -> str:
    """Normalise un nom pour comparaison (monstres, bestiaire). Accent/casse."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = s.replace("le ", "").replace("la ", "").replace("d'", "").replace("de ", "")
    return "_".join(s.split())


# --------------------------------------------------------------------------- #
#  Flags émis par les tools à l'orchestrateur
# --------------------------------------------------------------------------- #
def image_event(url: str, alt: str, name: str) -> dict[str, Any]:
    return {"type": "image", "data": {"url": url, "alt": alt, "name": name}}


def status_event(description: str, done: bool = False) -> dict[str, Any]:
    return {"type": "status", "data": {"description": description, "done": done}}


def _resolved_type(spec: ToolSpec, pname: str) -> Any:
    """Retourne l'annotation résolue d'un param (ou `inspect.Parameter.empty`)."""
    if pname in spec.resolved_hints:
        return spec.resolved_hints[pname]
    p = spec.expected_args.get(pname)
    return p.annotation if p else inspect.Parameter.empty


def _coerce_arg(value: Any, hint: Any) -> Any:
    """Coerce une valeur reçue du LLM vers le type Python attendu.

    Le mode prompt-based (et même le natif quand l'API n'exige pas de schéma
    strict) délivre souvent des chaînes pour des args numériques. Sans cette
    coercion, le tool ferait `int + str` → TypeError. On tente ici:
    - `int`:    `"3" → 3`,  `3.0 → 3`
    - `float`:  `"3.5" → 3.5`
    - `bool`:   `"true"/"1"→ True`, `"false"/"0"→ False`
    - `str`:    pas de conversion (déjà str)
    Les valeurs déjà du bon type sont renvoyées telles quelles. La coercion
    reconnait également Optional[X] / Union[X, None].
    """
    if value is None:
        return value
    # Normalise Optional[X] / Union[X, None] -> X si applicable.
    resolved = hint
    origin = getattr(hint, "__origin__", None)
    if origin is not None:
        from typing import Union
        if origin is Union:
            args = [a for a in getattr(hint, "__args__", []) if a is not type(None)]
            if args:
                resolved = args[0]
    hint = resolved

    if hint in (int,):
        if isinstance(value, bool):
            return int(value)        # bool est sous-type de int en Python
        if isinstance(value, (int,)):
            return value
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return value
    if hint in (float,):
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return value
    if hint in (bool,):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "yes", "oui", "1"):
                return True
            if v in ("false", "no", "non", "0"):
                return False
        try:
            return bool(value)
        except Exception:
            return value
    return value


# --------------------------------------------------------------------------- #
#  Boucle : exécute un tool (sync ou async) et normalise son retour
# --------------------------------------------------------------------------- #
async def invoke_tool(spec: ToolSpec, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Exécute un tool en lui passant ctx + args, renvoie un ToolResult normalisé.

    Accepte en retour : ToolResult, str, ou dict avec {'text': ...}.
    Détecte si le tool est sync/async et l'await/call accordingly.
    """
    # Filtre seulement les args attendus (ignore les extras glissés par le LLM).
    asked: dict[str, Any] = {}
    for pname, p in spec.expected_args.items():
        if pname in args:
            asked[pname] = _coerce_arg(args[pname], _resolved_type(spec, pname))
        elif p.default is not inspect.Parameter.empty:
            asked[pname] = p.default
        else:
            # Paramètre requis absent — on tente un coerce str pour robustesse
            asked[pname] = ""
    try:
        if spec.is_async:
            result = await spec.func(ctx, **asked)
        else:
            result = await asyncio.to_thread(spec.func, ctx, **asked)
    except TypeError as e:
        return ToolResult(text=f"⚠️ Appel invalide du tool '{spec.name}' : {e}")
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"❌ Erreur tool '{spec.name}' : {e}")

    if isinstance(result, ToolResult):
        return result
    if isinstance(result, str):
        return ToolResult(text=result)
    if isinstance(result, dict):
        return ToolResult(
            text=result.get("text", ""),
            events=result.get("events", []),
            state_patch=result.get("state_patch"),
        )
    return ToolResult(text=json.dumps(result, ensure_ascii=False))
