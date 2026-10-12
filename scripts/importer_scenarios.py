"""Import des scénarios et cartes fournis par l'hébergeur (non distribués).

Le dépôt GitHub n'embarque ni cartes officielles, ni scénarios tiers (© de
leurs auteurs — voir THIRD_PARTY_NOTICES.md) : chaque hébergeur dépose les
siens puis régénère le catalogue avec cet outil.

Arborescence attendue sous `server/data/scenarios/` :

    scenarios/
      <Univers>/                     ← univers (Laelith, Divers, …)
        Cartes/                      ← cartes de l'univers (optionnel)
        <Scénario>/                  ← un dossier par campagne
          *.pdf                      ← le(s) PDF jouable(s)
          <Nom>.donjon.json          ← manifeste de donjon (optionnel)
          Cartes/ | Maps/            ← cartes du scénario
          Artwork/{Locations,Monsters,NPCs}/  ← artwork (lieux/monstres/PNJ)
          Magic Items/               ← objets magiques
          Music Puzzle/              ← énigmes
      <Univers>/<Campagne>.pdf       ← PDF original complet → annexe

Découpage en chapitres reconnu dans les noms de PDF :
  « campagne-01-introduction.pdf » (tiret) ou « Campagne 1 - Sarsolles.pdf »
  (espaces). Un PDF original à côté du dossier devient une annexe du
  chapitre 1.

Le catalogue `server/data/scenarios_catalogue.json` est RECONSTRUIT : les
entrées existantes dont le PDF existe toujours sont conservées telles quelles
(pitch, niveau, joueurs, descriptions… intacts) ; les nouvelles entrées sont
ajoutées ; celles dont le PDF a disparu sont retirées (listées dans le
rapport). Après import, redémarrer le serveur (cache mémoire du catalogue).

Usage :
    python scripts/importer_scenarios.py               # analyse (rapport seul)
    python scripts/importer_scenarios.py --apply       # écrit le catalogue
    python scripts/importer_scenarios.py --cartes <dossier> [--force]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import unicodedata

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001
        pass

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_DIR = os.path.join(RACINE, "server", "data", "scenarios")
CATALOGUE = os.path.join(RACINE, "server", "data", "scenarios_catalogue.json")
CARTES_DIR = os.path.join(RACINE, "cartes")

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

# <base>-<NN>-<suite>.pdf  (army of the damned-01-introduction.pdf)
_CHAP_TIRET = re.compile(r"^(?P<base>.+?)-(?P<num>\d{1,3})-(?P<suite>.+)$")
# <base> <NN> - <suite>.pdf (Royaumes Oubliés 1 - Sarsolles.pdf)
_CHAP_ESPACE = re.compile(r"^(?P<base>.+?)\s+(?P<num>\d{1,3})\s+-\s+(?P<suite>.+)$")

# Sous-dossiers d'assets reconnus (nom normalisé → champ du catalogue)
_ASSETS = {
    "cartes": "cartes",
    "maps": "cartes",
    "artwork/locations": ("artwork", "lieux"),
    "artwork/lieux": ("artwork", "lieux"),
    "artwork/monsters": ("artwork", "monstres"),
    "artwork/monstres": ("artwork", "monstres"),
    "artwork/npcs": ("artwork", "pnjs"),
    "artwork/pnj": ("artwork", "pnjs"),
    "magic items": "objets",
    "objets": "objets",
    "music puzzle": "enigmes",
    "enigmes": "enigmes",
}


# --------------------------------------------------------------------------- #
#  Normalisation
# --------------------------------------------------------------------------- #
def slug(texte: str) -> str:
    """Slug stable : minuscules, sans accents, apostrophes supprimées,
    tout autre non-alphanumérique → underscore (cf. ids du catalogue :
    `laelith_loeil_de_gruumsh`, `terres_dragon_hurlemont`)."""
    t = unicodedata.normalize("NFKD", (texte or "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c)).replace("'", "")
    t = re.sub(r"[^a-z0-9]+", "_", t)
    return t.strip("_")


def alnum(texte: str) -> str:
    """Empreinte très lâche (rapprocher « Army of the Damned » de
    « armyofthedamned ») : minuscules sans accents/espaces/ponctuation."""
    t = unicodedata.normalize("NFKD", (texte or "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", t)


def humanize(texte: str) -> str:
    """« crypts-kelemvor_intro » → « Crypts kelemvor intro » (première
    lettre en majuscule, séparateurs en espaces)."""
    t = re.sub(r"[-_]+", " ", (texte or "").strip()).strip()
    return (t[:1].upper() + t[1:]) if t else t


def _nom_asset(stem: str) -> str:
    """Nom d'affichage d'un asset : préfixe `loot_` retiré, séparateurs
    humanisés (« loot_Moonsilver Spear » → « Moonsilver Spear »)."""
    s = re.sub(r"^loot[_ -]+", "", stem, flags=re.IGNORECASE)
    return re.sub(r"[_]+", " ", s).strip() or stem


# --------------------------------------------------------------------------- #
#  Scan de l'arborescence
# --------------------------------------------------------------------------- #
def scan_scenarios(base: str = SCENARIOS_DIR) -> dict:
    """Parcourt l'arbre et renvoie une structure intermédiaire :

    {"univers": [{id, nom, rel, cartes}],
     "candidats": [{univers, dir_rel, dir_name, pdfs, manifestes,
                    assets, non_classes}]}
    """
    univers: dict[str, dict] = {}
    candidats: list[dict] = []

    if not os.path.isdir(base):
        return {"univers": [], "candidats": []}

    for racine, dirs, fichiers in os.walk(base):
        rel = os.path.relpath(racine, base)
        parts = [] if rel == "." else rel.split(os.sep)
        if not parts:
            continue  # racine scenarios/ — rien d'exploitable directement
        uni_id, uni_nom = slug(parts[0]), parts[0]
        u = univers.setdefault(
            uni_id, {"id": uni_id, "nom": uni_nom, "rel": parts[0],
                     "cartes": [], "pdfs": []}
        )

        # PDF libres à la racine de l'univers (« Crypts Kelemvor.pdf » :
        # original complet — annexe, ou nouveau scénario mono-fichier) —
        # collectés UNIQUEMENT au niveau racine de l'univers.
        if len(parts) == 1:
            for f in sorted(fichiers):
                if f.lower().endswith(".pdf"):
                    u["pdfs"].append(os.path.join(rel, f))
            for f in sorted(fichiers):
                if os.path.splitext(f)[1].lower() in IMG_EXT:
                    u["cartes"].append({
                        "nom": _nom_asset(os.path.splitext(f)[0]),
                        "fichier": _url(os.path.join(rel, f)),
                    })
            for sd in sorted(dirs):
                if slug(sd) != "cartes":
                    continue
                for f in _fichiers_images(os.path.join(racine, sd)):
                    u["cartes"].append({
                        "nom": _nom_asset(os.path.splitext(f)[0]),
                        "fichier": _url(os.path.join(rel, sd, f)),
                    })
            continue

        # Dossier « Cartes/ » au 2e niveau = déjà compté ci-dessus ; ce
        # n'est pas un scénario.
        if len(parts) == 2 and slug(parts[1]) == "cartes":
            continue

        pdfs = sorted(f for f in fichiers if f.lower().endswith(".pdf"))

        # ── Assets : sous-dossiers reconnus (à 1 ou 2 niveaux) ───────────
        assets: dict = {}
        non_classes: list[str] = []
        parent_norm = slug(parts[-1])
        for sd in sorted(dirs):
            sd_slug = slug(sd)
            champ = _ASSETS.get(f"{parent_norm}/{sd_slug}") \
                or _ASSETS.get(sd_slug)
            files = _fichiers_images(os.path.join(racine, sd))
            if not files:
                continue
            if champ is None:
                non_classes.append(sd)
                continue
            items = [{"nom": _nom_asset(os.path.splitext(f)[0]),
                      "fichier": _url(os.path.join(rel, sd, f))}
                     for f in files]
            if isinstance(champ, tuple):
                assets.setdefault(champ[0], {})[champ[1]] = items
            else:
                assets[champ] = items

        candidats.append({
            "univers": parts[0],
            "dir_rel": rel,
            "dir_name": parts[-1],
            "pdfs": pdfs,
            "manifestes": sorted(
                f for f in fichiers if f.lower().endswith(".donjon.json")),
            "assets": assets,
            "non_classes": non_classes,
        })
    return {"univers": list(univers.values()), "candidats": candidats}


def _fichiers_images(dossier: str) -> list[str]:
    if not os.path.isdir(dossier):
        return []
    return sorted(
        f for f in os.listdir(dossier)
        if os.path.splitext(f)[1].lower() in IMG_EXT
        and not f.lower().endswith(".donjon.json")
    )


def _url(rel_path: str) -> str:
    return "/data/scenarios/" + rel_path.replace(os.sep, "/")


# --------------------------------------------------------------------------- #
#  Construction des entrées de catalogue
# --------------------------------------------------------------------------- #
def _construire_entrees(candidat: dict, anciens_par_pdf: dict,
                        ids_pris: set[str], rapport: list[str],
                        herite: bool = False,
                        base: str = SCENARIOS_DIR) -> list[dict]:
    """Construit les entrées de catalogue pour un dossier scénario.

    Les PDF déjà référencés par l'ancien catalogue sont conservés TELS QUELS
    (pitch/niveau/chaînage artisanaux intacts). Les nouveaux reçoivent un id
    généré et un chaînage automatique si un numéro de chapitre est détecté —
    SAUF si le dossier est déjà couvert par le catalogue (`herite`) : les
    PDF non catalogués y sont des annexes volontaires du découpage, on les
    signale sans les ajouter.
    """
    entrees: list[dict] = []
    nouvelles_simples: list[dict] = []
    chapitres: list[dict] = []
    exclus = 0

    for pdf in candidat["pdfs"]:
        url = _url(os.path.join(candidat["dir_rel"], pdf))
        ancien = anciens_par_pdf.get(url)
        if ancien is not None:
            entrees.append(dict(ancien))
            continue
        if herite:
            exclus += 1
            if exclus <= 5:
                rapport.append(
                    f"➖ non catalogué (dossier déjà géré) : {url}")
            continue

        stem = os.path.splitext(pdf)[0]
        m = _CHAP_TIRET.match(stem) or _CHAP_ESPACE.match(stem)
        if m:
            chapitres.append({
                "base": m.group("base"), "num": int(m.group("num")),
                "suite": m.group("suite"), "pdf": url,
            })
        else:
            e = _entree_simple(candidat, stem, url, ids_pris)
            nouvelles_simples.append(e)
            entrees.append(e)

    if exclus > 5:
        rapport.append(
            f"… (+{exclus - 5} autres non catalogués dans "
            f"{candidat['dir_rel']})")

    # Chaînage automatique des NOUVEAUX chapitres détectés.
    premier_chapitre: dict | None = None
    if chapitres:
        campagne = candidat["dir_name"]
        chapitres.sort(key=lambda c: c["num"])
        total = len(chapitres)
        uni = slug(candidat["univers"])
        for i, ch in enumerate(chapitres, start=1):
            base_id = f"{uni}_{slug(ch['base'])}"
            sid = f"{base_id}_ch{i:02d}"
            n = 2
            while sid in ids_pris:
                sid = f"{base_id}_ch{i:02d}_{n}"
                n += 1
            ids_pris.add(sid)
            e = {
                "id": sid,
                "titre": f"{humanize(ch['base'])} {ch['num']}/{total} - "
                         f"{humanize(ch['suite'])}",
                "pdf": ch["pdf"],
                "campagne": campagne,
                "chapitre": ch["num"],
                "chapitre_total": total,
            }
            if i < total:
                e["chapitre_suivant"] = f"{base_id}_ch{i + 1:02d}"
            e.update(candidat["assets"])
            if premier_chapitre is None:
                premier_chapitre = e
            entrees.append(e)

    # Assets détectés sur les entrées simples générées uniquement (les
    # entrées conservées de l'ancien catalogue ne sont PAS modifiées).
    for e in nouvelles_simples:
        e.update(candidat["assets"])

    # Annexe : PDF original complet posé à côté du dossier scénario (sans
    # doublonner une annexe déjà présente dans l'ancien catalogue). Cible :
    # le premier chapitre de la campagne, sinon la première entrée.
    annexes_connues = {
        a.get("fichier") for e in entrees for a in e.get("annexes", [])
    }
    cible_annexe = premier_chapitre or (entrees[0] if entrees else None)
    if cible_annexe is not None:
        for pdf_libre in _pdfs_libres_parent(candidat, base):
            if pdf_libre not in annexes_connues:
                cible_annexe.setdefault("annexes", []).append({
                    "nom": "Aventure complète (PDF original, référence)",
                    "fichier": pdf_libre,
                })

    # Manifestes .donjon.json (auto-découverts par l'app — simple validation)
    for man in candidat["manifestes"]:
        chemin = os.path.join(base, candidat["dir_rel"], man)
        try:
            with open(chemin, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data.get("etages"):
                raise ValueError("champ `etages` absent")
        except Exception as exc:                              # noqa: BLE001
            rapport.append(
                f"⚠️  manifeste invalide {candidat['dir_rel']}/{man} : {exc}")
    return entrees


def _entree_simple(candidat: dict, stem: str, url: str,
                   ids_pris: set[str]) -> dict:
    uni = slug(candidat["univers"])
    sid = f"{uni}_{slug(stem)}" or f"{uni}_scenario"
    n = 2
    while sid in ids_pris:
        sid = f"{uni}_{slug(stem)}_{n}"
        n += 1
    ids_pris.add(sid)
    return {"id": sid, "titre": humanize(stem), "pdf": url}


def _pdfs_libres_parent(candidat: dict, base: str = SCENARIOS_DIR) -> list[str]:
    """PDF(s) original(aux) posé(s) au-dessus du dossier scénario dont le nom
    (empreinte alnum) correspond au dossier (« Army of the Damned.pdf » à
    côté de « Army of the Damned/ »)."""
    parent = os.path.dirname(candidat["dir_rel"])
    if parent in ("", "."):
        return []
    dossier_parent = os.path.join(base, parent)
    empreinte = alnum(candidat["dir_name"])
    trouves = []
    for f in sorted(os.listdir(dossier_parent)) if os.path.isdir(dossier_parent) else []:
        racine, ext = os.path.splitext(f)
        if ext.lower() == ".pdf" and alnum(racine) == empreinte:
            trouves.append(_url(os.path.join(parent, f)))
    return trouves


# --------------------------------------------------------------------------- #
#  Assemblage du catalogue
# --------------------------------------------------------------------------- #
def lire_catalogue(chemin: str = CATALOGUE) -> dict:
    try:
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("universes"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"universes": []}


def construire_catalogue(arbre: dict, ancien: dict,
                         rapport: list[str],
                         base: str = SCENARIOS_DIR) -> dict:
    """Fusionne l'arbre scanné avec l'ancien catalogue :

    - entrées conservées telles quelles si leur PDF existe toujours ;
    - nouveaux scénarios ajoutés UNIQUEMENT s'ils vivent dans un dossier non
      référencé par le catalogue (les PDF non catalogués d'un dossier déjà
      géré sont des annexes volontaires du découpage — signalés, pas ajoutés) ;
    - entrées dont le PDF a disparu → retirées (listées).
    """
    anciens_par_pdf: dict[str, dict] = {}
    for u in ancien.get("universes", []):
        for s in u.get("scenarios", []):
            pdf = s.get("pdf")
            if pdf:
                anciens_par_pdf[str(pdf)] = s

    # Rapport : entrées de l'ancien catalogue dont le PDF a disparu.
    for url, s in anciens_par_pdf.items():
        rel = url.removeprefix("/data/scenarios/")
        if not os.path.isfile(os.path.join(SCENARIOS_DIR, rel)):
            rapport.append(
                f"➖ retiré (PDF absent) : {s.get('id')} → {url}")

    # Dossiers référencés par le catalogue (eux + tous leurs ancêtres) :
    # un PDF non catalogué qui vit sous l'un d'eux est présumé volontaire.
    dossiers_references: set[str] = set()
    dossiers_catalogues: set[str] = set()   # basename alnum des dossiers
    for url in anciens_par_pdf:
        rel = url.removeprefix("/data/scenarios/")
        dossier = os.path.dirname(rel)
        if dossier:
            dossiers_catalogues.add(alnum(os.path.basename(dossier)))
        while dossier and dossier not in (".", "/"):
            dossiers_references.add(
                dossier.replace(os.sep, "/").lower())
            dossier = os.path.dirname(dossier)

    ids_pris = {str(s.get("id")) for s in anciens_par_pdf.values()}
    # Copie profonde : le catalogue reconstruit ne doit JAMAIS partager de
    # listes avec `ancien` (les append ci-dessous le muteraient sinon).
    ancien = copy.deepcopy(ancien)
    construits: dict[str, dict] = {
        slug(u.get("id") or u.get("nom") or ""): dict(u)
        for u in ancien.get("universes", [])
    }
    univers_order = list(construits.keys())

    # ── Appariement des univers de l'arbre → univers du catalogue ────────
    # slug exact sinon empreinte lâche (« Les Royaumes Oubliés » →
    # `royaumes_oublies`, « Les Terres de l'Éternel » → `terres_eternel`).
    uni_match: dict[str, str] = {}
    for a in arbre["univers"]:
        a_id = slug(a["id"])
        if a_id in construits:
            uni_match[a_id] = a_id
            continue
        a_alnum = alnum(a["nom"])
        cible = None
        for key, u in construits.items():
            k_alnum = alnum(u.get("nom") or key)
            if k_alnum and (k_alnum in a_alnum or a_alnum in k_alnum):
                cible = key
                break
        uni_match[a_id] = cible or a_id

    # ── Fusion des cartes d'univers + création des univers inconnus ──────
    for a in arbre["univers"]:
        a_id = slug(a["id"])
        cible = uni_match[a_id]
        if cible not in construits:
            construits[cible] = {
                "id": cible, "nom": a["nom"], "description": "",
                "cartes": [], "scenarios": [],
            }
            univers_order.append(cible)
        # PDF libres à la racine de l'univers, toutes univers confondues
        # (les chemins relatifs contiennent déjà le dossier univers).
        a["pdfs_libres"] = [_url(pdf) for pdf in a.get("pdfs", [])]
        existantes = {
            c.get("fichier") for c in construits[cible].get("cartes", [])
        }
        for c in a.get("cartes", []):
            if c["fichier"] not in existantes:
                construits[cible].setdefault("cartes", []).append(c)

    # ── Scénarios ─────────────────────────────────────────────────────────
    exclus_affiches = 0
    for cand in arbre["candidats"]:
        uni_id = uni_match.get(slug(cand["univers"]))
        if uni_id is None or uni_id not in construits:
            continue
        rel_norm = cand["dir_rel"].replace(os.sep, "/").lower()
        herite = any(
            rel_norm == d or rel_norm.startswith(d + "/")
            for d in dossiers_references
        )
        entrees = _construire_entrees(cand, anciens_par_pdf, ids_pris,
                                      rapport, herite, base)
        deja = {
            s.get("pdf") for s in construits[uni_id].get("scenarios", [])
        }
        for e in entrees:
            if e.get("pdf") in deja:
                continue
            construits[uni_id].setdefault("scenarios", []).append(e)
            rapport.append(f"➕ ajouté : {e.get('id')} → {e.get('pdf')}")

    # ── PDF libres à la racine d'un univers ───────────────────────────────
    # Ignorés si c'est l'original d'un dossier scénario (catalogué → déjà
    # annexé ; nouveau → annexé par _construire_entrees).
    noms_dossiers = dossiers_catalogues | {
        alnum(c["dir_name"]) for c in arbre["candidats"]
    }
    for a in arbre["univers"]:
        a_id = slug(a["id"])
        cible = uni_match.get(a_id)
        if cible is None or cible not in construits:
            continue
        for pdf in a.get("pdfs_libres", []):
            url = pdf  # déjà une URL /data/scenarios/… (construite au scan)
            if url in anciens_par_pdf:
                continue
            stem = os.path.splitext(os.path.basename(pdf))[0]
            if alnum(stem) in noms_dossiers:
                exclus_affiches += 1
                if exclus_affiches <= 15:
                    rapport.append(
                        f"➖ ignoré : {url} (original du dossier du même nom)")
                continue
            e = _entree_simple({"univers": a["nom"]}, stem, url, ids_pris)
            deja = {
                s.get("pdf") for s in construits[cible].get("scenarios", [])
            }
            if url not in deja:
                construits[cible].setdefault("scenarios", []).append(e)
                rapport.append(f"➕ ajouté : {e.get('id')} → {url}")

    if exclus_affiches:
        rapport.append(f"… {exclus_affiches} original(aux) déjà annexé(s), "
                       f"ignoré(s)")

    universes = []
    for uni_id in univers_order:
        u = construits.get(uni_id)
        if u is None:
            continue
        u["scenarios"] = u.get("scenarios") or []
        universes.append(u)
    return {"universes": universes}


# --------------------------------------------------------------------------- #
#  Import de cartes
# --------------------------------------------------------------------------- #
def importer_cartes(src: str, force: bool = False) -> list[str]:
    """Copie les images/PDF de `src` vers `cartes/` (non-écrasant sauf
    --force). Renvoie les lignes du rapport."""
    lignes = []
    os.makedirs(CARTES_DIR, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(src)):
        p = os.path.join(src, f)
        if not os.path.isfile(p):
            continue
        if os.path.splitext(f)[1].lower() not in (IMG_EXT | {".pdf"}):
            continue
        dest = os.path.join(CARTES_DIR, f)
        if os.path.isfile(dest) and not force:
            lignes.append(f"⏭️  {f} (existe déjà — --force pour écraser)")
            continue
        with open(p, "rb") as fi, open(dest, "wb") as fo:
            fo.write(fi.read())
        lignes.append(f"✅ {f}")
        n += 1
    lignes.append(f"— {n} carte(s) copiée(s) vers {CARTES_DIR}")
    return lignes


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Import des scénarios/cartes de l'hébergeur + "
                    "régénération du catalogue.")
    ap.add_argument("--apply", action="store_true",
                    help="écrit le catalogue (sinon simple analyse)")
    ap.add_argument("--cartes", metavar="DOSSIER",
                    help="copie les images de DOSSIER vers cartes/")
    ap.add_argument("--force", action="store_true",
                    help="--cartes : écrase les fichiers existants")
    args = ap.parse_args()

    if args.cartes:
        if not os.path.isdir(args.cartes):
            print(f"❌ dossier introuvable : {args.cartes}")
            return 1
        for ligne in importer_cartes(args.cartes, force=args.force):
            print(ligne)

    rapport: list[str] = []
    arbre = scan_scenarios(SCENARIOS_DIR)
    if not arbre["univers"]:
        print(f"ℹ️  Aucun scénario sous {SCENARIOS_DIR}.")
        print("   Déposez vos PDF dans server/data/scenarios/<Univers>/<Scénario>/"
              " puis relancez.")
        return 0

    ancien = lire_catalogue(CATALOGUE)
    nouveau = construire_catalogue(arbre, ancien, rapport)

    total = sum(len(u.get("scenarios", [])) for u in nouveau["universes"])
    print(f"📚 {len(nouveau['universes'])} univers, {total} scénarios "
          f"({len(ancien.get('universes', []))} univers, "
          f"{sum(len(u.get('scenarios', [])) for u in ancien['universes'])} "
          f"avant).")
    for ligne in rapport:
        print(ligne)

    if args.apply:
        with open(CATALOGUE, "w", encoding="utf-8") as f:
            json.dump(nouveau, f, ensure_ascii=False, indent=2)
        print(f"💾 catalogue écrit : {CATALOGUE}")
        print("   ↻ redémarrez le serveur (docker compose restart dnd35) — "
              "le catalogue est mis en cache mémoire.")
    else:
        print("🔎 analyse seule — relancez avec --apply pour écrire.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
