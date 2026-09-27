# -*- coding: utf-8 -*-
"""Correctifs issus de la session de bêta-test navigateur (sept. 2026).

C1  — Suppression de partie : contrôle de propriété (meta.createur).
C2  — Garde de tour par PERSONNAGE (session.ws_personnage) : deux PJ du même
      compte (deux onglets) ne se volent plus leurs tours.
M1  — Marqueurs de combat en prose élargis + alias « zombi » : une créature
      qui approche/attaque en prose sans dégâts chiffrés est rattrapée.
M3  — Nettoyage de la narration finale : coulisses LLM, templates cassés,
      paraphrases doublées.
M4  — Détection de sorts narrés sans `incanter_sort` (rattrapage serveur).
Fix or — or PORTÉ = départ − achats (client) ; la charge serveur lit fiche["or"].

Usage : py -m pytest tests/test_correctifs_beta_session1.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import main as srv  # noqa: E402
from server.game.session import PartySession  # noqa: E402
from server.game.state import PartyState  # noqa: E402


# --------------------------------------------------------------------------- #
#  M3 — Nettoyage de la narration
# --------------------------------------------------------------------------- #
NARRATION_COULISSES = (
    "Votre masse d'armes lourde s'abat sur la tête du zombi. L'os pourri "
    "craque sous le choc.\n\n"
    "Résultat : Touché ! ()\n\n"
    "État du Zombie : 5 PV / 5 PV (max) — Note : Le zombi semble avoir des PV "
    "plus élevés que prévu ou une régénération rapide, ou alors le système "
    "n'a pas encore mis à jour ses PV restants après ce coup. Vérifions son "
    "état réel.\n\n"
    "En réalité, le zombi a subi 12 dégâts. S'il avait 5 PV au maximum, il "
    "devrait être mort ou inconscient. Vérifions son état actuel.\n\n"
    "Le zombi s'effondre instantanément, sa chair putréfiée se délitant en "
    "une masse informe. Les catacombes reviennent au silence."
)


def test_m3_coulisses_llm_purgees():
    nettoye = srv._nettoyer_meta_narration(NARRATION_COULISSES)
    assert "Vérifions" not in nettoye
    assert "En réalité" not in nettoye
    assert "le système n'a pas" not in nettoye
    assert "état réel" not in nettoye
    assert "Résultat :" not in nettoye
    assert "Touché ! ()" not in nettoye
    # La prose légitime est conservée.
    assert "L'os pourri" in nettoye
    assert "s'effondre" in nettoye


def test_m3_meta_discours_perte_de_trace_purge():
    """Régression bêta : « Le système semble avoir perdu la trace… » (le MJ
    exposait sa confusion interne) est purgé."""
    nettoye = srv._nettoyer_meta_narration(
        "Le combat s'engage dans les catacombes humides, mais quelque chose "
        "ne va pas. Le système semble avoir perdu la trace du mort-vivant et "
        "de la position de votre groupe.\n\n"
        "Le mort-vivant avance vers vous, ses pas lourds résonnant sur le sol."
    )
    assert "quelque chose ne va pas" not in nettoye
    assert "semble avoir perdu" not in nettoye
    assert "Le mort-vivant avance vers vous" in nettoye


def test_m3_parentheses_vides_et_possessif_suspendu():
    assert "Touché !" in srv._nettoyer_meta_narration("Touché ! () Le monstre tombe.")
    assert "Réussite." in srv._nettoyer_meta_narration("Réussite ().")
    assert "réussi !" in srv._nettoyer_meta_narration("Vous avez réussi votre !")


def test_m3_paraphrases_doublées_retranchées():
    p1 = (
        "Dans l'ombre d'un recoin de la fosse, vous remarquez une fiole de "
        "verre sombre et un parchemin froissé, abandonnés par un visiteur "
        "précédent. Vous les ramassez avec précaution."
    )
    p2 = (
        "Dans l'ombre d'un recoin de la fosse, vous remarquez une fiole de "
        "verre sombre et un parchemin froissé, abandonnés là par un visiteur "
        "ancien. Vous les ramassez prudemment, sans bruit."
    )
    p3 = "Le couloir s'enfonce vers le nord, plus froid à chaque pas."
    nettoye = srv._nettoyer_meta_narration(p1 + "\n\n" + p2 + "\n\n" + p3)
    # Les deux paraphrases (même scène re-racontée) n'apparaissent qu'une fois.
    occurrences = nettoye.count("fiole de verre sombre")
    assert occurrences == 1, nettoye
    # Le paragraphe distinct est conservé.
    assert "Le couloir s'enfonce" in nettoye


def test_m3_narration_propre_intacte():
    texte = (
        "Vous avancez dans la galerie voûtée. L'air est humide et froid.\n\n"
        "Au nord, une arche ouvre sur la salle des banquets funèbres."
    )
    assert srv._nettoyer_meta_narration(texte) == texte


# --------------------------------------------------------------------------- #
#  M1 — Détection de combat narré en prose élargie
# --------------------------------------------------------------------------- #
def _etat_exploration(data_dir: str) -> dict:
    return {"phase": "exploration", "pj": [], "monstres_combat": []}


def _data_dir_reel() -> str:
    """Bestiaire réel (lecture seule) pour la détection de prose."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "server", "data",
    )


