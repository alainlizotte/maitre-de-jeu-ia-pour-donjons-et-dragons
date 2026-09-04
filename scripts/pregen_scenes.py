# -*- coding: utf-8 -*-
"""Pré-génère les images de scènes d'un (ou plusieurs) scénario en cache.

But : chauffer à l'avance les illustrations des scènes marquantes d'un
scénario pour que `illustration_scene` les serve instantanément au lieu de
solliciter ComfyUI pendant la partie (aucun délai ressenti).

Usage (depuis la racine du projet) :
  py scripts/pregen_scenes.py --scenario divers_dues_for_the_dead
  py scripts/pregen_scenes.py --all                    # tous les scénarios
  py scripts/pregen_scenes.py --scenario ro_royaumes_oublies \
      --scenes "L'autel maudit|Le héros affronte le gardien de pierre" \
             "La relique|Une lumière dorée émane du sarcophage"

Chaque scène est générée via ComfyUI (usage « lieu », comme illustration_scene)
dans `data/images_scenes/pregen/` et référencée dans
`data/images_scenes/pregen/<scenario_id>.json`. Au runtime, illustration_scene
consulte ce manifest par slug == `_slug_image(titre ou description)` : en cas
de correspondance, l'image en cache est servie immédiatement.

Scènes auto-dérivées du PDF : on repère les phrases narrativement pertinentes
(Overview / Introduction / scènes d'action) et on les découpe en candidats
bruts — qualité variable selon la mise en page ; préférez des `--scenes`
explicites pour les scénarios dont on veut garantir le rendu.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.abspath("."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import _slug_image  # noqa: E402
from server.tools.scenarios import charger_catalogue, extraire_pdf, _url_to_path  # noqa: E402
from server.image.helpers import get_backend, scene_prompt  # noqa: E402

DATA_DIR = os.path.abspath("server/data")
PREGEN_DIR = os.path.join(DATA_DIR, "images_scenes", "pregen")


# --- Utilitaire : normalisation pour un hash stable (indépendant des accents/
# casses) servant de clé secondaire au manifest. ----------------------------
def _norm(texte: str) -> str:
    nf = unicodedata.normalize("NFKD", (texte or "").lower())
    return "".join(c for c in nf if not unicodedata.combining(c))


def _scenes_catalogue(sid: str) -> list[tuple[str, str]]:
    """Charge les scènes curées (lieux, événements, PNJ, trésor) pour `sid`.

    Cherche `data/scenes_catalogue_<univers>.py` puis les modules
    `scenes_catalogue_*.py` proches du scénario ; renvoie la liste de
    (titre, description) si le scénario y figure, sinon [].
    """
    import importlib.util
    scenes_dir = os.path.join(DATA_DIR, "scenes_catalogue_laelith.py")
    # Le catalogue curé est un dict : {scenario_id: [(titre, desc), ...]}
    for path in (
        os.path.join(os.path.dirname(DATA_DIR), "data", "scenes_catalogue_laelith.py"),
        scenes_dir,
    ):
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location("_scenes_cat", path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:                                   # noqa: BLE001
            continue
        data = getattr(mod, "SCENES_LAELITH", {})
        if sid in data:
            return list(data[sid])
    return []


# --------------------------------------------------------------------------- #
#  Dérivation de candidats scènes depuis le texte du PDF
# --------------------------------------------------------------------------- #
_SECTION_RE = re.compile(
    r"(overview|introduction|part \d|parti \d|chapter \d|chapitre \d|"
    r"scene|scène|then the|ensuite|meanwhile|pendant ce temps|la lumi|"
    r"révélation|reveal)",
    re.IGNORECASE,
)


def _candidats_du_pdf(texte: str, limite: int = 6) -> list[str]:
    """Extrait des phrases narratives du PDF comme candidats bruts de scènes."""
    if not texte:
        return []
    # Ne retient que des paragraphes/segments lisibles (>= 40 car., < 600),
    # sans les balises d'extraction ni les gros blocs de « --- ».
    seg = re.split(r"\n---\n|\n\s*\n", texte)
    candidats: list[str] = []
    for s in seg:
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) < 40:
            continue
        if "⚠️ (extrait" in s or "pymupdf" in s:
            continue
        if not _SECTION_RE.search(s):
            # On garde la première phrase si le segment démarre par « The » /
            # « Le » / « L' » (intro narrative).
            if not re.match(r"^(the |a |le |la |l'|une |un )", s, re.I):
                continue
        # Coupe à la première phrase complète
        phrase = s.split(". ", 1)[0].strip().strip(".") if "." in s else s
        if len(phrase) >= 25:
            candidats.append(phrase)
        if len(candidats) >= limite:
            break
    return candidats


# --------------------------------------------------------------------------- #
#  Génération + manifest
# --------------------------------------------------------------------------- #
async def _generer_scene(backend, titre: str, description: str, dest: str) -> bool:
    prompt = scene_prompt(description)
    try:
        path, _seed = await backend.generer("lieu", prompt, dest)
        return bool(path and os.path.isfile(path))
    except Exception as e:                               # noqa: BLE001
        print(f"    ✗ échec génération : {type(e).__name__}: {e}")
        return False


async def _pregen(backend, scenario_id: str, scenes: list[tuple[str, str]]) -> int:
    """Génère les scènes et écrit/merge le manifest. Renvoie le nb généré."""
    os.makedirs(PREGEN_DIR, exist_ok=True)
    manifest_path = os.path.join(PREGEN_DIR, f"{scenario_id}.json")
    manifest: dict = {}
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError):
            manifest = {}

    fait = 0
    for i, (titre, description) in enumerate(scenes):
        slug = _slug_image(titre or description)
        if slug in manifest and os.path.isfile(os.path.join(PREGEN_DIR, manifest[slug]["file"])):
            print(f"  – {titre or description[:40]} : déjà en cache, on saute")
            continue
        dest = os.path.join(PREGEN_DIR, f"{scenario_id}_{i:02d}_{slug}.png")
        print(f"  → {titre or description[:60]}")
        ok = await _generer_scene(backend, titre, description, dest)
        if not ok:
            print("      ⏭ (skip — ComfyUI n'a pas produit d'image)")
            continue
        manifest[slug] = {
            "titre": titre,
            "description": description,
            "file": os.path.basename(dest),
            "norm": _norm(f"{titre}|{description}"),
        }
        fait += 1

    if manifest:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    return fait


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", help="id d'un scénario (ex. divers_dues_for_the_dead)")
    ap.add_argument("--all", action="store_true", help="pré-générer tous les scénarios du catalogue")
    ap.add_argument("--scenes", nargs="*", default=None,
                    help="scènes explicites « titre|description » (plusieurs)")
    ap.add_argument("--pdf", action="store_true",
                    help="dériver aussi des scènes depuis le texte PDF")
    ap.add_argument("--max-pdf", type=int, default=4, help="nb max de scènes dérivées du PDF")
    ap.add_argument("--catalogue", action="store_true",
                    help="ajouter les scènes curées du catalogue (salles/événements/PNJ/trésor) "
                         "pour les scénarios dispos (data/scenes_catalogue_*.py)")
    args = ap.parse_args()

    ctx = ToolContext(partie_id="pregen", joueur="admin", data_dir=DATA_DIR)
    catalogue = charger_catalogue(ctx)
    flat = []
    for u in catalogue.get("universes", []):
        for s in u.get("scenarios", []):
            s = dict(s); s["_univers"] = u.get("id", "")
            flat.append(s)

    cibles = []
    if args.scenario:
        sel = [s for s in flat if s.get("id") == args.scenario]
        if not sel:
            print(f"❌ Scénario '{args.scenario}' introuvable.")
            return 2
        cibles = sel
    elif args.all:
        cibles = flat
    else:
        print("Indique --scenario <id> ou --all.")
        return 2

    backend = get_backend()
    if backend is None:
        print("❌ Backend ComfyUI indisponible.")
        return 2
    print(f"Pré-génération — {len(cibles)} scénario(s), comfyui: {backend.base_url}")

    total = 0
    for s in cibles:
        sid = s.get("id", "?")
        scenes: list[tuple[str, str]] = []
        if args.scenes:
            for item in args.scenes:
                if "|" in item:
                    titre, desc = item.split("|", 1)
                else:
                    titre, desc = "", item
                scenes.append((titre.strip(), desc.strip()))
        if args.pdf:
            pdf = s.get("pdf")
            if pdf:
                try:
                    texte = extraire_pdf(ctx, pdf)
                except Exception as e:                     # noqa: BLE001
                    print(f"  ⚠️ pdf {sid} : {e}")
                    texte = ""
                bruts = _candidats_du_pdf(texte, limite=args.max_pdf)
                for b in bruts:
                    if all(b not in d for _, d in scenes):
                        scenes.append(("", b))
                if bruts:
                    print(f"  {sid} — {len(bruts)} scène(s) dérivées du PDF déduites")
        if args.catalogue:
            cat = _scenes_catalogue(sid)
            _titles = { _norm(t) for t, _ in scenes }
            for titre, desc in cat:
                if _norm(titre) not in _titles:
                    scenes.append((titre, desc))
            if cat:
                print(f"  {sid} — {len(cat)} scène(s) curées du catalogue ajoutées")
        if not scenes:
            print(f"  {sid} — aucune scène (fournis --scenes et/ou --pdf)")
        print(f"[{sid}] {len(scenes)} scène(s)")
        total += await _pregen(backend, sid, scenes)

    print(f"\nTerminé : {total} image(s) générée(s). Manifest(s) dans {PREGEN_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))