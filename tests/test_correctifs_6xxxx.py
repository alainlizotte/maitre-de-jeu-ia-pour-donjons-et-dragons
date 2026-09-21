# -*- coding: utf-8 -*-
"""Six correctifs issus du second examen de la partie 5b4e2bbe (2026-09-21).

1. Engagement auto : une hostilité IMMINENTE narrée par le MJ (« prêt à
   attaquer », « vous vise ») sans déclaration d'attaque du joueur laisse la
   rencontre 100 % prose — le déclencheur 5ter est élargi
   (`_HOSTILITE_IMMINENTE_RE`).
2. Ancrage scénario : après 6 Gobelins d'affilée hors module, la bible
   rappelle désormais les CRÉATURES DÉJÀ AFFRONTÉES (`memoire.
   monstres_combattus`) pour empêcher la re-génération du même monstre.
3. Harmonisation narration ↔ serveur : la narration contredisait la fiche
   (« PV Barkrur : 13/17 » vs état 2/16 ; « Jet de dégâts … = ? » jamais
   résolu). Les blocs PV/CA sont réécrits aux valeurs officielles finales
   (`_harmoniser_statut_serveur`, bloc 5quater-h) et les formules incomplètes
   sont retirées du strip.
4. Dédup inventaire : ré-acquisition d'un objet déjà persisté avec la même
   quantité clonait l'entrée (carte ×2, épée courbe ×5) — refus explicite.
5. Soins narrés par le MJ : « vous récupérez 5 points de vie » sans aucun
   tool restait sans effet (fiche restée 2/16) — bloc 5quater-d-bis qui
   applique le montant annoncé (`_appliquer_soins_oublies`).
6. Anti-boucle : l'anti-répétition D1ter partageait le budget `corrections`
   avec l'anti-simulation D1bis — une fois 3 corrections de simulation
   consommées, l'écho verbatim n'était plus jamais purgé. Budget dédié
   `corrections_echo`.

Usage : py -m pytest tests/test_correctifs_6xxxx.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import main  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.llm import orchestrator  # noqa: E402
from server.llm.client import ChatResult  # noqa: E402
from server.llm.orchestrator import OrchestratedResult, Orchestrator  # noqa: E402
from server.llm.prompt_builder import _scenario_bible_bloc  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.inventaire import inventaire_ajouter  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402


# ---------------------------------------------------------------------------
# Correctif 3 — harmonisation PV/CA sur l'état serveur
# ---------------------------------------------------------------------------

def test_harmoniser_statut_serveur_reecrit_les_blocs():
    pj = [{"nom": "Barkrur", "pv": 2, "pv_max": 16, "ca": 15}]
    monstres = [{"nom": "Gobelin", "pv": -3, "pv_max": 5, "ac": 15}]
    nar = (
        "Le gobelin grince des dents. **PV Barkrur :** 13/17 et le gobelin "
        "tombe — **PV Gobelin :** 12/5 (blessé).\n\n"
        "**CA Gobelin :** 12, **CA Barkrur :** 18.\n\n"
        "**PV Fantôme :** 40/40 !"
    )
    out = main._harmoniser_statut_serveur(nar, pj, monstres)
    # PV officiel final (2/16), PAS les 13/17 inventés.
    assert "**PV Barkrur :** 2/16" in out
    assert "13/17" not in out
    # Monstre détruit → « détruit », pas 12/5.
    assert "**PV Gobelin :** détruit" in out
    assert "12/5" not in out
    # CA officiels.
    assert "**CA Gobelin :** 15" in out
    assert "**CA Barkrur :** 15" in out
    assert "18" not in out
    # Créature inconnue de l'état → bloc inventé supprimé.
    assert "Fantôme" not in out


def test_harmoniser_statut_serveur_monstre_vivant():
    pj = [{"nom": "Barkrur", "pv": 2, "pv_max": 16, "ca": 15}]
    monstres = [{"nom": "Gobelin", "pv": 3, "pv_max": 5, "ac": 15}]
    nar = "**PV Gobelin :** 0/5 (touché !) et **PV Barkrur :** 16/17."
    out = main._harmoniser_statut_serveur(nar, pj, monstres)
    assert "**PV Gobelin :** 3/5" in out
    assert "**PV Barkrur :** 2/16" in out


def test_strip_formules_incompletes():
    # Cas réel msg [28] : « **Jet de dégâts :** Barkrur lance 1d12 + 4
    # (FOR ×1,5) = ? » — le jet n'a jamais été résolu.
    texte = (
        "Barkrur lève sa hache.\n\n"
        "**Jet de dégâts :** Barkrur lance 1d12 + 4 (FOR ×1,5) = ?\n\n"
        "Et la suite du récit."
    )
    out = orchestrator.strip_narration_artifacts(texte)
    assert "Jet de dégâts" not in out
    assert "1d12" not in out
    assert "Et la suite du récit." in out
    # Formule suspendue SANS préfixe « Jet de dégâts ».
    texte2 = "Le coup vaut 1d8 + 2 = ? selon la formule."
    out2 = orchestrator.strip_narration_artifacts(texte2)
    assert "1d8" not in out2
    assert "= ?" not in out2


# ---------------------------------------------------------------------------
# Correctif 4 — déduplication inventaire
# ---------------------------------------------------------------------------

PID_INV = "test_6xxxx_inventaire"


def _setup_fiche_inv(d: str) -> None:
    os.makedirs(os.path.join(d, "fiches"), exist_ok=True)
    fiche = {
        "nom": "Barkrur", "joueur": "alain", "niveau": 1,
        "pv": 13, "pv_max": 17, "ca": 15, "xp": 0,
        "for": 12, "de": 12, "con": 12, "int": 10, "sag": 10, "cha": 10,
        "inventaire": [
            {"nom": "carte", "qte": 1, "portee": "quete",
             "partie": PID_INV},
            {"nom": "épée courbe", "qte": 1},
        ],
        "equipement": [
            {"nom": "carte", "qte": 1},
            {"nom": "épée courbe", "qte": 1},
        ],
    }
    with open(os.path.join(d, "fiches", "fiche_barkrur.json"), "w",
              encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False)


def test_dedup_inventaire_meme_quantite_refuse():
    d = tempfile.mkdtemp(prefix="dnd35_dedup_")
    try:
        _setup_fiche_inv(d)
        ctx = ToolContext(partie_id=PID_INV, joueur="alain", data_dir=d)
        # Ré-acquisition (même quantité 1) → refus de clonage.
        tr = asyncio.run(inventaire_ajouter(
            ctx, "Barkrur", "carte", quantite=1, portee="quete"))
        assert "DÉJÀ enregistré" in tr.text, tr.text
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_barkrur.json"),
            encoding="utf-8"))
        cartes = [i for i in fiche["inventaire"]
                  if i.get("nom") == "carte"]
        assert len(cartes) == 1 and cartes[0]["qte"] == 1, cartes
        # Cas épée courbe ×5 (butin de 5 gobelins) : objet PERMANENT — le
        # cumul reste légitime, PAS de dédup (test 4d4b4557).
        for _ in range(4):
            tr2 = asyncio.run(inventaire_ajouter(
                ctx, "Barkrur", "épée courbe", quantite=1, poids=1.5))
            assert "DÉJÀ enregistré" not in tr2.text, tr2.text
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_barkrur.json"),
            encoding="utf-8"))
        ep = [i for i in fiche["inventaire"]
              if i.get("nom") == "épée courbe"]
        assert len(ep) == 1 and ep[0]["qte"] == 5, ep
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_inventaire_quantite_différente_fusionne():
    d = tempfile.mkdtemp(prefix="dnd35_dedup2_")
    try:
        _setup_fiche_inv(d)
        ctx = ToolContext(partie_id=PID_INV, joueur="alain", data_dir=d)
        # Un VRAI gain supplémentaire (quantité différente) reste légitime.
        tr = asyncio.run(inventaire_ajouter(
            ctx, "Barkrur", "épée courbe", quantite=2, poids=1.0))
        assert "DÉJÀ enregistré" not in tr.text, tr.text
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_barkrur.json"),
            encoding="utf-8"))
        ep = [i for i in fiche["inventaire"]
              if i.get("nom") == "épée courbe"]
        assert len(ep) == 1 and ep[0]["qte"] == 3, ep
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Correctif 5 — soins narrés par le MJ appliqués au serveur
# ---------------------------------------------------------------------------

PID_SOIN = "test_6xxxx_soins"


class _ClientNeutre:
    """Client factice : aucun appel d'outil (execute_tool_direct suffit)."""

    async def chat(self, work, tools=None, tool_choice=None, temperature=None):
        return ChatResult(
            content="La scène se poursuit.",
            tool_calls=[], finish_reason="stop", raw={},
        )


