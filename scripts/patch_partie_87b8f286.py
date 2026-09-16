# -*- coding: utf-8 -*-
"""Patch de données (partie 87b8f286 + manifeste Crown of Mystra) :
- trame du scénario (etapes) portée par le donjon ;
- flag game_over sur la partie en cours (TPK : Throk'mar mort)."""
import json
import datetime

ETAPES = [
    {
        "titre": "Récupérer la Couronne de Mystra chez Zendar Nulentok",
        "salle": "4,0",
        "detail": (
            "PRÉREQUIS ABSOLU : vaincre Nulentok dans son groove (le puits "
            "(3,0) y mène) et reprendre la Couronne. La baguette de "
            "téléportation de Thukmuul Teleshann ne s'active QU'AU CONTACT "
            "de la Couronne — aucun voyage vers les gemmes n'est possible "
            "avant."
        ),
    },
    {
        "titre": "Réunir les huit gemmes dispersées sur Faerûn",
        "detail": (
            "Beljuril — Ruines de Sarr (4,1) ; Diamant — échoppe d'Aluarim "
            "(3,1) ; Jacinthe — QG zhentarim d'Olomaa (0,1) ; Rogue Stone — "
            "Calimport (0,2) ; trésor de Rineen (1,2) ; Émeraude de Shou "
            "Lung — temple de Farouk (4,2) ; Saphir étoilé — crevasse de "
            "Shakkak (4,3) ; Étoile de rubis — château de Meredoth (0,3)."
        ),
    },
    {
        "titre": "Restaurer la Couronne et la rendre à Mystra",
        "detail": (
            "Réactiver la Couronne avec les huit gemmes et la rapporter au "
            "haut lieu de Mystra (Tour de l'Équilibre, Silverymoon — (0,0))."
        ),
    },
]

MANIFESTE = "server/data/scenarios/Les Royaumes Oubliés/The Crown of Mystra.donjon.json"
PARTIE = "server/data/partie_87b8f286.json"

man = json.load(open(MANIFESTE, encoding="utf-8"))
man["etapes"] = ETAPES
with open(MANIFESTE, "w", encoding="utf-8", newline="\n") as f:
    json.dump(man, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("manifeste OK :", MANIFESTE)

partie = json.load(open(PARTIE, encoding="utf-8"))
partie["game_over"] = True
partie.setdefault("donjon", {})["etapes"] = ETAPES
partie.setdefault("histoire", []).append({
    "ts": datetime.datetime.now().isoformat(),
    "tour": "",
    "evenement": (
        "💀 Game over — Throk'mar est tombé (-10 PV). La partie attend la "
        "décision de la table : nouvelle partie, résurrection ou reprise."
    ),
})
with open(PARTIE, "w", encoding="utf-8", newline="\n") as f:
    json.dump(partie, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("partie OK :", PARTIE)