def test_m1_zombi_orthographe_francaise_detecte():
    types = srv._detecter_combat_prose(
        _data_dir_reel(),
        "Un bruit de pas lourds résonne. Une créature se déplace : un zombi, "
        "vêtu des vêtements de défunt d'un prêtre corrompu, avance vers vous "
        "avec un regard vide.",
        {"phase": "exploration"},
    )
    assert "Zombie" in types, types


def test_m1_attaque_esquivee_sans_degats_detectee():
    types = srv._detecter_combat_prose(
        _data_dir_reel(),
        "Le squelette brandit son épée rouillée et frappe : le coup manque "
        "son but et frôle votre armure sans l'entamer.",
        {"phase": "exploration"},
    )
    assert "Squelette" in types, types


def test_m1_squelettes_pluriel_detectes():
    types = srv._detecter_combat_prose(
        _data_dir_reel(),
        "Cassyt hurle : « Les morts se réveillent ! » Les squelettes "
        "avancent vers vous, leurs yeux creux remplis d'une lueur rougeâtre.",
        {"phase": "exploration"},
    )
    assert "Squelette" in types, types


def test_m1_prose_sans_hostilite_pas_de_faux_positif():
    types = srv._detecter_combat_prose(
        _data_dir_reel(),
        "Un squelette décoratif est scellé dans le mur de la crypte : on "
        "distingue ses os à travers la pierre. Vous continuez votre chemin.",
        {"phase": "exploration"},
    )
    assert types == [], types


# --------------------------------------------------------------------------- #
#  M4 — Détection de sorts narrés
# --------------------------------------------------------------------------- #
def test_m4_sort_narre_detecte():
    trouves = srv._detecter_sorts_narres(
        "Thorin concentre son énergie divine. Il lance le sort Bénédiction, "
        "une lueur de dévotion illuminant son visage.",
        set(),
    )
    assert any(t["nom"] == "Bénédiction" for t in trouves), trouves


def test_m4_refus_narre_pas_detected():
    trouves = srv._detecter_sorts_narres(
        "Vous tentez de lancer le sort Lumière, mais vous vous souvenez que "
        "vous ne l'avez pas préparé aujourd'hui. Le sort reste inaccessible.",
        set(),
    )
    assert trouves == [], trouves


def test_m4_equipement_bouclier_n_est_pas_le_sort():
    """Régression bêta : « bouclier » (équipement du clerc) déclenchait le
    rattrapage du SORT Bouclier, facturé au premier PJ trouvé."""
    trouves = srv._detecter_sorts_narres(
        "Vous levez votre bouclier acier lourd et vous avancez prudemment "
        "dans le couloir, bouclier devant vous.",
        set(),
    )
    assert trouves == [], trouves


