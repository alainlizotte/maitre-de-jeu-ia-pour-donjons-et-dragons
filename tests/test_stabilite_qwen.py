"""Stabilisation du MJ (post-batterie e2e Qwen3.5-9B).

1. 💰 Budget d'outils par tour (`Orchestrator._run_one_tool`) : au-delà du
   quota, l'outil est refusé SANS exécution — le modèle bouclait 17-32×
   sur `fiche_perso_mettre_a_jour` / `inventaire_consulter`. Les
   rattrapages serveur (`execute_tool_direct`) restent hors budget.
2. 🐺 Plafond de monstres dans `engager_combat` (6 max par engagement) :
   le « zoo » de 11 créatures contre 4 PJ niv. 1 ne doit plus être possible.
3. 🛠️ Nudge inventaire : `inventaire_consulter` rappelle les outils
   d'écriture (ramasser/ajouter/retirer/munition) — sinon aucun modèle ne
   remplit jamais l'inventaire.

Usage : py -m pytest tests/test_stabilite_qwen.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.orchestrator import Message, OrchestratedResult, Orchestrator  # noqa: E402
from server.tools.base import ToolContext, _TOOL_REGISTRY, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

discover_tools()
TOOLS = _TOOL_REGISTRY
PID = "test_stabilite"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir(avec_bestiaire: bool = False) -> str:
    d = tempfile.mkdtemp(prefix="dnd35_stab_")
    if avec_bestiaire:
        shutil.copy2(
            os.path.join(REPO, "server", "data", "bestiaire.json"),
            os.path.join(d, "bestiaire.json"),
        )
    return d


# --------------------------------------------------------------------------- #
#  1. Budget d'outils par tour
# --------------------------------------------------------------------------- #
def test_budget_bloque_le_second_creer_rapide():
    """fiche_perso_creer_rapide : quota 1/tour. Le 2e appel est refusé sans
    exécution (une seule fiche créée)."""
    d = _fresh_dir()
    try:
        orch = Orchestrator(client=None, tools=TOOLS)
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        spec = TOOLS["fiche_perso_creer_rapide"]
        args = {
            "nom": "Bargoum", "race": "Demi-orc", "classe": "Guerrier",
            "carac_texte": "For 16, Dex 14, Con 11, Int 9, Sag 13, Cha 9",
        }
        result = OrchestratedResult()

        tr1 = asyncio.run(orch._run_one_tool(spec, ctx, args, None, result))
        assert tr1.text.startswith("✅"), tr1.text

        args2 = dict(args, nom="Second")
        tr2 = asyncio.run(orch._run_one_tool(spec, ctx, args2, None, result))
        assert "LIMITE ATTEINTE" in tr2.text, tr2.text

        # La 2e fiche n'a PAS été créée.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        noms = [p.get("nom") for p in (etat.get("pj") or [])]
        assert noms == ["Bargoum"], noms
        # Les refus sont tracés (le budget compte les tentatives).
        assert sum(1 for t in result.tool_calls_trace
                   if t["name"] == "fiche_perso_creer_rapide") == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rattrapage_serveur_hors_budget():
    """`execute_tool_direct` (rattrapages déterministes) n'est PAS soumis
    au budget : il doit s'exécuter même après épuisement du quota."""
    d = _fresh_dir()
    try:
        orch = Orchestrator(client=None, tools=TOOLS)
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        spec = TOOLS["fiche_perso_creer_rapide"]
        args = {
            "nom": "Premier", "race": "Humain", "classe": "Guerrier",
            "carac_texte": "For 14, Dex 12, Con 12, Int 10, Sag 10, Cha 10",
        }
        result = OrchestratedResult()
        tr1 = asyncio.run(orch._run_one_tool(spec, ctx, args, None, result))
        assert tr1.text.startswith("✅"), tr1.text

        tr2 = asyncio.run(orch.execute_tool_direct(
            "fiche_perso_creer_rapide", dict(args, nom="Rattrapage"),
            ctx, result=result,
        ))
        assert tr2 is not None and tr2.text.startswith("✅"), tr2.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        noms = [p.get("nom") for p in (etat.get("pj") or [])]
        assert "Rattrapage" in noms, noms
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_budget_via_boucle_native_renvoie_message_tool():
    """Le refus de budget passe par _exec_tool_calls : le modèle reçoit un
    message tool (pas d'exception) et la trace compte la tentative."""
    d = _fresh_dir()
    try:
        orch = Orchestrator(client=None, tools=TOOLS, tool_mode="native")
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        result = OrchestratedResult()
        work = []
        call = {"id": "c1", "function": {
            "name": "fiche_perso_creer_rapide",
            "arguments": '{"nom": "A", "race": "Humain", "classe": "Guerrier"}',
        }}
        asyncio.run(orch._exec_tool_calls([call], ctx, work, result, None))
        assert len(work) == 1 and work[0].role == "tool"
        assert not work[0].content.startswith("❌")

        # Second passage simulé : trace déjà pleine → refus de budget.
        result.tool_calls_trace.append({
            "name": "fiche_perso_creer_rapide",
            "args": {}, "ok": True, "text": "simulé",
        })
        call2 = {**call, "id": "c2"}
        work2 = []
        asyncio.run(orch._exec_tool_calls([call2], ctx, work2, result, None))
        assert "LIMITE ATTEINTE" in work2[0].content
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  1bis. Blocage anti-boucle : 3 refus budget → narration forcée (pas d'outil)
# --------------------------------------------------------------------------- #
class _ClientObstine:
    """CLM simulé : ré-émet TOUJOURS le même tool call (comportement Q4
    avec les refus budget) jusqu'à ce qu'on ne lui propose plus d'outils."""
    def __init__(self):
        self.appels: list[tuple[list, object]] = []

    async def chat(self, work, tools=None, tool_choice=None, temperature=None):
        self.appels.append((list(work), tools))
        if tools is None:
            content = (
                "Bargoum et son groupe achèvent leur préparation, " 
                "prêts pour la suite."
            )
            return type("_CR", (), {
                "content": content, "tool_calls": None,
                "finish_reason": "stop", "raw": None,
            })()
        call = {"id": f"c{len(self.appels)}", "type": "function", "function": {
            "name": "fiche_perso_creer_rapide",
            "arguments": ('{"nom": "Bargoum", "race": "Demi-orc", '
                          '"classe": "Guerrier", "carac_texte": "For 16, '
                          'Dex 14, Con 11, Int 9, Sag 13, Cha 9"}'),
        }}
        return type("_CR", (), {
            "content": "", "tool_calls": [call],
            "finish_reason": "tool_calls", "raw": None,
        })()


def test_refus_budget_recidivants_forcent_la_narration_sans_outil():
    """Après 3 refus budget d'affilée, le tour passe en narration FORCÉE :
    plus aucun tool n'est proposé au modèle → la fiche est créée UNE seule
    fois et le tour se termine par une narration (au lieu de 20 appels
    inutiles comme le comportement Q4 observé en e2e)."""
    d = _fresh_dir()
    try:
        client = _ClientObstine()
        orch = Orchestrator(client=client, tools=TOOLS, tool_mode="native")
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        messages = [Message(role="system", content="MJ D&D. Réponds en français.")]
        result = asyncio.run(orch.run(messages, ctx))

        # ≥3 refus budget comptabilisés → arrêt dur déclenché.
        assert result.refus_budget >= 3, result.refus_budget
        assert result.narration_forcee is True
        # La narration finale est bien celle du modèle (sans outils).
        assert "préparation" in result.narration, result.narration
        # Le dernier appel LLM n'avait AUCUN tool : l'itération de blocage.
        assert client.appels[-1][1] is None
        # Une seule fiche créée malgré les réitérations du modèle.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        noms = [p.get("nom") for p in (etat.get("pj") or [])]
        assert noms == ["Bargoum"], noms
        # Un système « CORRECTION BUDGET » a été injecté (preuve du guidage).
        for work, _tools in client.appels:
            for m in work:
                if m.role == "system" and "CORRECTION BUDGET" in m.content:
                    break
            else:
                continue
            break
        else:
            raise AssertionError("message système CORRECTION BUDGET introuvable")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  2. Plafond de monstres par engagement
# --------------------------------------------------------------------------- #
def test_engager_refuse_plus_de_six_monstres():
    d = _fresh_dir(avec_bestiaire=True)
    try:
        r = asyncio.run(invoke_tool(
            TOOLS["engager_combat"],
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            {"monstres": "Orc, Orc, Orc, Orc, Orc, Orc, Orc"},
        ))
        assert "Trop de monstres" in r.text, r.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") != "combat"
        assert not (etat.get("initiative") or [])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_engager_accepte_six_monstres():
    d = _fresh_dir(avec_bestiaire=True)
    try:
        # 1 PJ niveau 1 → plafond FP 5 ; le garde passe, le combat s'engage.
        PartyState(data_dir=d, partie_id=PID).save({
            "phase": "exploration",
            "pj": [{"nom": "Bargoum", "niveau": 1}],
            "pnj": [], "initiative": [],
        })
        r = asyncio.run(invoke_tool(
            TOOLS["engager_combat"],
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            {"monstres": "Kobold, Kobold, Kobold, Kobold, Kobold, Kobold"},
        ))
        assert "Trop de monstres" not in r.text, r.text
        assert "Combat engagé" in r.text or "Initiative" in r.text, r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  2. Plafond de monstres par engagement
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  4. Verrou d'incarnation « 1 joueur = 1 personnage » (tours WS seulement)
# --------------------------------------------------------------------------- #
def _ctx_ws(d: str, joueur: str, tour: str = "tour-1") -> ToolContext:
    return ToolContext(partie_id=PID, joueur=joueur, data_dir=d, tour_id=tour)


def test_verrou_refuse_2e_personnage_meme_joueur():
    d = _fresh_dir()
    try:
        ctx = _ctx_ws(d, "Elara")
        r1 = asyncio.run(invoke_tool(
            TOOLS["fiche_perso_creer_rapide"], ctx,
            {"nom": "Elara", "race": "Elfe", "classe": "Magicienne"},
        ))
        assert r1.text.startswith("✅"), r1.text
        r2 = asyncio.run(invoke_tool(
            TOOLS["fiche_perso_creer_rapide"], ctx,
            {"nom": "Autre", "race": "Humain", "classe": "Guerrier"},
        ))
        assert "incarnes DÉJÀ" in r2.text, r2.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert [p.get("nom") for p in etat.get("pj") or []] == ["Elara"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_verrou_refuse_perso_du_bonus_autre_joueur():
    """Le cas e2e : le LLM crée le personnage d'un AUTRE pendant ton tour —
    désormais refusé (sinon le verrou de tour exclut le vrai joueur)."""
    d = _fresh_dir()
    try:
        r1 = asyncio.run(invoke_tool(
            TOOLS["fiche_perso_creer_rapide"], _ctx_ws(d, "Zarkon"),
            {"nom": "Aelin", "race": "Humain", "classe": "Rôdeur"},
        ))
        assert r1.text.startswith("✅"), r1.text
        r2 = asyncio.run(invoke_tool(
            TOOLS["fiche_perso_creer_rapide"], _ctx_ws(d, "Clerc", "tour-2"),
            {"nom": "Aelin", "race": "Humaine", "classe": "Rôdeuse"},
        ))
        assert "déjà incarné par" in r2.text, r2.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_verrou_inactif_hors_tour_ws():
    """Sans tour_id (REST, tests, scripts) : comportement historique —
    plusieurs PJ peuvent être créés (scénarios multi-PJ)."""
    d = _fresh_dir()
    try:
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        for nom in ("Groth", "Mélodie", "Elara", "Zarkon"):
            r = asyncio.run(invoke_tool(
                TOOLS["fiche_perso_creer_rapide"], ctx,
                {"nom": nom, "race": "Humain", "classe": "Guerrier"},
            ))
            assert r.text.startswith("✅"), (nom, r.text)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  5. Champs d'encombrement présents dès la création
# --------------------------------------------------------------------------- #
def test_fiche_creee_champs_encombrement():
    d = _fresh_dir()
    try:
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        r = asyncio.run(invoke_tool(
            TOOLS["fiche_perso_creer_rapide"], ctx,
            {"nom": "Bargoum", "race": "Demi-orc", "classe": "Guerrier",
             "carac_texte": "For 16, Dex 14, Con 11, Int 9, Sag 13, Cha 9"},
        ))
        assert r.text.startswith("✅"), r.text
        from server.tools.inventaire import _chemin_fiche
        import json
        fiche = json.load(open(_chemin_fiche(ctx, "Bargoum"), encoding="utf-8"))
        assert fiche.get("inventaire") == []
        assert fiche.get("poids_transporte") == 0
        # L'ENUM du schéma est SANS accents (Legere/Moyenne/Lourde/Depassee).
        assert fiche.get("etat_encumbrance") == "Legere"
        assert int(fiche.get("charge_max") or 0) > 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  3. Nudge outils d'écriture inventaire
# --------------------------------------------------------------------------- #
def test_consulter_rappelle_les_outils_ecriture():
    d = _fresh_dir()
    try:
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        asyncio.run(invoke_tool(
            TOOLS["fiche_perso_creer_rapide"], ctx,
            {"nom": "Bargoum", "race": "Demi-orc", "classe": "Guerrier"},
        ))
        r = asyncio.run(invoke_tool(
            TOOLS["inventaire_consulter"], ctx, {"nom": "Bargoum"},
        ))
        for outil in ("inventaire_ramasser", "inventaire_ajouter",
                      "inventaire_retirer", "inventaire_consommer_munition"):
            assert outil in r.text, (outil, r.text[-300:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  6. Rattrapage « fiche absente » (Qwen Q5 : boucle mettre_a_jour/recuperer
#     sur un perso jamais créé → le serveur crée la fiche après 2 échecs)
# --------------------------------------------------------------------------- #
def test_rattrapage_creation_auto_fiche_absente():
    d = _fresh_dir()
    try:
        orch = Orchestrator(client=None, tools=TOOLS)
        ctx = ToolContext(partie_id=PID, joueur="Elara", data_dir=d,
                          tour_id="tour-e2e-1")
        spec = TOOLS["fiche_perso_mettre_a_jour"]
        result = OrchestratedResult()
        # Échec antérieur pour « Elara » déjà tracé (compte = 1)
        result.tool_calls_trace.append({
            "name": "fiche_perso_mettre_a_jour", "args": {}, "ok": False,
            "text": "❌ Aucune fiche trouvée pour 'Elara' — ce personnage n'existe PAS ...",
        })
        tr = asyncio.run(orch._run_one_tool(
            spec, ctx, {"nom": "Elara", "champ": "pv", "valeur": "8"},
            None, result))
        assert "Rattrapage serveur" in tr.text, tr.text
        import json
        from server.tools.inventaire import _chemin_fiche
        fiche = json.load(open(_chemin_fiche(ctx, "Elara"), encoding="utf-8"))
        assert fiche.get("nom") == "Elara", fiche
        assert fiche.get("pv") == 8, fiche          # la mise à jour a été rejouée
        assert fiche.get("joueur") == "Elara", fiche
    finally:
        shutil.rmtree(d, ignore_errors=True)
