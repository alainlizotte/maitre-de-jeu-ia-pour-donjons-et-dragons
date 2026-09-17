"""Correctifs issus de l'analyse de la partie 5f3e31c9 (fix C6).

1. Dégâts PJ perdus : une attaque déclarée narrée en prose SANS tool
   (lancer_attaque/lancer_degats) laissait le monstre intact après le rejeu
   5bis-a (« avancement forcé »). Le serveur résout désormais l'attaque de
   façon déterministe (`_attaque_pj_sans_jet`), comme le moteur le fait
   pour les monstres.
2. Soins PJ perdus : « Kit premiers secours » (sans « de ») et « rétablir »
   ne déclenchaient PAS le rattrapage 5bis-c-long (_ACTION_SOIN_RE trop
   étroite) — le chat affirmait « récupéré 3 PV » mais l'état restait figé.
3. Double action dans le message : le LLM recopiait le bandeau
   « ⚔️ Au tour de X (joueur Y) de décider une action » (avec PV inventés)
   et le serveur ajoutait le sien → 2× le même appel d'action. De même, la
   même attaque était narrée deux fois après un rejeu réussi
   (concat brouillon + version outils). `_RE_AUTOUR_STRIP` + filtre
   `_RE_PROSE_RESOLUTION` + `_assemble_narrations` corrigent cela.

Usage : py -m pytest tests/test_correctifs_c6_5f3e31c9.py -q
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.main import (  # noqa: E402
    _ACTION_ATTAQUE_RE,
    _ACTION_COMBAT_RE,
    _ACTION_SOIN_RE,
    _RE_AUTOUR_STRIP,
    _RE_DEGATS_SUBIS_PJ,
    _RE_ENGAGEMENT_NARRE,
    _RE_PROSE_RESOLUTION,
    _SOIN_RE,
    _appliquer_degats_pj_narres,
    _appliquer_soins_oublies,
    _assemble_narrations,
    _attaque_pj_sans_jet,
    _ressusciter_pj_oublie,
)
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_c6_5f3e31c9"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEXTES_REELS = {
    "soin": "j'utilise mon Kit premiers secours pour rétablir mes PV",
    "attaque": "Je l'attaque avec ma hache",
    "narration_soin": (
        "Vous avez récupéré **3 PV**, vous remontant à **6 PV** sur vos 17."
    ),
    "footer_llm": (
        "Le loup-garou vous fixe avec une fureur animalique.\n\n"
        "⚔️ **Au tour de Utturgut** (joueur alain) de décider une action.\n"
        "🎯 Ennemis vivants : Loup-garou (humain) 24/32 PV.\n\n"
        "Que décidez-vous de faire ?"
    ),
    "double_footer": (
        "attaque prose.\n\n"
        "⚔️ **Au tour de Utturgut** (joueur alain) de décider une action.\n"
        "🎯 Ennemis vivants : Loup-garou (humain) 28/32 PV.\n\n"
        "Que décidez-vous de faire ?\n\n"
        "⚔️ **Au tour de Utturgut** (joueur alain) de décider une action.\n"
        "🎯 Ennemis vivants : Loup-garou (humain)."
    ),
    "brouillon_attaque": (
        "Votre hache s'abat avec une précision féroce, traversant l'espace "
        "qui sépare vos armes. Le métal rencontre la chair du loup-garou "
        "avec un son étouffé, et la créature émet un cri de rage. Le "
        "loup-garou encaisse les dégâts de votre attaque."
    ),
    "finale_attaque": (
        "Votre hache s'enfonce profondément dans la chair du loup-garou. "
        "La créature émet un grognement rauque de douleur, son équilibre "
        "vacille. Elle recule d'un pas, ses griffes ensanglantées, mais "
        "elle ne se laisse pas abattre."
    ),
    # Réels partie 5f3e31c9 msgs 21-24 (résurrection).
    "offre_resurrection": (
        "Je ne peux pas ressusciter votre personnage automatiquement sans "
        "une action narrative spécifique. Cela aura un coût narratif.\n"
        "**Choix pour la suite :** 1. Nouvelle partie. 2. Résurrection "
        "négociée. 3. Reprise narrative. Que choisissez-vous ?"
    ),
    "narration_resurrection": (
        "Le repos long est un soulagement bienvenu. Quand vous ouvrez les "
        "yeux, une nouvelle énergie coule dans vos veines. Votre santé est "
        "restaurée, vous avez récupéré **1 point de vie par niveau**, vous "
        "êtes maintenant à **16 PV** sur vos 17."
    ),
    "resurrection_sans_montant": (
        "Le prêtre de Mystra pose les mains sur votre poitrine et vous "
        "ressuscite. Vous revenez à la vie, brisé mais vivant."
    ),
    # Réels partie 5f3e31c9 msg 30 (combat halluciné hors bestiaire).
    "engagement_hallucine": (
        "⚔️ **Engagement du combat** :\n"
        "- **Utturgut** (Barbare Demi-orc) vs **Zendar Nulentok** "
        "(Anti-druide)\n"
        "- Initiative : Zendar (14) vs Utturgut (12)\n"
        "- **Zendar** commence le combat !"
    ),
    "degats_foudre": (
        "⚡ Zendar lance un sort de décharge électrique ! Vous subissez "
        "**12 dégâts de foudre** !\n\n"
        "💥 Utturgut subit 12 dégâts → PV -5/17."
    ),
    "offre_combat_imminent": (
        "⚔️ **Combat imminent** : Zendar Nulentok (anti-druide) est prêt "
        "à vous attaquer.\n\nQue décidez-vous de faire ?\n1. **Attaquer "
        "immédiatement** 2. **Tenter de négocier**"
    ),
}


# --------------------------------------------------------------------------- #
#  Infrastructure de test
# --------------------------------------------------------------------------- #
def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_c6_")
    shutil.copy2(
        os.path.join(REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _setup_combat(d: str) -> None:
    """État combat réel de 5f3e31c9 : Loup-garou 28/32 CA 18, Utturgut 3/17."""
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["phase"] = "combat"
    etat["tour"] = 5
    etat["pj"] = [{
        "nom": "Utturgut", "joueur": "alain", "race": "Demi-orc",
        "classe": "Barbare", "niveau": 1, "pv": 3, "pv_max": 17,
        "ca": 14, "conditions": [],
    }]
    etat["initiative"] = [{"nom": "Utturgut", "init": 12},
                          {"nom": "Loup-garou (humain)", "init": 8}]
    etat["courant_tour_pour"] = "Utturgut"
    etat["monstres_combat"] = [{
        "nom": "Loup-garou (humain)", "pv": 28, "pv_max": 32, "ca": 18,
        "conditions": [], "fp": "3",
    }]
    st.save(etat)
    # Fiche réelle : BBA 1, FOR 19, Hache à deux mains (catalogue 1d12).
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    with open(os.path.join(fiches, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Utturgut", "classe": "Barbare", "niveau": 1, "xp": 0,
            "pv": 3, "pv_max": 17, "ca": 14, "bab": 1,
            "carac": {"FOR": 19, "DEX": 10, "CON": 15, "INT": 9,
                      "SAG": 12, "CHA": 9},
            "equipement": [
                {"nom": "Hache à deux mains", "qte": 1},
                {"nom": "Sac à dos", "qte": 1},
            ],
            "inventaire": [
                {"nom": "Hache à deux mains", "qte": 1, "poids": 5.44},
                {"nom": "Kit premiers secours", "qte": 1, "poids": 0.45},
            ],
            "conditions": [],
        }, f, ensure_ascii=False)


def _setup_mort(d: str) -> None:
    """État réel 5f3e31c9 post-GAME OVER : exploration, Utturgut Mort
    (-10/17), flag game_over posé."""
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["phase"] = "exploration"
    etat["tour"] = 0
    etat["courant_tour_pour"] = None
    etat["pj"] = [{
        "nom": "Utturgut", "joueur": "alain", "race": "Demi-orc",
        "classe": "Barbare", "niveau": 1, "pv": -10, "pv_max": 17,
        "ca": 14, "conditions": ["Mort"],
    }]
    etat["monstres_combat"] = []
    etat["game_over"] = True
    st.save(etat)
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    with open(os.path.join(fiches, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Utturgut", "classe": "Barbare", "niveau": 1, "xp": 0,
            "pv": -10, "pv_max": 17, "ca": 14, "bab": 1,
            "carac": {"FOR": 19, "DEX": 10, "CON": 15, "INT": 9,
                      "SAG": 12, "CHA": 9},
            "equipement": [{"nom": "Hache à deux mains", "qte": 1}],
            "conditions": ["Mort"],
        }, f, ensure_ascii=False)


class _FakeOrch:
    """Orchestrateur minimal : execute_tool_direct réel (registry), trace +
    auto-application des dégâts de lancer_degats (miroir de
    orchestrator._auto_appliquer_degats) — sans LLM."""

    def __init__(self):
        self.tools = TOOLS
        self.calls: list[str] = []

    async def execute_tool_direct(self, name, args, ctx,
                                  on_event=None, result=None):
        self.calls.append(name)
        tr = await invoke_tool(self.tools[name], ctx, args)
        if result is not None:
            result.tool_calls_trace.append({
                "name": name, "args": args,
                "ok": not tr.text.startswith("❌"),
                "text": tr.text[:300],
            })
        if name == "lancer_degats":
            m = re.search(r"[Dd]égâts infligés\s*:\s*(\d+)", tr.text or "")
            if m and int(m.group(1)) > 0:
                tr2 = await invoke_tool(
                    self.tools["fiche_perso_infliger_degats"], ctx,
                    {"nom": args.get("cible"), "degats": int(m.group(1))},
                )
                if result is not None:
                    result.notes_mecaniques.append(tr2.text)
        return tr


def _result(narration: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        narration=narration,
        tool_calls_trace=[],
        tool_events=[],
        state_patches=[],
        notes_mecaniques=[],
        narrations_intermediaires=[],
        iterations=0,
    )


# --------------------------------------------------------------------------- #
#  Fix #2 — Soins : regex élargies + rattrapage
# --------------------------------------------------------------------------- #
def test_regex_soin_kit_sans_de():
    assert _ACTION_SOIN_RE.search(TEXTES_REELS["soin"])
    assert _ACTION_SOIN_RE.search("kit de premiers secours")
    assert _ACTION_SOIN_RE.search("un sort pour rétablir mes PV")
    assert _ACTION_SOIN_RE.search("je me soigne")
    assert not _ACTION_SOIN_RE.search(TEXTES_REELS["attaque"])


def test_regex_soin_narration_accentuee():
    assert _SOIN_RE.search(TEXTES_REELS["narration_soin"])  # « récupéré »
    assert _SOIN_RE.search("il récupère quelques PV")       # « récupère »
    assert _SOIN_RE.search("ses PV sont rétablis")


async def test_soins_narres_appliques_a_la_fiche():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["narration_soin"])
        txt = await _appliquer_soins_oublies(
            orch, result, _ctx(d), None, "Utturgut")
        assert txt, "le rattrapage doit produire une note mécanique"
        assert "fiche_perso_soigner" in orch.calls
        fiches = os.path.join(d, "fiches")
        with open(os.path.join(fiches, "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 6, f"PV attendus 6, obtenus {fiche['pv']}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_soins_deja_resolus_pas_de_double():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["narration_soin"])
        # Le tool a DÉJÀ été appelé dans le tour : rien à faire.
        result.tool_calls_trace.append({
            "name": "fiche_perso_soigner", "args": {}, "ok": True,
            "text": "ok",
        })
        txt = await _appliquer_soins_oublies(
            orch, result, _ctx(d), None, "Utturgut")
        assert txt == ""
        assert "fiche_perso_soigner" not in orch.calls
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Fix C6bis — Soin kit SANS montant narré : résolution serveur 1d4
# --------------------------------------------------------------------------- #
async def test_soin_kit_sans_montant_applique_1d4():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        real = random.randint

        def _randint(a: int, b: int) -> int:
            return 3 if (a, b) == (1, 4) else b  # kit → 1d4 = 3

        random.randint = _randint
        try:
            orch = _FakeOrch()
            result = _result(
                "Vous appliquez le pansement du kit de premiers secours. "
                "Votre vitalité se stabilise, vous permettant de tenir.")
            txt = await _appliquer_soins_oublies(
                orch, result, _ctx(d), None, "Utturgut")
        finally:
            random.randint = real

        assert txt, "une note mécanique doit être produite"
        assert "3 PV" in txt
        assert "lancer_des" in orch.calls
        assert "fiche_perso_soigner" in orch.calls
        with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 6, f"PV attendus 6 (3+1d4=3), obtenus {fiche['pv']}"
        # `fiche_perso_soigner` est dans la trace → action CONSOMMÉE
        # (la rotation avance, plus de timeout de 300 s à attendre).
        assert any(
            tc["name"] == "fiche_perso_soigner"
            for tc in result.tool_calls_trace
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_soin_sans_kit_n_invente_rien():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        # Fiche SANS aucun objet de soin → rien ne doit être inventé.
        p = os.path.join(d, "fiches", "fiche_utturgut.json")
        with open(p, encoding="utf-8") as f:
            fiche = json.load(f)
        fiche["equipement"] = [{"nom": "Sac à dos", "qte": 1}]
        fiche["inventaire"] = [{"nom": "Sac à dos", "qte": 1, "poids": 0.91}]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)

        orch = _FakeOrch()
        result = _result(
            "Vous tentez de vous soigner mais vos mains tremblent.")
        txt = await _appliquer_soins_oublies(
            orch, result, _ctx(d), None, "Utturgut")
        assert txt == ""
        assert not orch.calls
        with open(p, encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_soin_avec_montant_reste_prioritaire():
    """Un montant narré continue d'être appliqué tel quel (pas de 1d4)."""
    d = _fresh_dir()
    try:
        _setup_combat(d)
        real = random.randint
        random.randint = lambda a, b: b
        try:
            orch = _FakeOrch()
            result = _result(TEXTES_REELS["narration_soin"])
            txt = await _appliquer_soins_oublies(
                orch, result, _ctx(d), None, "Utturgut")
        finally:
            random.randint = real
        assert "montant narré" in txt
        assert "lancer_des" not in orch.calls  # pas de 1d4 en plus
        with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 6  # 3 + montant narré (3)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Fix C6ter — Résurrection narrée SANS tool : application déterministe