def test_m4_verbe_apres_le_nom_detecte():
    """Les phrasings naturels (« un rayon de givre jaillit de son index »)
    placent le verbe APRÈS le nom — ils sont bien détectés."""
    trouves = srv._detecter_sorts_narres(
        "Ses doigts se tendent et un rayon de givre jaillit de son index, "
        "vissant le mort-vivant.",
        set(),
    )
    assert any(t["nom"] == "Rayon de givre" for t in trouves), trouves


def test_m4_verbe_avant_le_nom_detecte():
    trouves = srv._detecter_sorts_narres(
        "Il lance le sort Bénédiction et se sent réconforté.",
        set(),
    )
    assert any(t["nom"] == "Bénédiction" for t in trouves), trouves


def test_m4_mention_simple_pas_detected():
    """Une simple MENTION sans verbe (question, description) ne déclenche pas."""
    trouves = srv._detecter_sorts_narres(
        "Vous vous demandez si Projectiles magiques serait utile ici.",
        set(),
    )
    assert trouves == [], trouves


def test_m4_memorisation_joueur_detecte():
    """« Je mémorise Bénédiction et Lumière » (demande du joueur) est détecté
    via la citation des noms (les connecteurs « et/puis » entre les sorts)."""
    cites = srv._detecter_sorts_cites(
        "Je mémorise Bénédiction et Lumière sur mes emplacements de sorts.",
    )
    assert "Bénédiction" in cites and "Lumière" in cites, cites


def test_m4_declaration_joueur_je_lance_detecte():
    trouves = srv._detecter_sorts_narres(
        "Je lance un rayon de givre sur le mort-vivant !", set(),
    )
    assert any(t["nom"] == "Rayon de givre" for t in trouves), trouves


def test_m4_connecteur_ensuite_detecte():
    """Régression bêta : « Vous lancez ensuite Lumière sur votre lanterne »
    (connecteur « ensuite » entre le verbe et le nom) est détecté."""
    trouves = srv._detecter_sorts_narres(
        "Vous lancez ensuite Lumière sur votre lanterne.", set(),
    )
    assert any(t["nom"] == "Lumière" for t in trouves), trouves


def test_m4_sort_deja_resolu_par_tool_ignore():
    trace_norm = {srv._normaliser_texte_sort(
        "✨ **Bénédiction** (niv. 1) lancé par Thorin — Emplacements niv.1 : 1/3")}
    trouves = srv._detecter_sorts_narres(
        "Il lance le sort Bénédiction et se sent réconforté.", trace_norm)
    assert trouves == [], trouves


# --------------------------------------------------------------------------- #
#  C2 — Session : identité par connexion
# --------------------------------------------------------------------------- #
def test_c2_session_personnage_et_utilisateur_par_ws():
    sess = PartySession(partie_id="test_c2")
    ws_a, ws_b = object(), object()
    sess.ws_personnage[ws_a] = "Thorin"
    sess.ws_user[ws_a] = "alain"
    sess.ws_personnage[ws_b] = "Elandra"
    # Pas de token pour ws_b : retombe sur le nom déclaré.
    assert sess.personnage_de(ws_a) == "Thorin"
    assert sess.personnage_de(ws_b) == "Elandra"
    assert sess.utilisateur_de(ws_a, "nimporte") == "alain"
    assert sess.utilisateur_de(ws_b, "declare") == "declare"


# --------------------------------------------------------------------------- #
#  C1 — Propriété des parties (API directe sur fichiers d'état)
# --------------------------------------------------------------------------- #
def _nonce_partie(tmp_path, createur=None, pjs=None) -> str:
    pid = "betac1"
    st = PartyState(data_dir=str(tmp_path), partie_id=pid)
    etat = st.load()
    etat.setdefault("meta", {})
    if createur is not None:
        etat["meta"]["createur"] = createur
    etat["pj"] = pjs or []
    st.save(etat)
    return pid


@pytest.fixture
def data_iso(tmp_path, monkeypatch):
    """Isole le data_dir de l'app sur un dossier temporaire."""
    monkeypatch.setattr(
        srv.cfg.paths, "data_dir", str(tmp_path), raising=False
    )
    monkeypatch.setattr(srv.cfg, "abs", lambda p: tmp_path / p if not str(p).startswith(str(tmp_path)) else p)
    return tmp_path


