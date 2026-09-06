"""Kit de rédaction des descriptions de salles pour un manifeste de donjon.

Extrait du PDF du scénario les passages utiles à la rédaction fidèle des
descriptions de salles (sections du donjon, textes d'ambiance), en filtrant
les statblocks 5e (Armor Class / Hit Points / Dex 14…) qui polluent le texte.

Usage :
    py scripts/kit_redaction.py --scenario laelith_loeil_de_gruumsh
    py scripts/kit_redaction.py --scenario <id> --sortie kit.txt
    py scripts/kit_redaction.py --scenario <id> --injecter redaction.json

Injection (`--injecter`) : un JSON { "(x,y) | (étage)": "description", … }
ou {"descriptions": {"x,y": "...", ...}, "types": {"x,y": "nouveau type"}}
fusionné dans le manifeste (les salles sans entrée conservent leur valeur).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RACINE))
sys.path.insert(0, str(_RACINE / "scripts"))

_MARQUEURS_STATBLOCK = (
    "Armor Class", "Hit Points", "Hit:", "Speed", "Saving Throws",
    "Skills", "Senses", "Languages", "Challenge", "Classe d'armure",
    "Points de vie", "Vitesse", "Jets de sauvegarde", "Défi",
    "Dex ", "Con ", "STR", "DEX", "CON", "INT", "WIS", "CHA",
)


def _resoudre_pdf(sid: str) -> Path | None:
    from generer_donjon_scenario import _resoudre_pdf
    return _resoudre_pdf(sid)


def _manifeste(sid: str) -> Path | None:
    pdf = _resoudre_pdf(sid)
    if pdf is None:
        return None
    dest = pdf.with_name(pdf.stem + ".donjon.json")
    return dest if dest.is_file() else None


def _filtrer_statblocks(texte: str) -> str:
    """Retire les lignes de statblock 5e (métadonnées de créature)."""
    lignes_gardees = []
    for ligne in texte.splitlines():
        l = ligne.strip()
        if any(m in l for m in _MARQUEURS_STATBLOCK):
            continue
        # Lignes "12 (2d6 + 5)" — valeurs seules des statblocks.
        if re.fullmatch(r"[0-9]+ \([0-9d+ ]+\)", l):
            continue
        lignes_gardees.append(ligne)
    return "\n".join(lignes_gardees)


def kit(sid: str, plafond: int = 30000) -> str:
    from generer_donjon_scenario import _extraire_texte
    pdf = _resoudre_pdf(sid)
    if pdf is None:
        return f"❌ PDF introuvable pour {sid}"
    texte = _extraire_texte(pdf, plafond=200000)
    texte = _filtrer_statblocks(texte)
    # Compresse les lignes vides multiples.
    texte = re.sub(r"\n{3,}", "\n\n", texte)
    en_tete = (
        f"=== KIT DE RÉDACTION — {sid} ===\n"
        f"Manifeste : {_manifeste(sid)}\n"
        f"Repère les sections du donjon (zones) et rédige UNE description "
        f"courte (1-3 phrases, français, ambiance) par salle du manifeste.\n"
        + "=" * 60 + "\n"
    )
    return en_tete + texte


def injecter(sid: str, redaction_path: Path) -> int:
    man_path = _manifeste(sid)
    if man_path is None:
        print(f"❌ Manifeste introuvable pour {sid}")
        return 1
    man = json.loads(man_path.read_text(encoding="utf-8"))
    data = json.loads(redaction_path.read_text(encoding="utf-8"))
    descriptions: dict[str, str] = data.get("descriptions") or {}
    types: dict[str, str] = data.get("types") or {}
    etats: dict[str, str] = data.get("etats_des_lieux") or {}
    touches = 0
    for fl in man.get("etages") or []:
        for s in fl.get("salles") or []:
            cle = f"{s['x']},{s['y']}"
            if cle in descriptions:
                s["description"] = descriptions[cle].strip()[:600]
                touches += 1
            if cle in types:
                s["type"] = types[cle].strip()
            if cle in etats:
                s["etat_des_lieux"] = etats[cle].strip()[:400]
    man_path.write_text(
        json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✅ {touches} description(s) injectée(s) dans {man_path.name}")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                            # noqa: BLE001
        pass
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--scenario", required=True)
    parseur.add_argument("--sortie", default=None,
                         help="fichier kit (défaut : stdout)")
    parseur.add_argument("--injecter", default=None,
                         help="JSON de rédaction à fusionner dans le manifeste")
    args = parseur.parse_args()
    if args.injecter:
        return injecter(args.scenario, Path(args.injecter))
    texte = kit(args.scenario)
    if args.sortie:
        Path(args.sortie).write_text(texte, encoding="utf-8")
        print(f"Kit écrit : {args.sortie} ({len(texte)} car)")
    else:
        print(texte)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