def _setup_fiche_soin(d: str) -> None:
    os.makedirs(os.path.join(d, "fiches"), exist_ok=True)
    fiche = {
        "nom": "Barkrur", "joueur": "alain", "niveau": 1,
        "pv": 2, "pv_max": 16, "ca": 15, "xp": 0,
        "for": 12, "de": 12, "con": 12, "int": 10, "sag": 10, "cha": 10,
        "inventaire": [], "equipement": [],
    }
    with open(os.path.join(d, "fiches", "fiche_barkrur.json"), "w",
              encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False)


def test_soins_narres_sans_tool_appliques():
    d = tempfile.mkdtemp(prefix="dnd35_soin_")
    try:
        _setup_fiche_soin(d)
        orch = Orchestrator(
            client=_ClientNeutre(), tools=discover_tools(),
            tool_mode="native",
        )
        ctx = ToolContext(partie_id=PID_SOIN, joueur="alain", data_dir=d)
        result = OrchestratedResult(
            narration=(
                "Le talisman s'illumine : vos blessures se referment, et "
                "vous récupérez **5 points de vie**."
            ),
        )
        txt = asyncio.run(main._appliquer_soins_oublies(
            orch, result, ctx, None, "Barkrur"))
        assert txt, "aucun rattrapage — le soin narré est resté sans effet"
        assert "Soin appliqué par le serveur" in txt
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_barkrur.json"),
            encoding="utf-8"))
        assert int(fiche.get("pv")) == 7, fiche.get("pv")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_soins_deja_appliques_ne_rejouent_pas():
    d = tempfile.mkdtemp(prefix="dnd35_soin2_")
    try:
        _setup_fiche_soin(d)
        orch = Orchestrator(
            client=_ClientNeutre(), tools=discover_tools(),
            tool_mode="native",
        )
        ctx = ToolContext(partie_id=PID_SOIN, joueur="alain", data_dir=d)
        result = OrchestratedResult(
            narration=(
                "vous récupérez **5 points de vie**.\n\n"
                "ℹ️ **Soin appliqué par le serveur** : déjà traité."
            ),
            # Le soin a DÉJÀ tourné dans le tour (trace) → aucun rejeu.
            tool_calls_trace=[{"name": "fiche_perso_soigner", "ok": True}],
        )
        txt = asyncio.run(main._appliquer_soins_oublies(
            orch, result, ctx, None, "Barkrur"))
        # Le tool a DÉJÀ tourné (détection par trace) → aucun rejeu.
        assert txt == ""
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_barkrur.json"),
            encoding="utf-8"))
        assert int(fiche.get("pv")) == 2, fiche.get("pv")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Correctif 1 — hostilité imminente → déclencheur 5ter