def test_c1_meta_createur_pose_a_la_creation(tmp_path, monkeypatch):
    """create_party enregistre meta.createur ; list_parties l'expose."""
    import asyncio
    monkeypatch.setattr(srv, "_dossier_donnees", lambda: str(tmp_path))
    # create_party utilise cfg.abs(cfg.paths.data_dir) pour PartyState :
    monkeypatch.setattr(srv.cfg.paths, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(srv.cfg, "abs", lambda p: tmp_path)
    rep = asyncio.run(srv.create_party({"titre": "C1"}, utilisateur="Owner"))
    pid = rep["partie_id"]
    etat = PartyState(data_dir=str(tmp_path), partie_id=pid).load()
    assert etat["meta"]["createur"] == "Owner"
    listing = asyncio.run(srv.list_parties())
    assert listing["details"][pid]["createur"] == "Owner"


def test_c1_suppression_refusee_a_un_compte_etranger(tmp_path, monkeypatch):
    """Créateur enregistré + compte étranger → HTTP 403, partie intacte."""
    import asyncio

    from fastapi import HTTPException

    pid = _nonce_partie(tmp_path, createur="Alain", pjs=[
        {"nom": "Thorin", "joueur": "Alain"},
    ])
    monkeypatch.setattr(srv.cfg.paths, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(srv.cfg, "abs", lambda p: tmp_path)
    monkeypatch.setattr(srv.sessions, "pop", lambda x: None, raising=False)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv.delete_party(pid, utilisateur="Bob"))
    assert ei.value.status_code == 403
    # La partie existe toujours.
    etat = PartyState(data_dir=str(tmp_path), partie_id=pid).load()
    assert "_erreur" not in etat


def test_c1_suppression_autorisee_pour_le_proprietaire(tmp_path, monkeypatch):
    import asyncio

    pid = _nonce_partie(tmp_path, createur="Alain")
    monkeypatch.setattr(srv.cfg.paths, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(srv.cfg, "abs", lambda p: tmp_path)
    monkeypatch.setattr(srv.sessions, "pop", lambda x: None, raising=False)

    rep = asyncio.run(srv.delete_party(pid, utilisateur="alain"))  # casse ≠
    assert rep["ok"] is True
    assert not (tmp_path / f"partie_{pid}.json").exists()  # fichier supprimé


def test_c1_partie_ancienne_sans_createur_protegee(tmp_path, monkeypatch):
    """Partie legacy sans createur, avec PJ d'un autre joueur → 403."""
    import asyncio

    from fastapi import HTTPException

    pid = _nonce_partie(tmp_path, createur=None, pjs=[
        {"nom": "AutrePerso", "joueur": "QuelquUn"},
    ])
    monkeypatch.setattr(srv.cfg.paths, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(srv.cfg, "abs", lambda p: tmp_path)
    monkeypatch.setattr(srv.sessions, "pop", lambda x: None, raising=False)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv.delete_party(pid, utilisateur="Mallory"))
    assert ei.value.status_code == 403


def test_c1_historique_inclus_maintenant_client_id(session_history=None):
    """Le broadcast joueur embarque `client_id` (M2) — vérif par source."""
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "server", "main.py"), encoding="utf-8").read()
    assert '"client_id"' in src
    # Et le client affiche les messages joueurs reçus.
    csrc = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client", "src", "hooks", "useChatSocket.ts"), encoding="utf-8").read()
    assert 'case "player"' in csrc and "ownSayIds" in csrc
    # ws.ts joint le token (C2).
    wsrc = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client", "src", "api", "ws.ts"), encoding="utf-8").read()
    assert "getToken" in wsrc and "payload.token" in wsrc


