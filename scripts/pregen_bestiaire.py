# -*- coding: utf-8 -*-
"""Pré-génération des portraits de monstres du bestiaire (cache ComfyUI).

Pour chaque entrée RÉELLE du bestiaire (`data/bestiaire.json`, hors gabarits
génériques « * de taille * ») sans PNG en cache, appelle EXACTEMENT le
chemin runtime `server.tools.monstres.image_pour` :
  description (`prompt_image` local, sinon KB RAG Manuel des Monstres)
  → `monstre_prompt` → génération ComfyUI → cache + meta desc_hash
— mêmes prompts et mêmes clés de cache que le jeu, pour des cache hits
instantanés pendant les parties.

Usage :
    py scripts/pregen_bestiaire.py --lister
    py scripts/pregen_bestiaire.py --all
    py scripts/pregen_bestiaire.py --all --force     # régénère tout

ComfyUI (`image.base_url`) et le RAG (`rag.base_url`) doivent être
joignables ; toggle `image.monstres_enabled` actif.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

_RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RACINE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from server.tools.base import ToolContext                      # noqa: E402
from server.tools.monstres import (                            # noqa: E402
    _est_monstre_generique,
    _find_image,
    _load_bestiaire,
    image_pour,
)

# Entrées parasites d'import (pas des créatures) : jamais illustrées.
# « Vision aveugle (Ext) » est un SENSE, pas un monstre.
_JUNK_NOMS = {
    "monstre", "monstres", "attaque de base/lutte",
    "vision aveugle (ext)", "vision aveugle",
}


def _entrees_reelles(mons: Any) -> list[dict[str, Any]]:
    """Liste des entrées exploitables du bestiaire chargé.

    `_load_bestiaire` renvoie le JSON brut enrichi d'un index `monstres`
    (dict de dict) : on l'exclut, ainsi que les entrées d'import sans `nom`
    (« monstre », nom vide, type « inconnu ») et la blocklist de déchets.
    """
    out: list[dict[str, Any]] = []
    if isinstance(mons, dict):
        paires = [(k, v) for k, v in mons.items()
                  if k not in ("_meta", "monstres")]
    else:
        paires = [(None, m) for m in (mons or [])]
    for k, v in paires:
        if not isinstance(v, dict):
            continue
        nom = str(v.get("nom") or "").strip()
        if not nom or nom.lower() in _JUNK_NOMS:
            continue
        out.append(v)
    return out


def _ctx() -> ToolContext:
    from server.config import get_config
    cfg = get_config()
    return ToolContext(
        partie_id="pregen",
        joueur="pregen",
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
    )


def _lister(ctx: ToolContext) -> list[dict[str, Any]]:
    """Entrées réelles du bestiaire sans PNG en cache."""
    out: list[dict[str, Any]] = []
    for m in _entrees_reelles(_load_bestiaire(ctx)):
        nom = str(m.get("nom") or "").strip()
        if _est_monstre_generique(m):
            continue
        if _find_image(ctx, nom) is None:
            out.append(m)
    return out


async def run(force: bool, etat_path: Path) -> int:
    from server.config import get_config
    cfg = get_config()
    if not cfg.image.enabled:
        print("❌ image.enabled: false dans config.yaml — génération coupée.")
        return 1
    if not cfg.image.effective("monstres"):
        print("❌ image.monstres_enabled coupé (config × maître GUI).")
        return 1

    ctx = _ctx()
    cibles = _lister(ctx)
    if force:
        # --force : on régénère TOUT le bestiaire réel (image_pour supprime
        # le PNG périmé avant l'appel, sinon le cache court-circuite).
        cibles = _entrees_reelles(_load_bestiaire(ctx))
        cibles = [m for m in cibles if not _est_monstre_generique(m)]
    total = len(cibles)
    print(f"🎬 Pré-génération de {total} portrait(s) de monstre…")
    debut = time.time()
    stats = {"ok": 0, "err": 0}
    erreurs: list[str] = []
    for i, m in enumerate(cibles, 1):
        nom = str(m.get("nom")).strip()
        try:
            url = await image_pour(ctx, nom)
            ok = url and url.endswith((".png", ".jpg", ".jpeg", ".webp")) \
                and "bestiaire_cache" in url
            statut = "ok" if ok else "err"
            msg = f"{nom} → {url}"
        except Exception as e:                               # noqa: BLE001
            statut, msg = "err", f"{nom}: {type(e).__name__}: {e}"
        stats[statut] += 1
        if statut == "err":
            erreurs.append(msg)
        dt = time.time() - debut
        print(f"[{i:3}/{total}] {statut:3} {msg[:90]} "
              f"({dt:.0f}s — reste est. {dt / i * (total - i):.0f}s)")
        etat_path.write_text(json.dumps({
            "total": total, "fait": i, "stats": stats,
            "en_cours": nom, "termine": i == total,
            "debut": debut, "now": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
    print(f"\n🏁 Terminé : {stats['ok']} générées, {stats['err']} erreurs "
          f"({time.time() - debut:.0f}s)")
    if erreurs:
        print("Erreurs :\n" + "\n".join(erreurs[:30]))
    return 0 if stats["err"] == 0 else 2


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Pré-génère les portraits des monstres du bestiaire.")
    parseur.add_argument("--all", action="store_true",
                         help="tous les monstres sans image")
    parseur.add_argument("--force", action="store_true",
                         help="régénère TOUT le bestiaire réel")
    parseur.add_argument("--lister", action="store_true")
    parseur.add_argument("--etat", default="data/pregen_bestiaire_etat.json",
                         help="fichier d'état (progression)")
    args = parseur.parse_args()
    etat_path = Path(args.etat)
    if not etat_path.is_absolute():
        etat_path = _RACINE / etat_path
    if args.lister:
        for m in _lister(_ctx()):
            print(f"{m.get('nom','?'):40} | FP {m.get('fp','?'):3} | "
                  f"{m.get('type','?')}")
        return 0
    if not (args.all or args.force):
        parseur.error("--all ou --force requis")
    return asyncio.run(run(args.force, etat_path))


if __name__ == "__main__":
    raise SystemExit(main())
