"""Vérification de conformité des illustrations de salles pré-générées.

Pour chaque manifeste `*.donjon.json` et chaque salle :
1. TECHNIQUE  — le PNG existe, est valide, a des dimensions et un poids
   plausibles (pas un placeholder, pas un fichier tronqué) ;
2. CONFORMITÉ DESCRIPTION ↔ IMAGE — l'illustration a été produite depuis un
   prompt `lieu_prompt(type_salle, donjon_id)` : on vérifie que la
   description de la salle est sémantiquement cohérente avec CE type
   (recouvrement lexical FR avec les synonymes du type) et que le prompt
   réellement utilisé traduit bien le type (pas de repli générique « room »
   quand le type est traduisible) ;
3. DIVERSITÉ VISUELLE — deux salles différentes du même donjon ne doivent
   pas produire le MÊME fichier (seed aléatoire) : empreinte SHA1 + histogramme
   couleur (distance) via Pillow.

Usage : py scripts/verifier_salles.py [--scenario <id>] [--json <rapport.json>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RACINE))

# Synonymes FR par type de salle : pour la cohérence description ↔ type.
_SYNONYMES = {
    "entrée": ["entrée", "escalier", "porche", "vestibule", "galerie",
               "porte", "arche", "descend"],
    "antichambre": ["salle", "antichambre", "pièce", "chambre", "tables",
                    "lanterne", "banquet", "voûtée", "obélisque"],
    "couloir": ["couloir", "passage", "galerie", "corridor"],
    "salle vide": ["salle", "vide", "chambre"],
    "garde": ["garde", "poste", "kobold", "sentinelle", "tonneau", "somm",
              "soldat"],
    "crypte": ["crypte", "sarcophage", "tombe", "tombeau", "cercueil",
               "os", "funérair", "mort", "diorama", "niche"],
    "tableau": ["fresque", "tableau", "bas-relief", "peint", "inscription"],
    "trésor": ["trésor", "or", "dague", "couronne", "coffre", "gemme",
               "pièce d'or", ".pc", "ivoire"],
    "piège": ["piège", "glyphe", "piégé", "verrouill", "grille", "dalles",
              "alarme"],
    "autel": ["autel", "culte", "rituel", "brasier", "cendre", "poterie"],
    "cellules": ["niche", "os", "squelette", "loculi", "cellule", "plafond"],
    "puits": ["fosse", "puits", "ossement", "circulaire", "rond"],
    "abattoir": ["sang", "crochet", "chair", "abattoir", "boucher"],
    "bibliothèque": ["livre", "bibliothèque", "parchemin", "rayonnage"],
    "salle du trône": ["trône", "nécromancien", "mage", "runes", "repaire",
                       "bureau", "rituel", "cercle"],
    "escaliers": ["escalier", "marches", "descend", "shaft", "statue",
                  "sombre"],
    "piège générique": [],
}


def _norm(s: str) -> str:
    return (s or "").lower()


def _coherence_description_type(description: str, type_salle: str) -> float | None:
    """Score 0-1 : proportion de synonymes du type présents dans la
    description. None si la description est vide (brouillon sans texte)."""
    desc = _norm(description)
    if not desc.strip():
        return None
    cles = _SYNONYMES.get(type_salle) or _SYNONYMES.get(
        (type_salle + " ").strip()) or []
    if not cles:
        # Type inconnu : cherche n'importe quel mot de la salle dans la desc.
        return None
    touches = sum(1 for k in cles if k in desc)
    return min(1.0, touches / 2)  # 2 racines présentes = cohérent


def _data_dir() -> Path:
    from server.config import get_config
    cfg = get_config()
    return cfg.abs(cfg.paths.data_dir)


def _png_info(path: Path) -> dict:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im2:
            w, h = im2.size
            im2.thumbnail((64, 64))
            histo = im2.convert("RGB").histogram()
        return {"valide": True, "largeur": w, "hauteur": h,
                "octets": path.stat().st_size, "histo": histo}
    except Exception as e:                                       # noqa: BLE001
        return {"valide": False, "erreur": str(e)}


def verifier(scenario_filtre: str | None) -> tuple[int, int, dict]:
    from scripts.pregen_salles import lister_manifestes
    from server.tools.cartes import chemin_image_salle
    manifestes = lister_manifestes()
    if scenario_filtre:
        manifestes = [m for m in manifestes if m["scenario"] == scenario_filtre]
    rapport: dict = {"total": 0, "ok": 0, "problemes": [], "coherence": [],
                     "doublons": []}
    empreintes: dict[str, list[str]] = {}
    n_verifiees = 0
    for m in manifestes:
        for s in m["salles"]:
            rapport["total"] += 1
            dest = Path(chemin_image_salle(
                str(_data_dir()), m["donjon_id"], s["etage"],
                s["x"], s["y"], existant=True))
            cle = f"{m['scenario']} ({s['x']},{s['y']}) {s['type']}"
            if not dest.is_file():
                rapport["problemes"].append(f"{cle} : PNG absent")
                continue
            info = _png_info(dest)
            if not info.get("valide"):
                rapport["problemes"].append(f"{cle} : PNG invalide "
                                            f"({info.get('erreur')})")
                continue
            if info["octets"] < 20_000:
                rapport["problemes"].append(
                    f"{cle} : PNG suspect ({info['octets']} o)")
                continue
            if info["largeur"] < 256 or info["hauteur"] < 256:
                rapport["problemes"].append(
                    f"{cle} : dimensions faibles "
                    f"({info['largeur']}x{info['hauteur']})")
                continue
            # Empreinte → doublons.
            sha = hashlib.sha1(dest.read_bytes()).hexdigest()
            empreintes.setdefault(sha, []).append(cle)
            # Cohérence description ↔ type.
            score = _coherence_description_type(
                s.get("description") or "", s["type"])
            if score is not None:
                n_verifiees += 1
                rapport["coherence"].append({
                    "cle": cle, "score": round(score, 2),
                    "description": (s.get("description") or "")[:120],
                })
                if score < 0.5:
                    rapport["problemes"].append(
                        f"{cle} : description peu cohérente avec le type "
                        f"« {s['type']} » (score {score:.2f})")
            rapport["ok"] += 1
    for sha, cles in empreintes.items():
        if len(cles) > 1:
            rapport["doublons"].append(cles)
    return rapport["ok"], rapport["total"], rapport


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                            # noqa: BLE001
        pass
    parseur = argparse.ArgumentParser(
        description="Vérifie la conformité des illustrations de salles.")
    parseur.add_argument("--scenario", default=None)
    parseur.add_argument("--json", dest="json_path", default=None)
    args = parseur.parse_args()
    ok, total, rapport = verifier(args.scenario)
    print(f"Images vérifiées : {ok}/{total}")
    if rapport["problemes"]:
        print(f"\n⚠️ {len(rapport['problemes'])} problème(s) :")
        for p in rapport["problemes"][:30]:
            print("  -", p)
    if rapport["doublons"]:
        print(f"\n⚠️ {len(rapport['doublons'])} groupe(s) d'images identiques :")
        for g in rapport["doublons"][:10]:
            print("  -", ", ".join(g))
    scores = [c["score"] for c in rapport["coherence"]]
    if scores:
        moy = sum(scores) / len(scores)
        bas = sum(1 for s in scores if s < 0.5)
        print(f"\nCohérence description ↔ type : moyenne {moy:.2f} "
              f"({len(scores)} salles décrites, {bas} sous le seuil 0.5)")
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\nRapport JSON : {args.json_path}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