# --------------------------------------------------------------------------- #
#  R2 — Jet manuel INFORMATIF (canal "dice", sans tour MJ)
# --------------------------------------------------------------------------- #
def test_r2_canal_dice_sans_tour_mj():
    """Un message WS type=dice est diffusé + mémorisé mais n'invoque PAS le
    MJ (avant : un tour LLM complet par clic, interprété en fiction)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(base, "server", "main.py"), encoding="utf-8").read()
    i = src.find('if mtype == "dice"')
    assert i >= 0, "canal 'dice' absent du handler WS"
    bloc = src[i : i + 1400]
    assert "remember_player_message" in bloc
    assert "session.broadcast" in bloc
    j = src.find("_handle_say", i)
    k = src.find('if mtype == "say"', i)
    assert k > i and j > k, "le canal dice ne doit pas invoquer _handle_say"


def test_r2_client_dice_envoie_le_nouveau_canal():
    """DiceRoller/PartyPage/ws.ts : le dé manuel passe par type="dice"."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wsrc = open(os.path.join(base, "client", "src", "api", "ws.ts"),
                encoding="utf-8").read()
    assert 'type: "dice"' in wsrc
    psrc = open(os.path.join(base, "client", "src", "pages", "PartyPage.tsx"),
                encoding="utf-8").read()
    assert "sendDice" in psrc
    rsrc = open(os.path.join(base, "client", "src", "components",
                             "RightSidebar.tsx"), encoding="utf-8").read()
    assert "sendDice" in rsrc


# --------------------------------------------------------------------------- #
#  R3 — Phase dédiée « game_over » en défaite
# --------------------------------------------------------------------------- #
def test_r3_defaite_passe_en_phase_game_over():
    """La clôture en défaite pose phase='game_over' (plus 'exploration') ;
    la victoire reste en exploration ; le patch le porte."""
    import asyncio

    from server.game.combat import cloturer, ResultatBoucle

    d = tempfile.mkdtemp()
    try:
        pid = "betar3"
        st = PartyState(data_dir=d, partie_id=pid)
        st.save({
            "phase": "combat",
            "pj": [{"nom": "Heros", "joueur": "J1", "pv": -10,
                    "conditions": ["Mort"]}],
            "monstres_combat": [{"nom": "Ogre", "pv": 5, "allie": False}],
        })
        res = ResultatBoucle()
        asyncio.run(cloturer(_ctx_stub(d, pid), res, "defaite"))
        etat = st.load()
        assert etat["phase"] == "game_over"
        assert etat["game_over"] is True
        assert res.phase == "game_over"
        assert res.patches[-1]["phase"] == "game_over"
        # Victoire : retour exploration.
        st2 = PartyState(data_dir=d, partie_id=pid)
        st2.save({
            "phase": "combat",
            "pj": [{"nom": "Heros", "joueur": "J1", "pv": 5}],
            "monstres_combat": [{"nom": "Ogre", "pv": 0, "allie": False,
                                 "conditions": ["Détruit"]}],
        })
        res2 = ResultatBoucle()
        asyncio.run(cloturer(_ctx_stub(d, pid), res2, "victoire"))
        etat2 = st2.load()
        assert etat2["phase"] == "exploration"
        assert etat2["phase"] != "game_over"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _ctx_stub(data_dir: str, pid: str):
    from server.tools.base import ToolContext
    return ToolContext(partie_id=pid, joueur="J1", data_dir=data_dir)


def test_r3_resurrection_repart_en_exploration():
    """La levée du game over (résurrection) ramène la phase en exploration."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(base, "server", "main.py"), encoding="utf-8").read()
    i = src.find("Levée du flag GAME OVER")
    assert i >= 0
    bloc = src[i : i + 700]
    assert '"phase"] = "exploration"' in bloc
    assert '"phase": "exploration"' in bloc


# --------------------------------------------------------------------------- #
#  R1 — Pénalité de résurrection niveau 1 : −2 CON (DMG 3.5) — DOCUMENTÉ
# --------------------------------------------------------------------------- #
def test_r1_penalie_con_niveau_1_est_officielle():
    """Le PV max qui baisse après une résurrection de niveau 1 N'EST PAS un
    bug : Raise Dead (DMG 3.5) sur un personnage de niveau 1 coûte −2 CON
    (irréparable) et le mod. CON réduit les PV max. Le code l'applique."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(base, "server", "main.py"), encoding="utf-8").read()
    assert "nouvelle_con = max(1, con - 2)" in src
    assert "perte_pvmax" in src