# ---------------------------------------------------------------------------

def _setup_bestiaire(d: str) -> None:
    gobelin = {
        "nom": "Gobelin", "cle": "gobelin", "type": "humanoide",
        "taille": "P", "fp": "1/3", "pv": 5, "pv_max": 5, "ca": 15,
        "init": 1,
    }
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump({"_meta": {}, "gobelin": gobelin}, f, ensure_ascii=False)


def test_hostilite_imminente_detectee():
    nar = (
        "Le Gobelin s'arrête à quelques mètres de vous, arme levée, prêt "
        "à attaquer. Que souhaitez-vous faire ?"
    )
    assert main._HOSTILITE_IMMINENTE_RE.search(nar)
    # Le déclencheur « prêt à attaquer » SEUL (sans « arme levée ») —
    # régression : la classe d'accents oubliait le ê (« pr[ée]t?s? »).
    assert main._HOSTILITE_IMMINENTE_RE.search("Le Gobelin est prêt à attaquer.")
    assert main._HOSTILITE_IMMINENTE_RE.search("Elle est prete a attaquer.")


def test_hostilite_imminente_declenche_detection():
    d = tempfile.mkdtemp(prefix="dnd35_hostil_")
    try:
        _setup_bestiaire(d)
        etat = {"phase": "exploration", "pj": [], "monstres_combat": []}
        nar = (
            "Le Gobelin s'arrête à quelques mètres de vous, arme levée, "
            "prêt à attaquer. Que souhaitez-vous faire ?"
        )
        # SANS le nouveau déclencheur : aucun marqueur de combat, aucun
        # dégât chiffré → rien (comportement historique fautif).
        assert main._detecter_combat_prose(d, nar, etat) == []
        # AVEC le déclencheur (hostilité imminente) : Gobelin détecté.
        types = main._detecter_combat_prose(
            d, nar, etat, forcer_declencheur=True)
        assert types == ["Gobelin"], types
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_variantes_hostilite():
    assert main._HOSTILITE_IMMINENTE_RE.search("Il s'apprête à frapper !")
    assert main._HOSTILITE_IMMINENTE_RE.search("La lame vous vise.")
    assert main._HOSTILITE_IMMINENTE_RE.search("Le bandit vous défie du "
                                               "regard, arme pointée.")
    assert main._HOSTILITE_IMMINENTE_RE.search("Le loup fond sur vous.")
    # Faux positifs à éviter : simple tension narrative sans agression.
    assert not main._HOSTILITE_IMMINENTE_RE.search(
        "Le marchand vous sourit et vous demande si vous désirez voir "
        "ses marchandises.")


