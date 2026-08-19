"""Orchestrateur de function-calling — cœur de la fiabilité du MJ.

Remplace le pipeline tool-calling d'OpenWebUI par une boucle contrôlée par le
backend, qui :
1. tente le function-calling **natif** (schéma JSON envoyé à Ollama) ;
2. si le LLM « simule » l'appel en prose, ou si l'API native est absente,
   retombe sur un mode **prompt-based** : description des tools dans le system
   + parsing des balises `<tool name="..." key="...">` ;
3. exécute le tool, renvoie son résultat en message `role=tool`, et boucle
   jusqu'à ce que le LLM produise une vraie réponse de narration.

La détection des patterns de simulation (`*(Simulation de l'appel ...)*`,
`*(Appel de l'outil ...)*`, `*(Simulation des jets)*`) injecte un correctif
système et relance — max 2 essais — pour éviter la boucle infernale décrite
dans le guide d'installation d'OpenWebUI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..tools.base import ToolContext, ToolResult, ToolSpec, invoke_tool
from ..game.state import PartyState
from ..tools.registry import tools_prompt_section, tools_schemas_all
from .client import ChatResult, Message, OllamaClient

# Ensembles de tools par phase. Un modèle 12B ne gère fiablement que ~10
# tools ; Gemma se perd au-delà de 30. On filtre dynamiquement selon la phase
# de la partie disponible sur disque. Chaque liste reste <= 12 tools.
_PHASE_TOOLS: dict[str, tuple[str, ...]] = {
    "opening": (
        "etat_partie_get",
        "etat_partie_patch",
        "lancer_caracteristiques",
        "manuels_distribuer",
        "manuels_lister",
        "carte_joueurs_get",
        "carte_joueurs_position",
        "scenarios_laelith_lister",
        "scenarios_laelith_charger",
    ),
    "opening_complete": (
        "etat_partie_get",
        "etat_partie_patch",
        "fiche_perso_creer_rapide",
        "fiche_perso_recuperer",
        "lancer_caracteristiques",
        "manuels_lister",
        "monstre_consulter",
        "carte_joueurs_get",
        "carte_donjon_entrer",
        "scenarios_laelith_lister",
        "scenarios_laelith_charger",
    ),
    "exploration": (
        "etat_partie_get",
        "etat_partie_patch",
        "fiche_perso_recuperer",
        "fiche_perso_mettre_a_jour",
        "carte_donjon_entrer",
        "carte_donjon_explorer",
        "carte_donjon_get",
        "carte_donjon_sortir",
        "carte_joueurs_get",
        "carte_joueurs_deplacer",
        "monstre_consulter",
        "lancer_d20",
    ),
    "combat": (
        "etat_partie_get",
        "etat_partie_patch",
        "lancer_attaque",
        "lancer_degats",
        "lancer_sauvegarde",
        "calculer_initiative",
        "demarrer_combat",
        "tour_suivant_combat",
        "finir_combat",
        "fiche_perso_infliger_degats",
        "fiche_perso_soigner",
        "monstre_consulter",
    ),
}


_log = logging.getLogger("dnd35.orchestrator")


# --------------------------------------------------------------------------- #
#  Patterns de « simulation » (à détecter et corriger)
# --------------------------------------------------------------------------- #
_SIMULATION_PATTERNS = [
    # `[^*]*?` accepte toute parenthèse interne (le text a souvent `())*`).
    re.compile(r"\*\(Simulation\s+de\s+l'appel[^*]*?\)\*", re.IGNORECASE),
    re.compile(r"\*\(Appel\s+de\s+l'outil[^*]*?\)\*", re.IGNORECASE),
    re.compile(r"\*\(Simulation\s+des\s+jets[^*]*?\)\*", re.IGNORECASE),
    re.compile(r"\*Simulation\s+de\s+l'appel\s+`?\w+`?\*\*", re.IGNORECASE),
    # Prose ordinaire : "Je vais simuler l'appel ..."  (sans astérisques).
    re.compile(r"\bsimul(?:er|e|ait|ent|é)\s+?(?:l'appel|l'outil|le\s+tool|les\s+jets)\b", re.IGNORECASE),
    # Variante sans astérisques : "(Simulation de l'appel ...)".
    re.compile(r"\(Simulation\s+de\s+l'appel[^*]*?\)", re.IGNORECASE),
    re.compile(r"\(Simulation\s+des\s+jets[^*]*?\)", re.IGNORECASE),
]


def looks_like_simulation(text: str) -> Optional[str]:
    """Renvoie le fragment de simulation trouvé, ou None."""
    if not text:
        return None
    for pat in _SIMULATION_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


# --------------------------------------------------------------------------- #
#  Extraction des appels d'outils en mode prompt-based
# --------------------------------------------------------------------------- #
_TOOL_TAG_RE = re.compile(
    r'<tool\s+name="(?P<name>[A-Za-z_][A-Za-z0-9_]*)"(?P<args>[^>]*)>',
    re.IGNORECASE,
)
_ARG_RE = re.compile(r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)="(?P<val>(?:\\.|[^"\\])*)"')


def parse_prompt_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extrait les balises `<tool name=".." key="value" ...>` du texte.

    Renvoie une liste de dicts `{"name": str, "arguments": {...}}` au même
    format que `tool_calls` OpenAI natif. Les valeurs sont dé-échappées
    (anti-escape des quotes/backslash).
    """
    calls: list[dict[str, Any]] = []
    for m in _TOOL_TAG_RE.finditer(text):
        name = m.group("name")
        args_blob = m.group("args")
        args: dict[str, Any] = {}
        for am in _ARG_RE.finditer(args_blob):
            v = am.group("val")
            # Dé-échapper \" \\ \n \t
            v = v.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")
            args[am.group("key")] = v
        calls.append({"name": name, "arguments": args})
    return calls


