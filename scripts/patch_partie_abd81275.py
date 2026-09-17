# -*- coding: utf-8 -*-
"""Patch de données (partie abd81275 + manifeste Crown of Mystra) :

Contexte (examen de la partie abd81275) : le MJ était confus car
1. le manifeste aplatissait tout Faerûn en un donjon 5×4 — le temple de
   Silverymoon (0,0) débouchait DIRECTEMENT sur la grotte de Nulentok (1,0),
   et chaque site-gemme était une « salle » adjacente, contredisant la trame
   (« aucun voyage vers les gemmes avant la Couronne ») ;
2. `quete.bible.ennemis` ne contenait que « Squelette » (détection sur
   l'extrait PDF) — le manifeste, lui, liste gobelins, goules, rats, ombres
   et nécromancien rouge.

Ce script (idempotent) :
- préfixe les notes des salles-gemmes du MANIFESTE avec la convention
  « lieu de Faerûn — porte = téléportation, scellée avant l'étape 1 » ;
- applique descriptions/notes du manifeste à la grille de la partie EN
  COURS (sans toucher visited/courant/portes : aucune remise à zéro) ;
- reconstitue `quete.bible.ennemis` avec la liste complète du module ;
- journalise l'intervention dans `histoire`.

NB : le correctif code (persos.enregistrer_personnage_partie) soigne
désormais le perso à l'entrée dans une NOUVELLE partie — ici on NE touche
PAS aux PV : le groupe est en jeu, ses PV actuels sont légitimes.
"""
import json
from datetime import datetime

MANIFESTE = (
    "server/data/scenarios/Les Royaumes Oubliés/"
    "The Crown of Mystra.donjon.json"
)
PARTIE = "server/data/partie_abd81275.json"

PREFIXE = (
    "📍 LIEU DE FAERÛN — cette salle n'est PAS physiquement adjacente à la "
    "précédente : sa « porte » est un SAUT DE TÉLÉPORTATION de la baguette "
    "de Teleshann (voyage instantané), SCELLÉ tant que la Couronne n'a pas "
    "été reprise à Nulentok (étape 1 ⬜). "
)

# Salles « voyage » (tout sauf la grotte de Nulentok (0,0)-(4,0)).
SALLES_VOYAGE = [
    (4, 1), (3, 1), (2, 1), (1, 1), (0, 1), (0, 2), (1, 2), (2, 2),
    (3, 2), (4, 2), (4, 3), (3, 3), (2, 3), (1, 3), (0, 3),
]

# Ennemis canoniques du module (grille du manifeste), liste complète.
ENNEMIS = ["Squelette", "Gobelin", "Goule", "Rat", "Ombre", "Nécromancien_rouge"]


def _prefixe_note(note: str) -> str:
    note = str(note or "").strip()
    if note.startswith("📍 LIEU DE FAERÛN"):
        return note  # déjà patché (idempotence)
    return PREFIXE + note


# ── 1. Manifeste (référence pour les futures parties / ré-entrées) ──────
man = json.load(open(MANIFESTE, encoding="utf-8"))
n_manifeste = 0
for etage in man.get("etages", []):
    for salle in etage.get("salles", []):
        if (salle.get("x"), salle.get("y")) in SALLES_VOYAGE:
            salle["note"] = _prefixe_note(salle.get("note"))
            n_manifeste += 1
with open(MANIFESTE, "w", encoding="utf-8", newline="\n") as f:
    json.dump(man, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"manifeste OK : {n_manifeste} salle(s)-gemme(s) préfixée(s) — {MANIFESTE}")

# ── 2. Partie en cours : grille resynchronisée (sans reset d'exploration) ──
partie = json.load(open(PARTIE, encoding="utf-8"))
donjon = partie.setdefault("donjon", {})
salles_partie = {
    (s.get("x"), s.get("y")): s
    for s in donjon.get("grille", [])
    if isinstance(s, dict) and "x" in s
}
man_salles = {
    (s.get("x"), s.get("y")): s
    for etage in man.get("etages", [])
    for s in etage.get("salles", [])
}
n_sync = 0
for xy, salle in salles_partie.items():
    ref = man_salles.get(xy)
    if ref is None:
        continue
    avant = (salle.get("description"), salle.get("note"))
    salle["description"] = ref.get("description", salle.get("description"))
    salle["note"] = ref.get("note", salle.get("note"))
    if (salle.get("description"), salle.get("note")) != avant:
        n_sync += 1
# Propage aussi la trame du manifeste (etapes) + convention au niveau donjon.
if man.get("etapes"):
    donjon["etapes"] = man["etapes"]

# ── 3. Bible : liste d'ennemis complète (fidèle au module) ──────────────
bible = partie.setdefault("quete", {}).setdefault("bible", {})
avant_ennemis = list(bible.get("ennemis") or [])
bible["ennemis"] = ENNEMIS

# ── 4. Journal de l'intervention ─────────────────────────────────────────
partie.setdefault("histoire", []).append({
    "ts": datetime.now().isoformat(),
    "tour": "",
    "evenement": (
        "⚙️ Correction du scénario : la grotte de Nulentok est le seul vrai "
        "donjon ((0,0)→(4,0), exploration à pied) ; les autres salles sont "
        "des lieux de Faerûn reliés par la téléportation de la baguette — "
        "scellée tant que la Couronne n'est pas reprise à Nulentok. "
        "Ennemis du module : " + ", ".join(ENNEMIS) + "."
    ),
})

with open(PARTIE, "w", encoding="utf-8", newline="\n") as f:
    json.dump(partie, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"partie OK : {n_sync} salle(s) resynchronisee(s), ennemis "
      f"{avant_ennemis} -> {ENNEMIS} — {PARTIE}")