# --------------------------------------------------------------------------- #
#  R5 — Crash patch partiel sans nom (page blanche) — corrigé
# --------------------------------------------------------------------------- #
def test_r5_crash_patch_partiel_sans_nom_corrige():
    """Régression bêta (crash navigateur) : un patch d'état `pj.<i>.pv` appliqué
    sur un snapshot périmé crée une entrée pj PARTIELLE (sans nom) —
    `slugify(undefined)` plantait tout le panneau (page blanche). Le rendu
    tolère désormais les fiches sans nom (fallback slug + monogramme)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(base, "client", "src", "components",
                            "StateSidebar.tsx"), encoding="utf-8").read()
    # Portrait : nom optionnel + slug fallback
    assert "nom?: string" in src
    assert 'slugify(nom ?? "")' in src
    # PlayerCard : pj optionnel
    assert "pj?: Personnage" in src
    # Monogramme : jamais split sur undefined
    assert '(nom || "?")' in src
    # La carte pj map tolère les entrées nulles
    assert "pj={p ?? undefined}" in src


# --------------------------------------------------------------------------- #
#  R4 — Suppression de compte (auto-suppression authentifiée)
# --------------------------------------------------------------------------- #
def test_r4_suppression_de_compte(tmp_path, monkeypatch):
    """DELETE /api/auth/compte : parties créées + fiches + compte supprimés ;
    les tokens deviennent invalides."""
    import asyncio

    from fastapi import HTTPException

    from server import auth as auth_mod

    ok, _msg = auth_mod.creer_utilisateur(str(tmp_path), "Victime", "mdp1234")
    assert ok
    monkeypatch.setattr(srv.cfg.paths, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(srv.cfg, "abs", lambda p: tmp_path)
    rep = asyncio.run(srv.create_party({"titre": "R4"}, utilisateur="Victime"))
    pid = rep["partie_id"]
    # Fiche possédée.
    (tmp_path / "fiches").mkdir(exist_ok=True)
    fiche = {"nom": "PersoVictime", "proprietaire": "Victime",
             "race": "Humain", "classe": "Guerrier", "niveau": 1,
             "carac": {}, "pv": 10, "pv_max": 10, "equipement": []}
    (tmp_path / "fiches" / "fiche_persovictime.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8")

    token = auth_mod.generer_token(str(tmp_path), "Victime")
    assert auth_mod.verifier_token(str(tmp_path), token) == "Victime"

    rep2 = asyncio.run(srv.auth_supprimer_compte(utilisateur="Victime"))
    assert rep2["ok"] is True
    assert pid in rep2["parties_supprimees"]
    assert "PersoVictime" in rep2["fiches_supprimees"]
    # Compte supprimé → token invalide.
    assert auth_mod.verifier_token(str(tmp_path), token) is None
    assert not (tmp_path / "fiches" / "fiche_persovictime.json").exists()
    assert not (tmp_path / f"partie_{pid}.json").exists()

    # Compte inexistant → 404.
    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv.auth_supprimer_compte(utilisateur="Fantome"))
    assert ei.value.status_code == 404


def test_r4_les_parties_d_autrui_sont_conservees(tmp_path, monkeypatch):
    """La suppression de compte NE touche PAS les parties des autres."""
    import asyncio

    from server import auth as auth_mod

    auth_mod.creer_utilisateur(str(tmp_path), "Victime2", "mdp1234")
    monkeypatch.setattr(srv.cfg.paths, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(srv.cfg, "abs", lambda p: tmp_path)
    # Partie créée par un AUTRE compte.
    asyncio.run(srv.create_party({"titre": "AutreTable"}, utilisateur="Alain"))
    asyncio.run(srv.auth_supprimer_compte(utilisateur="Victime2"))
    # La partie d'Alain existe toujours.
    listing = asyncio.run(srv.list_parties())
    assert any(
        d["titre"] == "AutreTable" for d in listing["details"].values()
    )