# ---------------------------------------------------------------------------
# Correctif 2 — ancrage scénario : créatures déjà affrontées
# ---------------------------------------------------------------------------

def test_bible_rappelle_les_creatures_deja_affrontees():
    quete = {
        "bible": {
            "resume": "Le donjon de Mystra hante les oubliettes.",
            "ennemis": ["Squelette"],
        },
    }
    etat = {
        "memoire": {
            "monstres_combattus": [
                {"noms": ["Gobelin"], "issue": "victoire"},
                {"noms": ["Gobelin"], "issue": "victoire"},
                {"noms": ["Gobelin"], "issue": "défaite"},
            ],
        },
    }
    bloc = _scenario_bible_bloc(quete, etat)
    assert "CRÉATURES DÉJÀ AFFRONTÉES" in bloc
    assert "Gobelin (×3)" in bloc
    assert "fais AVANCER la trame" in bloc


def test_bible_sans_historique_de_combat_rien_de_plus():
    quete = {"bible": {"resume": "Un donjon.", "ennemis": ["Squelette"]}}
    bloc = _scenario_bible_bloc(quete, {"memoire": {}})
    assert "CRÉATURES DÉJÀ AFFRONTÉES" not in bloc
    # Et le bloc d'origine reste intact.
    assert "Squelette" in bloc


# ---------------------------------------------------------------------------
# Correctif 6 — budget d'écho DÉDIÉ (D1ter indépendant de D1bis)
# ---------------------------------------------------------------------------

NARRATION_ECHO = (
    "La sphère d'énergie vous frôle. Vos blessures se referment, et vous "
    "récupérez 5 points de vie. Le gobelin s'éloigne dans le couloir."
)


class _ClientEcho:
    """Renvoie TOUJOURS la même narration (écho verbatim + gain en prose).

    Reproduit le cycle de la partie 5b4e2bbe : la simulation (D1bis) épuise
    ses 3 corrections sur « récupérez 5 PV », puis l'anti-répétition D1ter
    (budget dédié) doit quand même détecter l'écho.
    """

    def __init__(self) -> None:
        self.appels = 0

    async def chat(self, work, tools=None, tool_choice=None, temperature=None):
        self.appels += 1
        return ChatResult(
            content=NARRATION_ECHO,
            tool_calls=[], finish_reason="stop", raw={},
        )


def test_echo_corrige_meme_apres_budget_simulation():
    d = tempfile.mkdtemp(prefix="dnd35_echo_")
    try:
        PartyState(data_dir=d, partie_id="test_6xxxx_echo").save({
            "meta": {"titre": "test"},
            "phase": "exploration",
            "pj": [{"nom": "Barkrur", "pv": 2, "pv_max": 16,
                    "joueur": "alain"}],
            "pnj": [],
            "monstres_combat": [],
            "histoire": [],
        })
        orch = Orchestrator(
            client=_ClientEcho(), tools=discover_tools(),
            tool_mode="native",
        )
        ctx = ToolContext(
            partie_id="test_6xxxx_echo", joueur="alain", data_dir=d)
        from server.llm.client import Message
        messages = [
            Message(role="system", content="Tu es le MJ."),
            Message(role="user", content="je regarde autour de moi"),
            # Tour PRÉCÉDENT : la narration à écho-er figure dans
            # l'historique.
            Message(role="assistant", content=NARRATION_ECHO),
        ]
        result = asyncio.run(orch.run(messages, ctx))
        # D1ter purge l'écho avec son budget DÉDIÉ (indépendant du budget
        # `corrections` de D1bis — c'est le cœur du correctif).
        assert result.corrections_echo >= 1, result.corrections_echo
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_champ_corrections_echo_existe():
    r = OrchestratedResult()
    assert r.corrections_echo == 0
    assert r.corrections == 0


if __name__ == "__main__":  # pragma: no cover
    test_harmoniser_statut_serveur_reecrit_les_blocs()
    test_harmoniser_statut_serveur_monstre_vivant()
    test_strip_formules_incompletes()
    test_dedup_inventaire_meme_quantite_refuse()
    test_dedup_inventaire_quantite_différente_fusionne()
    test_soins_narres_sans_tool_appliques()
    test_soins_deja_appliques_ne_rejouent_pas()
    test_hostilite_imminente_detectee()
    test_hostilite_imminente_declenche_detection()
    test_variantes_hostilite()
    test_bible_rappelle_les_creatures_deja_affrontees()
    test_bible_sans_historique_de_combat_rien_de_plus()
    test_echo_corrige_meme_apres_budget_simulation()
    test_champ_corrections_echo_existe()
    print("OK test_correctifs_6xxxx")