# --------------------------------------------------------------------------- #
async def test_resurrection_narree_appliquee():
    """Cas réel 5f3e31c9 msgs 21-24 : le MJ narre le repos + « 16 PV sur
    vos 17 » mais l'état reste Mort/-10 → le serveur applique + la pénalité
    de Raise Dead (niveau 1 → −2 CON, irréparable, PV max réduits)."""
    d = _fresh_dir()
    try:
        _setup_mort(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["narration_resurrection"])
        txt = await _ressusciter_pj_oublie(orch, _ctx(d), None, result)
        assert txt, "une note mécanique doit être produite"
        assert "fiche_perso_condition" in orch.calls  # Mort levée
        assert "fiche_perso_soigner" in orch.calls    # PV rétablis
        assert "fiche_perso_mettre_a_jour" in orch.calls  # −2 CON + pv_max
        with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 16
        assert fiche["carac"]["CON"] == 13, "pénalité Raise Dead : −2 CON"
        assert fiche["pv_max"] == 16, "mod CON +2→+1 : PV max −1"
        assert "Mort" not in fiche["conditions"]
        assert "pénalité de résurrection" in txt
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert not etat.get("game_over"), "le flag GAME OVER doit être levé"
        assert etat["pj"][0]["pv"] == 16
        assert "Mort" not in (etat["pj"][0]["conditions"] or [])
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_resurrection_vraie_sans_penalite():
    """Résurrection VRAIE (Clr 9) : PV rétablis, mais AUCUNE perte de
    niveau ni de CON (l'unique variante sans pénalité en 3.5)."""
    d = _fresh_dir()
    try:
        _setup_mort(d)
        orch = _FakeOrch()
        narr = (
            "Le grand prêtre de Mystra invoque le sort suprême : une "
            "**Résurrection Vraie**. Vous êtes maintenant à **16 PV** sur "
            "vos 17, en pleine santé."
        )
        result = _result(narr)
        txt = await _ressusciter_pj_oublie(orch, _ctx(d), None, result)
        assert txt
        assert "fiche_perso_condition" in orch.calls   # Mort levée
        assert "fiche_perso_soigner" in orch.calls     # PV rétablis
        # AUCUNE pénalité : ni CON, ni niveau.
        assert "fiche_perso_mettre_a_jour" not in orch.calls
        assert "fiche_perso_perte_niveau" not in orch.calls
        with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["carac"]["CON"] == 15
        assert fiche["pv_max"] == 17
        assert fiche["pv"] == 16
        assert "aucune perte" in txt
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_resurrection_negociee_penalite_toujours():
    """« Résurrection négociée » (Raise Dead) : la pénalité s'applique —
    « négociée » ne doit PAS être pris pour une Résurrection Vraie."""
    d = _fresh_dir()
    try:
        _setup_mort(d)
        orch = _FakeOrch()
        narr = (
            "La Résurrection négociée du prêtre s'accomplit. Vous êtes "
            "maintenant à **16 PV** sur vos 17."
        )
        result = _result(narr)
        txt = await _ressusciter_pj_oublie(orch, _ctx(d), None, result)
        assert txt
        assert "fiche_perso_mettre_a_jour" in orch.calls  # −2 CON appliquée
        with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["carac"]["CON"] == 13
        assert fiche["pv_max"] == 16
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_resurrection_niveau2_perd_un_niveau():
    """Raise Dead au-delà du niveau 1 : −1 niveau (XP au point médian)."""
    d = _fresh_dir()
    try:
        _setup_mort(d)
        # Promuons Utturgut niveau 2 (CON 15 → mod +2, perte = 5+1+2 = 8 PV max).
        p = os.path.join(d, "fiches", "fiche_utturgut.json")
        with open(p, encoding="utf-8") as f:
            fiche = json.load(f)
        fiche["niveau"] = 2
        fiche["xp"] = 1500
        fiche["pv"] = -10
        fiche["pv_max"] = 22
        with open(p, "w", encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)
        st = PartyState(data_dir=d, partie_id=PID)
        etat = st.load()
        etat["pj"][0].update({"niveau": 2, "pv_max": 22})
        st.save(etat)

        orch = _FakeOrch()
        result = _result(TEXTES_REELS["resurrection_sans_montant"])
        txt = await _ressusciter_pj_oublie(orch, _ctx(d), None, result)
        assert txt
        assert "fiche_perso_perte_niveau" in orch.calls
        with open(p, encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["niveau"] == 1, "Raise Dead : −1 niveau"
        assert fiche["carac"]["CON"] == 15, "pas de perte de CON au-delà du niv 1"
        assert fiche["pv"] == 1, "retour à 1 PV (aucun montant narré)"
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_offre_resurrection_n_applique_pas():
    """Le MJ PROPOSE la résurrection (msg 22) : ne rien appliquer."""
    d = _fresh_dir()
    try:
        _setup_mort(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["offre_resurrection"])
        txt = await _ressusciter_pj_oublie(orch, _ctx(d), None, result)
        assert txt == ""
        assert not orch.calls
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("game_over") is True
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_resurrection_sans_montant_pv_1():
    """Vocabulaire de résurrection affirmé sans montant → PV 1."""
    d = _fresh_dir()
    try:
        _setup_mort(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["resurrection_sans_montant"])
        txt = await _ressusciter_pj_oublie(orch, _ctx(d), None, result)
        assert txt
        with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 1
        assert "Mort" not in fiche["conditions"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_resurrection_pas_de_pj_mort_inerte():
    d = _fresh_dir()
    try:
        _setup_combat(d)  # Utturgut vivant (3/17)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["narration_resurrection"])
        txt = await _ressusciter_pj_oublie(orch, _ctx(d), None, result)
        assert txt == ""
        assert not orch.calls
    finally:
        shutil.rmtree(d, ignore_errors=True)



# --------------------------------------------------------------------------- #
#  Fix #1 — Attaque PJ résolue déterministement côté serveur
# --------------------------------------------------------------------------- #
async def test_attaque_pj_sans_jet_applique_les_degats():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        real = random.randint

        def _randint(a: int, b: int) -> int:
            if (a, b) == (1, 20):
                return 15  # 15 + (BBA 1 + FOR +4) = 20 ≥ CA 18 → touché
            return b       # dégâts max : 1d12 = 12

        random.randint = _randint
        try:
            orch = _FakeOrch()
            result = _result()
            note = await _attaque_pj_sans_jet(
                orch, _ctx(d), None, result, "Utturgut")
        finally:
            random.randint = real

        assert "⚔️ **Attaque**" in note
        assert "lancer_attaque" in orch.calls
        assert "lancer_degats" in orch.calls
        # 1d12 (max 12) + 6 (FOR 19 mod +4 ×1,5 à deux mains) = 18.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        lg = etat["monstres_combat"][0]
        assert lg["pv"] == 28 - 18, f"PV attendus 10, obtenus {lg['pv']}"
        # L'auto-application a noté l'inflige dans les notes mécaniques.
        assert any("Loup-garou" in n for n in result.notes_mecaniques)
        # La trace contient le tool qui CONSOMME l'action (rotation).
        assert any(
            tc["name"] == "lancer_attaque" for tc in result.tool_calls_trace
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_attaque_pj_ratee_n_inflige_rien():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        real = random.randint

        def _randint(a: int, b: int) -> int:
            return 2 if (a, b) == (1, 20) else b  # 2 + 5 = 7 < CA 18

        random.randint = _randint
        try:
            orch = _FakeOrch()
            result = _result()
            note = await _attaque_pj_sans_jet(
                orch, _ctx(d), None, result, "Utturgut")
        finally:
            random.randint = real

        assert "Manqué" in note
        assert "lancer_degats" not in orch.calls
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat["monstres_combat"][0]["pv"] == 28
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_attaque_pj_sans_arme_au_catalogue_inerte():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        # Équipement SANS arme du catalogue → rien ne peut être résolu.
        p = os.path.join(d, "fiches", "fiche_utturgut.json")
        with open(p, encoding="utf-8") as f:
            fiche = json.load(f)
        fiche["equipement"] = [{"nom": "Sac à dos", "qte": 1}]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)

        orch = _FakeOrch()
        result = _result()
        note = await _attaque_pj_sans_jet(
            orch, _ctx(d), None, result, "Utturgut")
        assert note == ""
        assert not orch.calls
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat["monstres_combat"][0]["pv"] == 28
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_attaque_pj_sans_ennemi_vivant_inerte():
    d = _fresh_dir()
    try:
        _setup_combat(d)
        st = PartyState(data_dir=d, partie_id=PID)
        etat = st.load()
        etat["monstres_combat"] = [{
            "nom": "Loup-garou (humain)", "pv": 0, "pv_max": 32, "ca": 18,
            "conditions": ["Détruit"], "fp": "3",
        }]
        st.save(etat)

        orch = _FakeOrch()
        result = _result()
        note = await _attaque_pj_sans_jet(
            orch, _ctx(d), None, result, "Utturgut")
        assert note == ""
        assert not orch.calls
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  Fix #3 — Bandeau « Au tour de » dupliqué + double narration d'attaque
# --------------------------------------------------------------------------- #
def test_strip_bandeau_autour_llm():
    stripped = _RE_AUTOUR_STRIP.sub("", TEXTES_REELS["footer_llm"]).strip()
    assert "Au tour de" not in stripped
    assert "24/32" not in stripped, "fuite d'info MJ (PV) à retirer"
    assert "Que décidez-vous" not in stripped
    assert "fureur animalique" in stripped, "la prose d'avant doit rester"


def test_strip_double_bandeau_d_un_coup():
    stripped = _RE_AUTOUR_STRIP.sub("", TEXTES_REELS["double_footer"]).strip()
    assert "Au tour de" not in stripped


def test_strip_bandeau_serveur_idempotent():
    seul = (
        "texte.\n\n⚔️ **Au tour de Utturgut** (joueur alain) de décider "
        "une action.\n🎯 Ennemis vivants : Loup-garou (humain)."
    )
    stripped = _RE_AUTOUR_STRIP.sub("", seul).strip()
    assert "Au tour de" not in stripped


def test_rejeu_attaque_narree_une_seule_fois():
    """Le brouillon intermédiaire (prose sans tool) est abandonné au profit
    de la narration du rejeu (outils réels) — la même attaque ne doit plus
    être lue deux fois."""
    parts = _assemble_narrations(
        [TEXTES_REELS["brouillon_attaque"]], TEXTES_REELS["finale_attaque"])
    parts = [
        p for p in parts[:-1] if not _RE_PROSE_RESOLUTION.search(p)
    ] + parts[-1:]
    assert len(parts) == 1, f"une seule narration attendue : {parts}"
    assert "s'enfonce" in parts[0]
    assert "s'abat" not in parts[0]
    assert "encaisse" not in parts[0]


def test_intro_de_scene_conservee_lors_du_rejeu():
    intro = "Une brise glaciale traverse le couloir abandonné, glaçant vos os."
    parts = _assemble_narrations([intro], TEXTES_REELS["finale_attaque"])
    parts = [
        p for p in parts[:-1] if not _RE_PROSE_RESOLUTION.search(p)
    ] + parts[-1:]
    assert parts == [intro, TEXTES_REELS["finale_attaque"]]

# --------------------------------------------------------------------------- #
#  Fix C6quinquies — Dégâts narrés hors combat + engagement halluciné
# --------------------------------------------------------------------------- #
def _setup_exploration(d: str) -> None:
    """Utturgut vivant (16/16, CON 13 post-résurrection), phase exploration."""
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["phase"] = "exploration"
    etat["tour"] = 0
    etat["pj"] = [{
        "nom": "Utturgut", "joueur": "alain", "race": "Demi-orc",
        "classe": "Barbare", "niveau": 1, "pv": 16, "pv_max": 16,
        "ca": 14, "conditions": [],
    }]
    etat["monstres_combat"] = []
    st.save(etat)
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    with open(os.path.join(fiches, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Utturgut", "classe": "Barbare", "niveau": 1, "xp": 0,
            "pv": 16, "pv_max": 16, "ca": 14, "bab": 1,
            "carac": {"FOR": 19, "DEX": 10, "CON": 13, "INT": 9,
                      "SAG": 12, "CHA": 9},
            "equipement": [{"nom": "Hache à deux mains", "qte": 1}],
            "conditions": [],
        }, f, ensure_ascii=False)


async def test_degats_narres_pj_appliques_hors_combat():
    """Cas réel msg 30 : « vous subissez 12 dégâts » narré sans tool en
    exploration → le serveur les applique (16 → 4)."""
    d = _fresh_dir()
    try:
        _setup_exploration(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["degats_foudre"])
        txt = await _appliquer_degats_pj_narres(
            orch, _ctx(d), None, result, "Utturgut")
        assert txt, "une note mécanique doit être produite"
        assert "fiche_perso_infliger_degats" in orch.calls
        with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 4, f"PV attendus 4 (16-12), obtenus {fiche['pv']}"
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat["pj"][0]["pv"] == 4
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_degats_narres_pas_de_double_si_tool_trace():
    d = _fresh_dir()
    try:
        _setup_exploration(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["degats_foudre"])
        result.tool_calls_trace.append({
            "name": "fiche_perso_infliger_degats", "args": {}, "ok": True,
            "text": "ok",
        })
        txt = await _appliquer_degats_pj_narres(
            orch, _ctx(d), None, result, "Utturgut")
        assert txt == ""
        assert not orch.calls
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_degats_narres_en_combat_inerte():
    """En combat, les filets dédiés s'appliquent — pas de double voie."""
    d = _fresh_dir()
    try:
        _setup_combat(d)
        orch = _FakeOrch()
        result = _result(TEXTES_REELS["degats_foudre"])
        txt = await _appliquer_degats_pj_narres(
            orch, _ctx(d), None, result, "Utturgut")
        assert txt == ""
        assert not orch.calls
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_regex_engagement_narre():
    assert _RE_ENGAGEMENT_NARRE.search(TEXTES_REELS["engagement_hallucine"])
    # Un simple « combat imminent » dans une OFFRE de choix ne déclenche PAS.
    assert not _RE_ENGAGEMENT_NARRE.search(
        TEXTES_REELS["offre_combat_imminent"])
    # Le texte joueur « j'attaque Zendar » est bien une action de combat.
    assert _ACTION_ATTAQUE_RE.search("j'attaque Zendar avec ma hache")


def test_regex_degats_subis():
    m = _RE_DEGATS_SUBIS_PJ.search(TEXTES_REELS["degats_foudre"])
    assert m and m.group(1) == "12"
    m2 = _RE_DEGATS_SUBIS_PJ.search(
        "Le piège frappe : Utturgut encaisse 8 dégâts de pierres.")
    assert m2 and m2.group(1) == "8"


async def test_zendar_au_bestiaire():
    """Zendar (scénario Crown of Mystra converti 2e→3.5) est trouvable avec
    ses stats converties : CA 20 (AC 2e 0), PV 81, FP 10."""
    from server.tools.monstres import _find_monstre
    d = _fresh_dir()
    try:
        m = _find_monstre(_ctx(d), "Zendar Nulentok (anti-druide)")
        assert m is not None, "Zendar doit figurer au bestiaire"
        assert int(m["ca"]) == 20
        assert int(m["pv"]) == 81
        assert str(m["fp"]).strip() == "10"
        assert "Cimeterre" in str(m["attaques"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_engager_zendar_refuse_solo_niveau1():
    """Garde de difficulté : FP 10 contre un Barbare niveau 1 solo
    (plafond FP 5) → refus explicite, pas de combat sans espoir."""
    d = _fresh_dir()
    try:
        r = await invoke_tool(TOOLS["fiche_perso_creer_rapide"], _ctx(d), {
            "nom": "Utturgut", "race": "Demi-orc", "classe": "Barbare",
            "niveau": 1, "joueur": "alain",
            "carac_texte": "For 19, Dex 10, Con 13, Int 9, Sag 12, Cha 9",
        })
        assert r.text.startswith("✅"), r.text
        r2 = await invoke_tool(TOOLS["engager_combat"], _ctx(d), {
            "monstres": "Zendar Nulentok (anti-druide)"})
        assert r2.text.startswith("⛔"), r2.text
        assert "sans espoir" in r2.text.lower()
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") != "combat"
    finally:
        shutil.rmtree(d, ignore_errors=True)
