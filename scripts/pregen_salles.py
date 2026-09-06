"""Pré-génération des illustrations de salles pour les manifestes de donjons.

Chaque scénario avec un fichier `*.donjon.json` (plan canonique) voit toutes
ses salles illustrées dans le cache `data/images_salles/<slug>_<x>_<y>.png`
— MÊME convention de nommage et MÊME prompt que le runtime
(`server/tools/cartes.py::_illustrer_salle`), pour des cache hits
instantanés pendant le jeu.

Usage :
    py scripts/pregen_salles.py --lister
    py scripts/pregen_salles.py --scenario divers_dues_for_the_dead
    py scripts/generer_salles.py --all [--force] [--etat data/pregen_salles.json]

ComfyUI doit être joignable (`image.enabled: true` + base_url / env).
Le script respecte le toggle `image.salles_enabled` (config × maître GUI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RACINE))

from server.image.helpers import lieu_prompt
from server.tools.cartes import chemin_image_salle as cartes_chemin_image_salle


def _data_dir() -> Path:
    from server.config import get_config
    cfg = get_config()
    return cfg.abs(cfg.paths.data_dir)


def _slug_donjon(donjon_id: str) -> str:
    """MÊME logique que cartes._illustrer_salle (slug de cache)."""
    return (str(donjon_id or "salle") or "salle").lower().replace(" ", "_")


def lister_manifestes() -> list[dict[str, Any]]:
    """[{chemin, scenario, donjon_id, salles: [{x, y, type, etage}]}]."""
    base = _data_dir() / "scenarios"
    out: list[dict[str, Any]] = []
    for chemin in sorted(base.rglob("*.donjon.json")):
        try:
            man = json.loads(chemin.read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            continue
        if not isinstance(man, dict):
            continue
        salles: list[dict[str, Any]] = []
        for idx, fl in enumerate(man.get("etages") or []):
            for s in (fl.get("salles") or []):
                if not isinstance(s, dict):
                    continue
                try:
                    salles.append({
                        "x": int(s["x"]), "y": int(s["y"]),
                        "type": str(s.get("type") or "salle"),
                        "etage": idx,
                        "description": str(s.get("description") or ""),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
        out.append({
            "chemin": chemin,
            "scenario": str(man.get("scenario") or man.get("id") or ""),
            "donjon_id": str(man.get("donjon_id") or man.get("id") or "Donjon"),
            "salles": salles,
        })
    return out


async def _generer_une(tache: dict[str, Any], force: bool) -> tuple[str, str]:
    """Génère une salle. Renvoie (statut, message). statut ∈ cache|ok|off|err."""
    from server.image.helpers import generer_si_dispo
    dest: Path = Path(tache["dest"])
    existant: Path = Path(tache.get("existant") or dest)
    if existant.is_file() and not force:
        return "cache", str(existant)
    prompt = lieu_prompt(tache["type"], tache["donjon_id"])
    try:
        r = await generer_si_dispo("lieu", prompt, str(dest))
    except Exception as e:                                       # noqa: BLE001
        return "err", f"{tache['cle']}: {type(e).__name__}: {e}"
    if r and os.path.isfile(str(r)):
        return "ok", str(r)
    return "off", f"{tache['cle']}: génération indisponible (ComfyUI ?)"


async def run(scenario_filtre: str | None, force: bool, etat_path: Path) -> int:
    from server.config import get_config
    cfg = get_config()
    if not cfg.image.enabled:
        print("❌ image.enabled: false dans config.yaml — génération coupée.")
        return 1
    if not cfg.image.effective("salles"):
        print("❌ image.salles_enabled coupé (config × maître GUI).")
        return 1

    manifestes = lister_manifestes()
    if scenario_filtre:
        manifestes = [m for m in manifestes if m["scenario"] == scenario_filtre]
        if not manifestes:
            print(f"❌ Aucun manifeste pour « {scenario_filtre} ».")
            return 1
    taches: list[dict[str, Any]] = []
    for m in manifestes:
        cache_dir = _data_dir() / "images_salles"
        cache_dir.mkdir(parents=True, exist_ok=True)
        for s in m["salles"]:
            # MÊME convention que le runtime : chemin canonique AVEC étage ;
            # pour l'étage 0, repli sur l'ancien nom (caches d'avant
            # correction) quand le fichier canonique n'existe pas encore.
            dest = Path(cartes_chemin_image_salle(
                str(_data_dir()), m["donjon_id"], s["etage"], s["x"], s["y"]))
            existant = Path(cartes_chemin_image_salle(
                str(_data_dir()), m["donjon_id"], s["etage"], s["x"], s["y"],
                existant=True))
            taches.append({
                "cle": f"{m['scenario']}|{s['etage']}|({s['x']},{s['y']})",
                "dest": dest,
                "existant": existant,
                "type": s["type"],
                "donjon_id": m["donjon_id"],
                "scenario": m["scenario"],
                "salle": s,
            })
    total = len(taches)
    print(f"🎬 Pré-génération de {total} salle(s) pour {len(manifestes)} manifeste(s)…")
    debut = time.time()
    stats = {"cache": 0, "ok": 0, "off": 0, "err": 0}
    erreurs: list[str] = []
    for i, t in enumerate(taches, 1):
        statut, msg = await _generer_une(t, force)
        stats[statut] += 1
        if statut == "err":
            erreurs.append(msg)
        if statut in ("ok", "err", "off") or i % 10 == 0 or i == total:
            dt = time.time() - debut
            print(f"[{i:3}/{total}] {statut:5} {msg[:80]} "
                  f"({dt:.0f}s — reste est. {dt / i * (total - i):.0f}s)")
        # État persisté (reprise / supervision externe).
        etat_path.write_text(json.dumps({
            "total": total, "fait": i, "stats": stats,
            "en_cours": t["cle"], "termine": i == total,
            "debut": debut, "now": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
    print(f"\n🏁 Terminé : {stats['ok']} générées, {stats['cache']} en cache, "
          f"{stats['off']} indisponibles, {stats['err']} erreurs "
          f"({time.time() - debut:.0f}s)")
    if erreurs:
        print("Erreurs :\n" + "\n".join(erreurs[:20]))
    return 0 if stats["err"] == 0 and stats["off"] == 0 else 2


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Pré-génère les illustrations de salles des manifestes.")
    parseur.add_argument("--scenario", help="id de scénario précis")
    parseur.add_argument("--all", action="store_true", help="tous les manifestes")
    parseur.add_argument("--force", action="store_true",
                         help="régénère même si le PNG existe")
    parseur.add_argument("--lister", action="store_true")
    parseur.add_argument("--etat", default="data/pregen_salles_etat.json",
                         help="fichier d'état (progression)")
    args = parseur.parse_args()
    if args.lister:
        for m in lister_manifestes():
            print(f"{m['scenario']:40} | {m['donjon_id']:40} | "
                  f"{len(m['salles'])} salles")
        return 0
    if not (args.all or args.scenario):
        parseur.error("--all ou --scenario requis")
    etat_path = Path(args.etat)
    if not etat_path.is_absolute():
        etat_path = _RACINE / etat_path
    return asyncio.run(run(args.scenario, args.force, etat_path))


if __name__ == "__main__":
    raise SystemExit(main())
