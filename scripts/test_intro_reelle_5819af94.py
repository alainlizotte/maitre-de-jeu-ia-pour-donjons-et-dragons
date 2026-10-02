# -*- coding: utf-8 -*-
"""Vérification de bout en bout du correctif « intro 5819af94 ».

Contexte : en partie 5819af94, le tour d'INTRO (« débute la partie ») narrait
la Couronne de Mystra REMISE AU JOUEUR DANS UNE BOÎTE par Teleshann — alors
que le scénario l'ancre chez Zendar Nulentok (groove (4,0), obtenue après
combat). Cause : le `detail` des objectifs n'était injecté QUE via le bloc
CARTE DU DONJON (trame du manifeste), absent au tour d'intro (avant
`carte_donjon_entrer`). Correctif : le détail est injecté aussi dans le bloc
🎯 OBJECTIFS DE QUÊTE du récap (prompt_builder).

Ce script rejoue le MÊME contexte avec le VRAI LLM (backend live) :
  1. partie de test via l'API live (même quête ch01 que 5819af94) + 1 PJ ;
  2. SANS entrée dans le donjon (fidèle au tour d'intro incriminé) ;
  3. tour d'intro « débute la partie » via l'orchestrateur (code corrigé) ;
  4. contrôles :
     - le prompt injecté porte l'antidote (détail « vaincre Nulentok »,
       « Teleshann remet … le parchemin de la route et la fiole de vérité ») ;
     - la narration N'ANTICIPE PAS l'objet-clé (pas de Couronne remise/
       matérialisée en boîte) et l'ancre chez Nulentok ;
     - la mécanique n'a PAS enregistré la Couronne (inventaire de quête vide,
       objectif BLOQUÉ, progression 0/3) — quoi que narre le MJ.
  5. nettoyage : partie de test supprimée.

Usage : py scripts/test_intro_reelle_5819af94.py
(llama.cpp doit tourner sur localhost:8080 — cf. docker-compose.yml.)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.request
import uuid
from dataclasses import replace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from server.config import load_config                        # noqa: E402
from server.game.objectifs import objectifs_quete as _oq     # noqa: E402
from server.game.state import PartyState                    # noqa: E402
from server.llm.client import Message, OllamaClient         # noqa: E402
from server.llm.orchestrator import (                        # noqa: E402
    Orchestrator, _assemble_narrations,
)
from server.tools.base import ToolContext, invoke_tool      # noqa: E402
from server.tools.registry import discover_tools            # noqa: E402

API = "http://localhost:8123"
DATA_DIR = os.path.join(_REPO, "server", "data")
TOOLS = discover_tools("server.tools")

PASS = 0
FAIL = 0


def check(nom: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {nom}")
    else:
        FAIL += 1
        print(f"  ❌ {nom} {('- ' + detail) if detail else ''}")


def http(method: str, path: str, payload: dict | None = None,
         token: str = ""):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


async def tool(ctx: ToolContext, nom_outil: str, **kw):
    return await invoke_tool(TOOLS[nom_outil], ctx, kw)


# Mots qui signalent que l'objet-clé est REMIS/MATÉRIALISÉ au joueur —
# l'anticipation exacte incriminée en 5819af94 (« une petite boîte en bois…
# révélant une couronne », « vous recevrez cette boîte contenant la couronne »).
_RE_REMISE_COURONNE = re.compile(
    r"(remets?|remet|remise|tend[sz]?|tendance|confie|confi[ée]|donn[ée]|"
    r"offre|offert|paume|bo[îi]te|re[çc]evrez|re[çc]ois)",
    re.IGNORECASE,
)


def phrases_couronne(texte: str) -> list[str]:
    """Phrases mentionnant la Couronne (détection « remise » par phrase)."""
    sorti = []
    for ph in re.split(r"(?<=[.!?…])\s+|\n+", texte or ""):
        if re.search(r"couronne", ph, re.IGNORECASE):
            sorti.append(ph.strip())
    return sorti


async def main() -> int:
    pid = "intro_" + uuid.uuid4().hex[:8]
    print(f"\n⚙️  Partie de test : {pid}\n")

    # 0. Compte de test jetable (POST /api/parties exige un compte vérifié).
    compte = "robot_intro_" + uuid.uuid4().hex[:6]
    auth = http("POST", "/api/auth/inscription", {
        "nom": compte, "mot_de_passe": "x4x4x4",
    })
    token = str(auth.get("token") or "")
    check("compte de test créé (token obtenu)", bool(token), compte)

    # 1. Mise en place — même quête ch01 que la partie 5819af94 (API live).
    http("POST", "/api/parties", {
        "titre": "Test correctif intro 5819af94",
        "partie_id": pid,
    }, token=token)
    rq = http("POST", f"/api/parties/{pid}/quest", {
        "titre": "The Crown Of Mystra",
        "pitch": "La Couronne de Mystra, artefact de pouvoir magique immense, "
                 "a disparu. Les PJ parcourront Faerun pour la retrouver avant "
                 "qu'elle ne tombe entre de mauvaises mains.",
        "source": "[ro_the_crown_of_mystra_ch01] /data/scenarios/Les Royaumes "
                  "Oubliés/The Crown Of Mystra/"
                  "the crown of mystra-01-partie partie 1 2.pdf",
    })
    check("quête ch01 chargée via l'API live",
          bool(rq.get("ok")) and "crown_of_mystra" in
          str(rq.get("quete", {}).get("source", "")))

    ctx = ToolContext(partie_id=pid, joueur="alain", data_dir=DATA_DIR)
    await tool(ctx, "fiche_perso_creer_rapide",
               nom="Bargoum", race="Demi-orc", classe="Guerrier",
               joueur="alain", niveau=1)
    await tool(ctx, "etat_partie_patch", chemin="phase", valeur="opening_complete")
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    check("1 PJ créé + phase opening_complete",
          len(etat.get("pj") or []) == 1
          and etat.get("phase") == "opening_complete",
          f"pj={len(etat.get('pj') or [])}, phase={etat.get('phase')}")
    check("SANS donjon (fidèle au tour d'intro incriminé)",
          not (etat.get("donjon") or {}).get("id"))

    # 2. Prompt système (code corrigé) — l'antidote doit y être DÈS L'INTRO.
    cfg = load_config()
    cfg = replace(cfg, llm=replace(
        cfg.llm, base_url="http://localhost:8080/v1"))
    from server.llm.prompt_builder import PromptBuilder  # noqa: E402
    pb = PromptBuilder(cfg)
    system_text, etat = pb.build_system_message(pid)
    check("prompt : bloc 🎯 OBJECTIFS DE QUÊTE présent",
          "🎯 OBJECTIFS DE QUÊTE" in system_text)
    check("prompt : détail « vaincre Nulentok dans son groove » injecté",
          "vaincre Nulentok dans son groove" in system_text)
    check("prompt : détail « Teleshann remet … le parchemin de la route et "
          "la fiole de vérité » injecté",
          "parchemin de la route et la fiole de v" in system_text)

    # 3. Tour d'INTRO avec le VRAI LLM (orchestrateur, code corrigé).
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    messages = [Message(role="system", content=system_text),
                Message(role="user",
                        content="**[alain]** : débute la partie")]
    ctx.tour_id = uuid.uuid4().hex
    orch = Orchestrator(
        client=OllamaClient(cfg.llm),
        tools=discover_tools("server.tools"),
        tool_mode=cfg.llm.tool_mode,
        detect_simulation=cfg.llm.detect_simulation,
        max_iterations=cfg.llm.max_tool_iterations,
        max_tools_exposed=cfg.llm.max_tools_exposed,
        tool_temperature=cfg.llm.tool_temperature,
        decision_phase=getattr(cfg.game, "decision_phase", True),
    )
    print("\n⏳ Tour d'intro en cours (LLM live)…\n")
    result = await orch.run(messages, ctx, on_event=on_event)
    narration = "\n\n".join(_assemble_narrations(
        result.narrations_intermediaires, result.narration)).strip()
    print("── NARRATION DU MJ (telle que diffusée) ──")
    print(narration[:3000])
    if len(narration) > 3000:
        print(f"… (+{len(narration) - 3000} caractères)")

    # 4. Contrôles de conformité.
    print("\n── Contrôles de conformité ──")
    phrases = phrases_couronne(narration)
    remises = [p for p in phrases if _RE_REMISE_COURONNE.search(p)]
    check("AUCUNE phrase ne remet/matérialise la Couronne au joueur "
          "(l'anticipation 5819af94 ne se reproduit pas)",
          not remises, " | ".join(p[:160] for p in remises[:3]))
    check("la Couronne est ancrée chez Nulentok (possession/garde)",
          bool(re.search(
              r"(?:Nulentok[^.]*(?:couronne|possède|garde|détient)|"
              r"couronne[^.]*Nulentok)",
              narration, re.IGNORECASE | re.DOTALL))
          or ("Nulentok" in narration and not remises),
          "Nulentok absent de la narration")

    # La remise NARRÉE n'est JAMAIS validée par la mécanique (quoi qu'il
    # arrive) : inventaire de quête vide, objectif BLOQUÉ, progression 0/3.
    etat_apres = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    info_o = _oq(etat_apres, DATA_DIR, partie_id=pid)
    check("La Couronne de Mystra ABSENTE de l'inventaire de quête",
          "La Couronne de Mystra" in (info_o.get("manquants") or []),
          f"manquants={info_o.get('manquants')}")
    check("objectif 1 toujours ⛔ BLOQUÉ (progression 0/3)",
          info_o.get("progression") == "0/3"
          and any(o.get("statut") == "bloque"
                  for o in (info_o.get("objectifs") or [])),
          f"progression={info_o.get('progression')}")
    check("aucun inventaire_ajouter de la Couronne dans les traces d'outils",
          not any(
              "couronne" in str(tc.get("args") or {}).lower()
              and tc.get("name") == "inventaire_ajouter"
              for tc in result.tool_calls_trace),
          str([(tc.get("name"), tc.get("args"))
               for tc in result.tool_calls_trace
               if tc.get("name") == "inventaire_ajouter"])[:200])

    # 5. Nettoyage.
    try:
        http("DELETE", f"/api/parties/{pid}", token=token)
        print("\n🧹 Partie de test supprimée.")
    except Exception as e:                                       # noqa: BLE001
        print(f"\n⚠️  Nettoyage impossible : {e}")

    print(f"\n═════════════ RÉSULTAT : {PASS} OK / {FAIL} ÉCHEC ═════════════")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
