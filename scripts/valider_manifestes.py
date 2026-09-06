import json, sys, os, glob, unicodedata, re

BESTIAIRE = json.load(open('server/data/bestiaire.json', encoding='utf-8'))
CREATURES = {k for k in BESTIAIRE if k != '_meta'}  # clés canoniques (ex: loup_garou)


def _normalise_nom(s: str) -> str:
    """Même normalisation que server/tools/base.py : accent/casse/sep → '_'."""
    s = s.lower().strip()
    s = ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )
    return re.sub(r'[^a-z0-9]+', '_', s).strip('_')


CREATURES_NORM = {_normalise_nom(k): k for k in CREATURES}


def fp_pour(nom):
    return BESTIAIRE.get(nom, {}).get('fp', None)

def numerique(fp):
    if fp is None:
        return None
    try:
        return eval(str(fp).replace('1/2', '0.5').replace('1/3', '0.333').replace('1/4', '0.25').replace('1/6', '0.166').replace('1/8', '0.125').replace('1/10', '0.1'))
    except Exception:
        return None

def valider(chemin):
    nom = os.path.basename(chemin)
    pbs = []
    try:
        m = json.load(open(chemin, encoding='utf-8'))
    except Exception as e:
        return [f"{nom}: JSON invalide: {e}"]
    for ni, etage in enumerate(m['etages']):
        salles = etage['salles']
        coords = set()
        voisins = {}
        for s in salles:
            c = (s['x'], s['y'])
            if c in coords:
                pbs.append(f"{nom} étage {ni}: coordonnée doublée {c}")
            coords.add(c)
            if not s.get('description'):
                pbs.append(f"{nom} étage {ni} {c}: description vide")
            if s['type'] not in ('entrée',) and not s.get('portes'):
                pbs.append(f"{nom} étage {ni} {c}: porte manquante")
            for dir, on in (s.get('portes') or {}).items():
                if on:
                    delta = {'nord': (0, -1), 'sud': (0, 1), 'est': (1, 0), 'ouest': (-1, 0)}[dir]
                    voisins.setdefault(c, []).append((c[0] + delta[0], c[1] + delta[1]))
            for e in s.get('ennemis', []) or []:
                nom_brut = re.sub(r'\s*[×xX]\s*[0-9].*$', '', e).strip()
                # candidats : premier mot, puis nom complet normalisé
                canon = CREATURES_NORM.get(_normalise_nom(nom_brut.split(' ')[0])) or \
                        CREATURES_NORM.get(_normalise_nom(nom_brut))
                if canon is None:
                    pbs.append(f"{nom} étage {ni} {c}: créature inconnue '{e}'")
                else:
                    fp = numerique(fp_pour(canon))
                    if fp is not None and fp > 5:
                        pbs.append(f"{nom} étage {ni} {c}: '{e}' FP {fp} > 5 (mettre en note)")
        # symétrie
        porte_trous = {}  # c -> set de voisins demandés
        for c, v in voisins.items():
            for d in v:
                if d not in voisins or c not in voisins[d]:
                    pbs.append(f"{nom} étage {ni}: porte asymétrique {c} <-> {d}")
        # connexité depuis l'entrée
        entree = tuple(etage['entree'])
        vu = set()
        pile = [entree]
        while pile:
            c = pile.pop()
            if c in vu:
                continue
            vu.add(c)
            for d in voisins.get(c, []):
                pile.append(d)
        inac = {c for c in coords if c not in vu}
        if inac:
            pbs.append(f"{nom} étage {ni}: salles inaccessibles {inac}")
    return pbs

if __name__ == '__main__':
    cibles = sys.argv[1:] or glob.glob('server/data/scenarios/**/*.donjon.json', recursive=True)
    total = 0
    for c in cibles:
        pbs = valider(c)
        for p in pbs:
            print('PBS:', p)
        total += len(pbs)
    print(f"{len(cibles)} manifestes, {total} problèmes" if not cibles or len(pbs) else f"{len(cibles)} manifestes")
    print('OK' if total == 0 else f'{total} problème(s)')