def strip_prompt_tool_calls(text: str) -> str:
    """Retire les balises `<tool ...>` du texte (pour le rendu narration)."""
    # Supprime la ligne entière contenant la balise tool
    return re.sub(
        r"^[ \t]*<tool\s+name=\"[^\"]*\"[^>]*>[ \t]*\r?\n?",
        "",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )


# --------------------------------------------------------------------------- #
#  Session d'orchestration (une par message joueur)
# --------------------------------------------------------------------------- #
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Hook async pour émettre des events aux clients (image, status, deltas...)."""


@dataclass
class OrchestratedResult:
    narration: str = ""
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    state_patches: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    corrections: int = 0
    simulation_attempted: bool = False
    # Trace lisible des appels d'outils effectifs — diagnostic & logs.
    # Liste de dicts {name, args, ok, text} alimentée par _run_one_tool.
    tool_calls_trace: list[dict[str, Any]] = field(default_factory=list)


class Orchestrator:
    """Pilote un tour complet (request joueur → réponse MJ narrée)."""

    def __init__(
        self,
        client: OllamaClient,
        tools: dict[str, ToolSpec],
        tool_mode: str = "prompt",   # "native" | "prompt" | "auto"
        detect_simulation: bool = True,
        max_iterations: int = 6,
    ):
        self.client = client
        self.tools = tools
        self.tool_mode = tool_mode
        self.detect_simulation = detect_simulation
        self.max_iterations = max_iterations

    # ------------------------------------------------------------------ #
    def _filter_tools_by_phase(
        self, all_tools: dict[str, ToolSpec], ctx: ToolContext
    ) -> dict[str, ToolSpec]:
        """Renvoie un sous-ensemble de tools limité à la phase courante.

        Un modèle 12B comme Gemma ne gère qu'~10 tools fiables ; à 39 il se
        perd, hallucine des noms d'outils et répond en prose sans appeler.
        On lit le `state.phase` et la présence de PJ sur disque, puis on
        restreint l'ensemble exposé au LLM. Le filtrage reste transparent :
        tous les tools restent exécutables ; seul le set présenté change.
        """
        try:
            state = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                max_history=50,
            )
            etat = state.load()
            phase = (etat.get("phase") or "opening").strip().lower() if etat else "opening"
            pj = etat.get("pj") if etat else []
        except Exception:
            phase, pj = "opening", []
        # Cas particulier : pas encore de PJ → on force "opening".
        if not pj:
            phase = "opening"
        elif phase not in _PHASE_TOOLS:
            phase = "exploration"
        allowed = set(_PHASE_TOOLS[phase])
        filtered = {n: s for n, s in all_tools.items() if n in allowed}
        # Garde-fou : si on n'obtient rien (ex. config cassée), on retombe sur
        # l'ensemble complet pour ne jamais brider la discussion.
        return filtered or all_tools

    # ------------------------------------------------------------------ #
    async def run(
        self,
        messages: list[Message],
        ctx: ToolContext,
        on_event: Optional[EventCallback] = None,
        on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> OrchestratedResult:
        """Boucle principale : appelle le LLM, exécute les tools, narrate.

        - `messages`   : historique complet (system + tours précédents + nouveau
                         message joueur).
        - `ctx`         : contexte tool (partie_id, joueur, data_dir).
        - `on_event`    : callback async pour events structurés (image, status).
        - `on_delta`    : callback async pour streaming tokens de narration au
                         client (peut être None si on ne stream pas).
        """
        result = OrchestratedResult()
        work = list(messages)
        # Filtrage par phase : un modèle 12B gère mal 39 tools fiables. Avant la
        # création de perso, seuls 3 outils de la phase d'ouverture suffisent ;
        # en exploration, seuls les pertinents. En cas de doute, on donne les
        # tools utiles à la phase courante. Le filtrage se détermine par la
        # dernière partie état disponible sur disque via ctx.
        filtered = self._filter_tools_by_phase(self.tools, ctx)
        schemas = tools_schemas_all(filtered)
        tool_section = tools_prompt_section(filtered)

        # En mode "prompt" seulement, on documente les balises <tool ...> au
        # system message (Gemma/Qwen tool-calling natif fragile sans schémas).
        # En mode "auto" on laisse d'abord les schémas JSON du payload parler
        # seuls — Gemma 4 12B supporte le tool-calling natif mais dilue le
        # signal si les outils sont aussi re-documentés en prose (context too
        # heavy). La prose n'est ajoutée qu'en mode "prompt" pur.
        if self.tool_mode == "prompt" and tool_section and work and work[0].role == "system":
            work[0] = Message(
                role="system",
                content=work[0].content + "\n\n" + tool_section,
            )

        # OpenAI impose : si tools non vides, tool_choice = "auto" sauf si
        # l'on veut forcer un appel. On laisse "auto".
        for _ in range(self.max_iterations):
            result.iterations += 1
            use_native = self.tool_mode in ("native", "auto")
            tools_arg = schemas if use_native else None
            chat = await self.client.chat(work, tools=tools_arg, tool_choice="auto" if use_native else None)

            # --- A. Détection de simulation textuelle ----------------------
            if self.detect_simulation:
                sim = looks_like_simulation(chat.content)
                if sim:
                    result.simulation_attempted = True
                    if result.corrections < 2:
                        result.corrections += 1
                        # On injecte un correctif et on relance le même tour.
                        work.append(Message(
                            role="assistant",
                            content=chat.content,
                            tool_calls=chat.tool_calls or None,
                        ))
                        work.append(Message(
                            role="system",
                            content=(
                                "⚠️ CORRECTION : tu as écrit "
                                f"« {sim} » au lieu d'appeler réellement l'outil. "
                                "Les `*(Simulation de l'appel ...)*` sont interdites :"
                                " elles invalident le jet. Rappelle l'outil via la "
                                "balise exacte `<tool name=\"...\" key=\"value\">` "
                                "(mode prompt) ou via le tool_calls natif — sans "
                                "reformuler la narrative jusqu'à obtenir le résultat. "
                                "Recommence ce tour en appelant réellement l'outil."
                            ),
                        ))
                        continue
                    # 2 corrections déjà : on continue avec le reste (best effort).

            # --- B. Mode natif : tool_calls présents -----------------------
            if chat.tool_calls and use_native:
                work.append(Message(
                    role="assistant",
                    content=chat.content or "",
                    tool_calls=chat.tool_calls,
                ))
                await self._exec_tool_calls(chat.tool_calls, ctx, work, result, on_event)
                continue

            # --- C. Mode prompt : extraire balises <tool> -------------------
            prompt_calls = parse_prompt_tool_calls(chat.content)
            if prompt_calls:
                # On enregistre la réponse nettoye des balises comme assistant.
                # Ollama v1 attend `tool_calls` natifs pour le mode prompt sans
                # schéma — on passe directement par le message.
                clean = strip_prompt_tool_calls(chat.content).strip()
                work.append(Message(
                    role="assistant",
                    content=chat.content,
                ))
                if on_delta and clean:
                    # On pousse quand même le texte résiduel (hors balises tool).
                    await on_delta(clean)
                await self._exec_tool_calls_prompt(prompt_calls, ctx, work, result, on_event)
                continue

            # --- D. Réponse finale (narration) ------------------------------
            # Aucun appel d'outil à effectuer ⇒ narration complète du MJ.
            work.append(Message(role="assistant", content=chat.content))
            # Stream propres deltas si callback fourni (sinon on envoie tout).
            if on_delta:
                # Déjà poussé pendant stream ? Non : chat() est non-streaming.
                # On simule le flux en envoyant le bloc entier en un token.
                await on_delta(chat.content)
            result.narration = chat.content
            break

        else:
            # Boucle épuisée sans final : on renvoie ce qu'on a.
            result.narration = result.narration or (
                work[-1].content if work and work[-1].role == "assistant" else ""
            )

        return result

    # ------------------------------------------------------------------ #
    async def _exec_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        ctx: ToolContext,
        work: list[Message],
        result: OrchestratedResult,
        on_event: Optional[EventCallback],
    ) -> None:
        """Exécute un lot de tool_calls OpenAI natifs (peut être parallèle)"""
        for call in tool_calls:
            fn = call.get("function", call)
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            # arguments peut être une string JSON ou un dict
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError as e:
                    args = {}
                    self._reply_tool_error(work, name, call.get("id"), f"❌ JSON d'args invalide : {e}")
                    continue
            else:
                args = raw_args or {}

            spec = self.tools.get(name)
            if not spec:
                self._reply_tool_error(
                    work, name, call.get("id"),
                    f"❌ Tool '{name}' inconnu. Tools disponibles : "
                    + ", ".join(self.tools.keys()),
                )
                continue
            tr = await self._run_one_tool(spec, ctx, args, on_event, result)
            work.append(Message(
                role="tool",
                name=name,
                tool_call_id=call.get("id") or name,
                content=tr.text,
            ))

    async def _exec_tool_calls_prompt(
        self,
        calls: list[dict[str, Any]],
        ctx: ToolContext,
        work: list[Message],
        result: OrchestratedResult,
        on_event: Optional[EventCallback],
    ) -> None:
        """Exécute les tool calls trouvés par parsing prompt-based."""
        for call in calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
            spec = self.tools.get(name)
            if not spec:
                work.append(Message(
                    role="tool",
                    name=name,
                    content=(
                        f"❌ Tool '{name}' inconnu. Tools disponibles : "
                        + ", ".join(self.tools.keys())
                    ),
                ))
                continue
            tr = await self._run_one_tool(spec, ctx, args, on_event, result)
            work.append(Message(
                role="tool",
                name=name,
                content=tr.text,
            ))

    # ------------------------------------------------------------------ #
    def _reply_tool_error(
        self,
        work: list[Message],
        name: str,
        call_id: Optional[str],
        err: str,
    ) -> None:
        work.append(Message(role="tool", name=name, tool_call_id=call_id or name, content=err))

    # ------------------------------------------------------------------ #
    async def _run_one_tool(
        self,
        spec: ToolSpec,
        ctx: ToolContext,
        args: dict[str, Any],
        on_event: Optional[EventCallback],
        result: OrchestratedResult,
    ) -> ToolResult:
        """Exécute un tool, relaye ses events, agrège le patch d'état."""
        # On attache le callback temps-réel au ctx pour que les tools puissent
        # émettre des events en live (ex : « ⏳ Génération image en cours »).
        ctx.on_event = on_event
        args_log = json.dumps(args, ensure_ascii=False, default=str)[:200]
        _log.info("tool_call name=%s args=%s", spec.name, args_log)
        tr = await invoke_tool(spec, ctx, args)
        ok = not tr.text.startswith("❌")
        result.tool_calls_trace.append({
            "name": spec.name,
            "args": args,
            "ok": ok,
            "text": tr.text[:300],
        })
        _log.info("tool_call done name=%s ok=%s text=%s",
                  spec.name, ok, tr.text[:150].replace("\n", " "))
        for ev in tr.events:
            result.tool_events.append(ev)
            if on_event:
                try:
                    await on_event(ev)
                except Exception:
                    pass
        if tr.state_patch:
            result.state_patches.append(tr.state_patch)
        return tr
