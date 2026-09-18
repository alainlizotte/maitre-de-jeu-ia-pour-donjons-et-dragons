"""Découpe les scénarios PDF trop volumineux en chapitres chaînés.

Pour chaque scénario du catalogue dont le texte extrait dépasse le plafond
d'injection au MJ (24 000 caractères, cf. `server/tools/scenarios.py`), le
script :
  1. découpe le PDF en chapitres de ≤ `--cible` caractères, à des frontières
     de pages, en privilégiant les pages qui débutent une section
     (« Chapitre », « Partie », « Acte », « Background »,…) ;
  2. écrit les PDF chapitres dans un sous-dossier (ou à côté de l'original
     si le dossier porte déjà le nom du scénario) ;
  3. remplace l'entrée du catalogue par les chapitres chaînés
     (`campagne`, `chapitre`, `chapitre_total`, `chapitre_suivant`),
     en préservant les champs annexes (cartes, artwork, objets,…) et en
     référençant le PDF original dans les annexes ;
  4. met à jour les manifestes `<nom>.donjon.json` qui référencent l'ancien
     identifiant (champ `scenario` en liste).

Sections MANUELLES : pour les documents non linéaires (campagnes avec
annexes de référence), la table `_SECTIONS_MANUELLES` impose les plages de
pages jouables et exporte le reste en annexes non chaînées.

Usage :
    python scripts/decouper_scenarios.py --analyse            # aperçu
    python scripts/decouper_scenarios.py --apply              # tout traiter
    python scripts/decouper_scenarios.py --apply --ids id1 id2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

import pymupdf

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGUE = os.path.join(RACINE, "server", "data", "scenarios_catalogue.json")
ARBRE_SCENARIOS = os.path.join(RACINE, "server", "data", "scenarios")
PLAFOND_APP = 24000          # plafond d'injection du MJ (scenarios.py)
CIBLE = 22000                # taille cible d'un chapitre (marge de sécurité)
TOLERANCE = 5000             # dépassement toléré pour couper sur une section
HARD_MAX = PLAFOND_APP - 800  # limite absolue d'un chapitre

# --------------------------------------------------------------------------- #
#  Sections manuelles : plages de pages jouables + annexes exportées
#  (pages 1-based, inclusives). Chaque section jouable est ensuite
#  redécoupée automatiquement si elle dépasse la cible.
# --------------------------------------------------------------------------- #
_SECTIONS_MANUELLES = {
    # Campagne de 502 pages : jouable = Introduction + Chapitres 1-9.
    # Le reste (PNJs/épilogues, aides de jeu, lore) devient des annexes PDF.
    "ro_le_sceau_des_sept_sœurs": {
        "sous_dossier": "Le Sceau des Sept Sœurs",
        "jouables": [
            ("Introduction", 15, 30),
            ("Chapitre 1 : Dans la Gueule du Loup", 31, 40),
            ("Chapitre 2 : Du Soleil et des Larmes", 41, 66),
            ("Chapitre 3 : L'Esprit de Sacrifice", 67, 100),
            ("Chapitre 4 : Le Secret du Chartreux", 101, 138),
            ("Chapitre 5 : La Voie des Druides", 139, 166),
            ("Chapitre 6 : Une Âme de Glace", 167, 216),
            ("Chapitre 7 : De l'Ombre et des Masques", 217, 264),
            ("Chapitre 8 : Plus près des étoiles", 265, 300),
            ("Chapitre 9 : Le Sceau des Sept Sœurs", 301, 334),
        ],
        "annexes": [
            ("Annexe - PJs, PNJs et epilogues (reference)", 335, 412),
            ("Annexe - Aides de jeu (reference)", 413, 419),
            ("Annexe - Histoire de Toril et La Geste du Superbe (reference)", 420, 501),
        ],
    },
}

# Lignes qui signalent le DÉBUT d'une section (candidat à une coupure).
_RE_SECTION = re.compile(
    r"^\s*(chapitre|chapter|partie|part|acte|section|scene|scène|prologue|"
    r"epilogue|épilogue|introduction|annexe|appendice|appendix|background|"
    r"synopsis|level|etage|étage|area|zone|encounter|rencontre|adventure|"
    r"the\s+[A-Z])\b",
    re.IGNORECASE,
)
_RE_CAPS = re.compile(r"^[A-ZÀ-ÖØ-Þ0-9\s\-'’&,():.!«»\"/?]+$")


def _sans_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _slug(s: str, limite: int = 42) -> str:
    s = _sans_accents(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s[:limite].strip() or "partie"


def _propre(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _lignes_utiles(texte: str, n: int = 5) -> list[str]:
    return [l.strip() for l in texte.splitlines() if l.strip()][:n]


def _est_debut_section(texte_page: str) -> bool:
    """True si la page semble débuter une section nommée."""
    for l in _lignes_utiles(texte_page, 4):
        if _RE_SECTION.match(l) and 6 < len(l) < 80:
            return True
    return False


def _titre_section(texte_page: str) -> str:
    """Premier titre plausible trouvé dans les premières lignes de la page."""
    for l in _lignes_utiles(texte_page, 5):
        if _RE_SECTION.match(l) and 6 < len(l) < 80:
            return _propre(re.sub(r"\.{3,}.*$", "", l))
        if _RE_CAPS.match(l) and 8 <= len(l) <= 60 and not l.isdigit():
            return _propre(l)
    return ""


def _coupe_simple(idxs: list[int], pages_texte: list[str], cible: int) -> list[list[int]]:
    """Découpe mécanique (sans recherche de section) en ≤ cible."""
    morceaux: list[list[int]] = []
    courant: list[int] = []
    taille = 0
    for i in idxs:
        n = len(pages_texte[i]) + 3
        if courant and taille + n > cible:
            morceaux.append(courant)
            courant = []
            taille = 0
        courant.append(i)
        taille += n
    if courant:
        morceaux.append(courant)
    return morceaux


def _decouper_pages(pages_texte: list[str], cible: int) -> list[list[int]]:
    """Regroupe les index de pages en chapitres ≤ cible, en préférant les
    débuts de section comme frontière. Aucun morceau ne dépasse HARD_MAX."""
    morceaux: list[list[int]] = []
    courant: list[int] = []
    taille = 0
    coupe_section: int | None = None  # position DANS `courant` d'un début de section
    for i, t in enumerate(pages_texte):
        n = len(t) + 3
        if courant and taille + n > cible:
            if (
                coupe_section is not None
                and coupe_section >= 1
                and sum(len(pages_texte[j]) + 3 for j in courant[:coupe_section])
                >= 0.4 * cible
            ):
                morceaux.append(courant[:coupe_section])
                courant = courant[coupe_section:]
            else:
                morceaux.append(courant)
                courant = []
            taille = sum(len(pages_texte[j]) + 3 for j in courant)
            coupe_section = None
        courant.append(i)
        taille += n
        # Si CETTE page débute une section, elle marque une future coupure
        # possible (on refera un chapitre qui commence sur son titre).
        if len(courant) >= 2 and _est_debut_section(t):
            coupe_section = len(courant) - 1
    if courant:
        morceaux.append(courant)
    # fusionne les résidus trop petits avec le morceau précédent (sans
    # jamais dépasser la limite dure)
    borne = min(cible + TOLERANCE, HARD_MAX)
    fusion: list[list[int]] = []
    for m in morceaux:
        t_m = sum(len(pages_texte[j]) + 3 for j in m)
        if fusion:
            t_prev = sum(len(pages_texte[j]) + 3 for j in fusion[-1])
            if t_prev + t_m <= borne and (t_m < 0.15 * cible or len(fusion[-1]) < 2):
                fusion[-1].extend(m)
                continue
        fusion.append(m)
    # dernier filet : aucun morceau au-dessus de HARD_MAX
    final: list[list[int]] = []
    for m in fusion:
        if sum(len(pages_texte[j]) + 3 for j in m) > HARD_MAX:
            final.extend(_coupe_simple(m, pages_texte, cible))
        else:
            final.append(m)
    return final


def _renumeroter(blocs: list[tuple[str, list[int]]]) -> list[tuple[str, list[int]]]:
    """Renumérote les séries consécutives partageant le même titre de base."""
    def _base(t: str) -> str:
        return (
            re.sub(r"\s*\(partie \d+/\d+\)\s*$", "", t).strip() or "Partie"
        )

    final: list[tuple[str, list[int]]] = []
    i = 0
    while i < len(blocs):
        j = i
        while j + 1 < len(blocs) and _base(blocs[j + 1][0]) == _base(blocs[i][0]):
            j += 1
        n = j - i + 1
        for k in range(i, j + 1):
            titre = blocs[k][0]
            if n > 1:
                titre = f"{_base(titre)} (partie {k - i + 1}/{n})"
            final.append((titre, blocs[k][1]))
        i = j + 1
    return final


def _charger_catalogue() -> dict:
    with open(CATALOGUE, encoding="utf-8") as f:
        return json.load(f)


def _scenarios_oversizes(cata: dict) -> list[tuple[str, dict]]:
    """Scénarios (hors chapitres déjà découpés) dont le texte dépasse le plafond."""
    resultats = []
    for u in cata.get("universes", []):
        for s in u.get("scenarios", []):
            pdf = s.get("pdf")
            if not pdf or s.get("chapitre"):
                continue
            chemin = os.path.join(RACINE, "server", pdf.lstrip("/"))
            if not os.path.isfile(chemin):
                continue
            with pymupdf.open(chemin) as d:
                txt = "".join(p.get_text() + "\n\n\n" for p in d)
            if len(txt) > PLAFOND_APP:
                resultats.append((s["id"], s))
    return resultats


def _trouver_sections(s: dict) -> tuple[list[tuple[str, list[int]]], list[tuple[str, list[int]]]]:
    """Renvoie (sections jouables [(titre, [index pages])], annexes [(titre, plage)])."""
    chemin = os.path.join(RACINE, "server", s["pdf"].lstrip("/"))
    with pymupdf.open(chemin) as d:
        pages_texte = [p.get_text() for p in d]
    manuel = _SECTIONS_MANUELLES.get(s["id"])
    if manuel:
        jouables = [
            (titre, list(range(d - 1, f)))
            for titre, d, f in manuel["jouables"]
        ]
        annexes = manuel.get("annexes", [])
        return jouables, annexes
    # Auto : tout le document est jouable.
    groupes = _decouper_pages(pages_texte, CIBLE)
    return [("", idxs) for idxs in groupes], []


def _exporter_pdf(chemin_source: str, pages: list[int], dest: str) -> int:
    """Écrit un PDF extrait ; renvoie la taille texte (méthode de l'app)."""
    with pymupdf.open(chemin_source) as src:
        out = pymupdf.open()
        for i in pages:
            out.insert_pdf(src, from_page=i, to_page=i)
        out.save(dest)
        out.close()
    with pymupdf.open(dest) as v:
        return sum(len(p.get_text()) + 3 for p in v)


def _maj_manifestes(vieux_id: str, nouveaux_ids: list[str]) -> list[str]:
    """Propage les ids de chapitres dans les manifestes .donjon.json."""
    touches = []
    for racine, _d, fichiers in os.walk(ARBRE_SCENARIOS):
        for f in fichiers:
            if not f.endswith(".donjon.json"):
                continue
            chemin = os.path.join(racine, f)
            try:
                with open(chemin, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:                                    # noqa: BLE001
                continue
            scen = data.get("scenario")
            ids = set(scen) if isinstance(scen, list) else {scen}
            if vieux_id in ids and set(nouveaux_ids) - ids:
                data["scenario"] = sorted(
                    x for x in (ids | set(nouveaux_ids)) if x
                )
                with open(chemin, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                touches.append(chemin)
    return touches


def _dossier_sortie(s: dict, config_manuel: dict | None) -> str:
    parent = os.path.dirname(os.path.join(RACINE, "server", s["pdf"].lstrip("/")))
    if config_manuel and config_manuel.get("sous_dossier"):
        nom = config_manuel["sous_dossier"]
    else:
        nom = _sans_accents(s["titre"]).strip()
    if os.path.normcase(_sans_accents(os.path.basename(parent)).lower()) == (
        os.path.normcase(_sans_accents(nom).lower())
    ):
        return parent
    return os.path.join(parent, nom)


def traiter(s: dict, universe: dict, apply: bool, cible: int) -> None:
    sid = s["id"]
    config_manuel = _SECTIONS_MANUELLES.get(sid)
    chemin_source = os.path.join(RACINE, "server", s["pdf"].lstrip("/"))
    with pymupdf.open(chemin_source) as d:
        pages_texte = [p.get_text() for p in d]

    sections, annexes_pages = _trouver_sections(s)

    # Redécoupe chaque section trop grosse ; titre les morceaux.
    def _taille_pages(idxs: list[int]) -> int:
        return sum(len(pages_texte[i]) + 3 for i in idxs)

    blocs: list[tuple[str, list[int]]] = []
    for titre_sec, idxs in sections:
        taille = _taille_pages(idxs)
        if taille <= min(cible + TOLERANCE, HARD_MAX):
            blocs.append((titre_sec, idxs))
            continue
        # index locaux -> globaux
        sous_pages = [pages_texte[i] for i in idxs]
        groupes = _decouper_pages(sous_pages, cible)
        nb = len(groupes)
        for k, g in enumerate(groupes, start=1):
            globaux = [idxs[j] for j in g]
            if nb > 1:
                titre = f"{titre_sec} (partie {k}/{nb})" if titre_sec else f"Partie {k}/{nb}"
            else:
                titre = titre_sec
            blocs.append((titre, globaux))

    # Titres automatiques pour les découpages sans section nommée.
    for _k, (titre_sec, idxs) in enumerate(blocs):
        if not titre_sec:
            blocs[_k] = (_titre_section(pages_texte[idxs[0]]), idxs)

    # Numérotation cohérente des séries de parties (aucun chapitre ne
    # dépasse HARD_MAX : garanti par _decouper_pages).
    blocs = _renumeroter(blocs)

    # Annexes PDF (reference, hors chaîne) — pages réelles.
    annexes_export = []
    if annexes_pages:
        dossier = _dossier_sortie(s, config_manuel)
        for titre, d, f in annexes_pages:
            annexes_export.append((titre, d - 1, f - 1))  # 0-based inclusif

    if not apply:
        total = sum(len(idxs) for _, idxs in blocs)
        print(f"[analyse] {sid} : {len(blocs)} chapitres, {total} pages, "
              f"annexes={len(annexes_export)}")
        for k, (t, idxs) in enumerate(blocs[:40], start=1):
            tg = sum(len(pages_texte[i]) + 3 for i in idxs)
            print(f"    {k:>2}. p.{idxs[0]+1}-{idxs[-1]+1} ({tg}c) {t[:60]}")
        return

    dossier = _dossier_sortie(s, config_manuel)
    os.makedirs(dossier, exist_ok=True)

    # 1) PDF chapitres.
    nouveaux = []
    nb_total = len(blocs)
    extras = {
        k: s[k]
        for k in ("cartes", "artwork", "objets", "enigmes", "annexes")
        if s.get(k)
    }
    annexe_originale = {
        "nom": "Aventure complete (PDF original, reference)",
        "fichier": s["pdf"],
    }
    for k, (titre_sec, idxs) in enumerate(blocs, start=1):
        nom_fichier = (
            f"{_slug(s['titre'], 24)}-{k:02d}-{_slug(titre_sec, 30)}.pdf"
        )
        dest = os.path.join(dossier, nom_fichier)
        taille = _exporter_pdf(chemin_source, idxs, dest)
        if taille > PLAFOND_APP:
            print(f"  ⚠️ {os.path.basename(dest)} = {taille}c > plafond !")
        texte = _propre(" ".join(pages_texte[i] for i in idxs[:3]))[:200]
        url = "/data/scenarios/" + os.path.relpath(
            dest, ARBRE_SCENARIOS
        ).replace(os.sep, "/")
        entree = {
            "id": f"{sid}_ch{k:02d}",
            "titre": f"{s['titre']} {k}/{nb_total} — {titre_sec or 'Partie ' + str(k)}",
            "pdf": url,
            "pitch": (
                f"Partie {k}/{nb_total} de « {s['titre']} » "
                f"({taille // 1000}k caracteres). " + texte
            )[:400],
            "niveau": s.get("niveau", ""),
            "joueurs": s.get("joueurs", ""),
            "campagne": s["titre"],
            "chapitre": k,
            "chapitre_total": nb_total,
        }
        if k < nb_total:
            entree["chapitre_suivant"] = f"{sid}_ch{k + 1:02d}"
        for cle, val in extras.items():
            entree[cle] = json.loads(json.dumps(val, ensure_ascii=False))
        annexes = list(entree.get("annexes") or [])
        annexes.append(annexe_originale)
        entree["annexes"] = annexes
        nouveaux.append(entree)

    # 2) Annexes de référence (hors chaîne).
    for titre, d0, f0 in annexes_export:
        nom_fichier = f"annexe-{_slug(titre)}.pdf"
        dest = os.path.join(dossier, nom_fichier)
        _exporter_pdf(chemin_source, list(range(d0, f0 + 1)), dest)
        url = "/data/scenarios/" + os.path.relpath(
            dest, ARBRE_SCENARIOS
        ).replace(os.sep, "/")
        for entree in nouveaux:
            entree["annexes"].append({"nom": titre, "fichier": url})

    # 3) Remplace l'entrée dans le catalogue.
    idx = universe["scenarios"].index(s)
    universe["scenarios"][idx:idx + 1] = nouveaux

    # 4) Manifestes donjon.
    touches = _maj_manifestes(sid, [e["id"] for e in nouveaux])

    print(f"[apply] {sid} : {nb_total} chapitres écrits dans {dossier}")
    if touches:
        for t in touches:
            print(f"        manifeste mis à jour : {os.path.basename(t)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrire les modifications")
    parser.add_argument("--cible", type=int, default=CIBLE)
    parser.add_argument("--ids", nargs="*", help="restreint à ces identifiants")
    args = parser.parse_args()

    cata = _charger_catalogue()
    cibles = _scenarios_oversizes(cata)
    if args.ids:
        cibles = [(i, s) for i, s in cibles if i in set(args.ids)]
    # Du plus volumineux au plus petit.
    def _taille(entree):
        chemin = os.path.join(RACINE, "server", entree[1]["pdf"].lstrip("/"))
        with pymupdf.open(chemin) as d:
            return sum(len(p.get_text()) + 3 for p in d)
    cibles.sort(key=lambda e: -_taille(e))

    univers_par_scenario = {}
    for u in cata.get("universes", []):
        for s in u.get("scenarios", []):
            univers_par_scenario[id(s)] = u

    for sid, s in cibles:
        u = univers_par_scenario.get(id(s))
        if u is None:
            continue
        traiter(s, u, args.apply, args.cible)

    if args.apply:
        with open(CATALOGUE, "w", encoding="utf-8") as f:
            json.dump(cata, f, ensure_ascii=False, indent=1)
        print("Catalogue réécrit :", CATALOGUE)


if __name__ == "__main__":
    main()
