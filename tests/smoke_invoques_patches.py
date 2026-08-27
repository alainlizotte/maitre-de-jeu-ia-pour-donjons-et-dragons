"""Smoke test des correctifs temps réel + invoqués de combat (sans LLM).

Vérifie :
  1. Détection du pattern de simulation « (L'application de l'outil ...) ».
  2. fiche_perso_infliger_degats → state_patch contient pj.<i>.pv (path patch).
  3. combat_ajouter_combattant : insertion mid-combat SANS reset (allié + ennemi,
     désambiguïsation d'homonymes), PV suivis dans monstres_combat.
  4. Dégâts sur monstre → patch monstres_combat complet.
  5. Refus hors combat avec message guidant vers engager_combat.

Usage : py tests/smoke_invoques_patches.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import _TOOL_REGISTRY as REG  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.llm.orchestrator import looks_like_simulation  # noqa: E402

# Charge les modules de tools (enregistre les @tool).
import server.tools.state  # noqa: F401,E402
import server.tools.fiches  # noqa: F401,E402
import server.tools.dice  # noqa: F401,E402
import server.tools.monstres  # noqa: F401,E402
import server.tools.voyage  # noqa: F401,E402
import server.tools.cartes  # noqa: F401,E402
import server.tools.manuels  # noqa: F401,E402
import server.tools.scenarios  # noqa: F401,E402

ok_count = 0


def check(label: str, cond: bool) -> None:
    global ok_count
    if not cond:
        print(f"❌ {label}")
        sys.exit(1)
    ok_count += 1
    print(f"✅ {label}")


async def main() -> None:
    data_dir = tempfile.mkdtemp(prefix="dnd35_smoke_")
    pid = "smoke_invoque"
    ctx = ToolContext(partie_id=pid, joueur="alice", data_dir=data_dir)

    try:
        # --- 1. Pattern de simulation ------------------------------------ #
        sim_txt = (
            "(L'application de l'outil fiche_perso_infliger_degats met à "
            "jour la fiche de l'Entité des Profondeurs : PV -3)"
        )
        frag = looks_like_simulation(sim_txt)
        check(
            "pattern « L'application de l'outil » détecté comme simulation",
            bool(frag),
        )
        check(
            "narration légitime NON marquée simulation",
            looks_like_simulation(
                "Le naga plonge sa lance vers Borin et le blesse grièvement."
            ) is None,
        )

        # --- 2. Setup : fiche PJ ------------------------------------------ #
        st = PartyState(data_dir=data_dir, partie_id=pid)
        etat = st.load()
        etat["pj"] = [{
            "nom": "Brunhild", "joueur": "alice", "race": "Nain",
            "classe": "Guerrier", "niveau": 1, "pv": 12, "pv_max": 12,
            "ca": 16, "carac": {"FOR": 16, "DEX": 12, "CON": 14},
        }]
        st.save(etat)
        # Fiche JSON sur disque (pour les tools de fiches).
        import json
        os.makedirs(os.path.join(data_dir, "fiches"), exist_ok=True)
        with open(
            os.path.join(data_dir, "fiches", "fiche_brunhild.json"), "w",
            encoding="utf-8",
        ) as f:
            json.dump({
                "nom": "Brunhild", "joueur": "alice", "race": "Nain",
                "classe": "Guerrier", "niveau": 1,
                "carac": {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 9, "CHA": 8},
                "pv": 12, "pv_max": 12, "ca": 16,
                "sauvegardes": {"Vigueur": 4, "Reflexes": 0, "Volonte": 0},
                "bab": 1, "competences": {}, "dons": [], "equipement": [],
                "or": 0, "alignement": "Loyal Bon", "histoire": "",
                "conditions": [],
            }, f, ensure_ascii=False)

        tr = await invoke_tool(
            REG["fiche_perso_infliger_degats"], ctx,
            {"nom": "Brunhild", "degats": 4},
        )
        check("dégâts PJ appliqués (PV 12→8)", "PV 8/12" in tr.text)
        check(
            "state_patch contient le path patch pj.0.pv=8",
            tr.state_patch is not None
            and tr.state_patch.get("pj.0.pv") == 8
            and tr.state_patch.get("pj_updated") == "Brunhild",
        )
        etat = st.load()
        check("état disque synchronisé (pj[0].pv=8)", etat["pj"][0]["pv"] == 8)

        # --- 3. Combat + invoqués ----------------------------------------- #
        tr = await invoke_tool(
            REG["engager_combat"], ctx, {"monstres": "Gobelin"}
        )
        check("engager_combat OK", "Combat engagé" in tr.text)
        etat = st.load()
        check("courant_tour_pour défini", bool(etat.get("courant_tour_pour")))

        # Allié invoqué (côté joueurs).
        tr = await invoke_tool(
            REG["combat_ajouter_combattant"], ctx,
            {"nom": "Loup", "allie": True},
        )
        check("combat_ajouter_combattant (allié) OK", "rejoint le combat" in tr.text)
        etat2 = st.load()
        noms_init = [e["nom"] for e in etat2["initiative"]]
        check("Loup présent dans l'initiative", "Loup" in noms_init)
        check(
            "combat NON réinitialisé (courant_tour_pour inchangé)",
            etat2.get("courant_tour_pour") == etat.get("courant_tour_pour"),
        )
        loup = next(
            (m for m in etat2["monstres_combat"] if m["nom"] == "Loup"), None
        )
        check(
            "Loup suivi dans monstres_combat avec allie=True",
            loup is not None and loup.get("allie") is True,
        )
        check(
            "state_patch expose initiative + monstres_combat",
            tr.state_patch is not None
            and "initiative" in tr.state_patch
            and "monstres_combat" in tr.state_patch,
        )

        # Ennemi homonyme → désambiguïsation.
        tr = await invoke_tool(
            REG["combat_ajouter_combattant"], ctx, {"nom": "Loup"}
        )
        etat3 = st.load()
        noms_init = [e["nom"] for e in etat3["initiative"]]
        check(
            "homonyme désambiguïsé (« Loup (2) »)",
            "Loup (2)" in noms_init and noms_init.count("Loup") == 1,
        )
        loup2 = next(
            (m for m in etat3["monstres_combat"] if m["nom"] == "Loup (2)"), None
        )
        check(
            "ennemi homonyme sans flag allie",
            loup2 is not None and not loup2.get("allie"),
        )

        # Initiative fournie explicitement.
        tr = await invoke_tool(
            REG["combat_ajouter_combattant"], ctx,
            {"nom": "Araignée géante", "initiative": 25},
        )
        etat4 = st.load()
        check(
            "initiative explicite respectée (25 en tête)",
            etat4["initiative"][0]["nom"] == "Araignée géante"
            and etat4["initiative"][0]["init"] == 25,
        )

        # --- 4. Dégâts sur monstre ---------------------------------------- #
        tr = await invoke_tool(
            REG["fiche_perso_infliger_degats"], ctx,
            {"nom": "Gobelin", "degats": 3},
        )
        check("dégâts monstre appliqués", "Gobelin" in tr.text and "3 dégâts" in tr.text)
        check(
            "patch monstres_combat complet renvoyé",
            tr.state_patch is not None and "monstres_combat" in tr.state_patch,
        )
        etat5 = st.load()
        gob = next(
            (m for m in etat5["monstres_combat"] if m["nom"] == "Gobelin"), None
        )
        check(
            "PV du Gobelin suivis sur disque",
            gob is not None and gob["pv"] == gob["pv_max"] - 3,
        )

        # --- 4bis. Journal des rencontres (persistance galerie) ------------ #
        from server.tools.monstres import _memoriser_rencontre
        _memoriser_rencontre(ctx, "Gobelin", "/data/bestiaire_cache/gobelin.png")
        _memoriser_rencontre(ctx, "Loup", "/data/bestiaire_cache/loup.png")
        _memoriser_rencontre(ctx, "Gobelin", "/data/bestiaire_cache/gobelin2.png")
        etat6 = st.load()
        journal = etat6.get("rencontres_images") or []
        noms_journal = [r["nom"] for r in journal]
        check(
            "journal rencontres : chaque monstre une seule fois",
            noms_journal.count("Gobelin") == 1 and "Loup" in noms_journal,
        )
        gob6 = next(
            (m for m in etat6["monstres_combat"] if m["nom"] == "Gobelin"), None
        )
        check(
            "monstres_combat[Gobelin].image_url synchronisé (dernière URL)",
            gob6 is not None and gob6.get("image_url")
            == "/data/bestiaire_cache/gobelin2.png",
        )
        _memoriser_rencontre(ctx, "Gobelin", "/data/bestiaire_cache/gobelin2.png")
        etat7 = st.load()
        check(
            "rappel à l'identique = pas de duplication",
            len(etat7.get("rencontres_images") or []) == len(journal),
        )

        # --- 5. Refus hors combat ----------------------------------------- #
        st2 = PartyState(data_dir=data_dir, partie_id=pid)
        e = st2.load()
        e["phase"] = "exploration"
        e["initiative"] = []
        st2.save(e)
        tr = await invoke_tool(
            REG["combat_ajouter_combattant"], ctx, {"nom": "Loup"}
        )
        check(
            "refus hors combat avec guidage vers engager_combat",
            tr.text.startswith("❌") and "engager_combat" in tr.text,
        )
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)

    print(f"\n🎉 {ok_count} vérifications passées — nettoyage effectué.")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
