"""Outil Monstres — adapté de `Outil_ImageMonstre.py`.

Renvoie la fiche complète + génère/c sert l'image d'un monstre D&D 3.5.

Spécificité de l'app standalone (vs OpenWebUI) :
- Pas de backend de génération d'image AI (pas d'OpenWebUI AutoImage ici).
  À la place : un **placeholder SVG stylé** est généré et mis en cache sous
  `data/bestiaire_cache/<slug>.svg`. Si une vraie image PNG existe déjà au
  même chemin, elle est servie en priorité. L'utilisateur peut déposer ses
  propres PNGs dans ce dossier pour remplacer les placeholders.
- L'image est exposée au front via l'URL `/data/bestiaire_cache/<slug>.svg`
  (route servie par StaticFiles dans `main.py`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool
from ..image.helpers import generer_averti, monstre_prompt

_log = logging.getLogger("dnd35.monstres")


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
_BESTIAIRE_CACHE: Optional[dict[str, Any]] = None
_BESTIAIRE_MTIME: float = 0.0


def _slug(s: str) -> str:
    nf = unicodedata.normalize("NFKD", s)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only.strip())
    return ascii_only[:60].strip("_").lower() or "monstre"


# Marqueurs de numérotation ajoutés aux homonymes en combat (« Gobelin »,
# « Gobelin (2) », « Gobelin 2 », « Gobelin #2 »). On les retire pour obtenir
# le NOM DE TYPE canonique : deux créatures identiques doivent partager la
# MÊME image, pas en générer une aléatoire par individu numéroté.
_TYPE_SUFFIX_RE = re.compile(
    r"(?:[\s_\-]*[\(\[](?:#?\d+)[\)\]])|(?:[\s_\-]+#?\d+)$",
    re.IGNORECASE,
)


def _type_nom(nom: str) -> str:
    """Nom de type canonique d'un monstre de combat.

    « Gobelin (2) » → « Gobelin », « Gobelin #3 » → « Gobelin ». Sert de clé
    de cache d'image pour qu'un groupe de monstres identiques réutilise la
    même illustration au lieu d'en régénérer une aléatoire par individu.
    """
    n = str(nom or "").strip()
    for _ in range(3):
        dec = _TYPE_SUFFIX_RE.sub("", n).strip()
        if dec == n:
            break
        n = dec
    return n.strip() or str(nom or "").strip()


def _cache_key(nom: str) -> str:
    """Clé de cache d'image d'un monstre (slug du nom de type canonique).

    Centralise tous les chemins image sur le NOM DE TYPE : « Gobelin (2) »
    partage le PNG/méta de « Gobelin ».
    """
    return _slug(_type_nom(nom))


def _bestiaire_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.data_dir, "bestiaire.json")


def _load_bestiaire(ctx: ToolContext) -> dict[str, Any]:
    """Charge le bestiaire avec cache memo recharge si mtime change."""
    global _BESTIAIRE_CACHE, _BESTIAIRE_MTIME
    path = _bestiaire_path(ctx)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {"_meta": {"nb_monstres": 0}, "monstres": {}}
    if _BESTIAIRE_CACHE is None or mt != _BESTIAIRE_MTIME:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Le bestiaire source a les monstres en clés top-level (hors _meta).
            monstres: dict[str, Any] = {}
            for k, v in raw.items():
                if k == "_meta":
                    continue
                if isinstance(v, dict) and "nom" in v:
                    monstres[v.get("cle", k)] = v
            raw["monstres"] = monstres
            _BESTIAIRE_CACHE = raw
            _BESTIAIRE_MTIME = mt
        except (json.JSONDecodeError, OSError):
            return {"_meta": {"nb_monstres": 0}, "monstres": {}}
    return _BESTIAIRE_CACHE  # type: ignore[return-value]


# Prompts « génériques » écrits par les scripts d'import (enrichir_bestiaire,
# import_bestiaire_drs) : ils ne contiennent que le nom, aucune info visuelle.
_GENERIC_PROMPT_RE = re.compile(
    r"^fantasy\s+.+\bcreature\b,\s*D&D 3\.5( manual)? illustration",
    re.IGNORECASE,
)

# Animaux, compagnons, montures ET créatures importées sans type ni
# `prompt_image` (« — » dans le bestiaire) : apparence anglaise curée pour
# le générateur. Sans elle, le repli RAG renvoyait des blocs de stats (ou
# rien) et Qwen-Image inventait une créature aléatoire (« Ane » → démon
# cuirassé, « Belette » → chat, observé en pré-génération 54de40ed).
# Les variantes « sanguinaire » = gabarit Félon (MM 3.5) : cornes + yeux
# rouges braise.
_APPARENCES_CUREES_EN: dict[str, str] = {
    "aigle": "majestic golden eagle, hooked beak, powerful talons",
    "ane": "gray donkey with long ears and sturdy hooves",
    "babouin": "baboon ape, dog-like muzzle, shaggy gray fur, long limbs",
    "belette": "slim brown weasel, elongated sinuous body, short legs",
    "bison": "massive bison, shaggy brown coat, curved horns",
    "blaireau": "european badger, black and white striped face, heavy claws",
    "calmar": "giant squid, pale flesh, long tentacles, large watchful eye",
    "chameau": "desert camel, tan fur, humped back, long lashes",
    "chat": "sleek tabby house cat, alert slit eyes",
    "chauve_souris": "small brown bat, leathery membranous wings, fanged muzzle",
    "chouette": "horned owl, large round golden eyes, mottled feathers",
    "corbeau": "black raven, glossy feathers, sharp beak",
    "crapaud": "warty toad, wide mouth, bulging eyes",
    "faucon": "peregrine falcon, pointed wings, fierce yellow eyes",
    "glouton": "wolverine, stocky muscular build, dark fur, sharp fangs",
    "leopard": "leopard, spotted golden coat, muscular feline body",
    "lezard": "small green lizard, long tail, quick feet",
    # NB : « (varan) » laisse un underscore final via _normalise_nom.
    "lezard_carnivore_varan_": "monitor lizard, venomous drooling jaws, long claws",
    "marsouin": "porpoise, sleek gray body, blunt snout",
    "mulet": "mule, long ears, sturdy pack frame",
    "pieuvre": "octopus, eight suckered arms, bulbous mantle",
    "poney": "small shaggy pony, gentle eyes",
    "poney_de_guerre": "barded war pony, muscular, trained for battle",
    "raie_manta": "giant manta ray, wide winged, gliding under water",
    "sanglier": "wild boar, bristly dark hide, curved tusks",
    "belette_sanguinaire": "ferocious dire weasel, elongated savage body, small horns, burning red eyes",
    "blaireau_sanguinaire": "ferocious dire badger, black and white fur, small horns, burning red eyes",
    "chauve_souris_sanguinaire": "dire bat, broad leathery wings, small horns, burning red eyes",
    "glouton_sanguinaire": "dire wolverine, massive shoulders, small horns, burning red eyes",
    "gorille_sanguinaire": "ferocious dire gorilla, massive arms, small horns, burning red eyes",
    "lion_sanguinaire": "fiendish lion, great mane, small horns, burning red eyes",
    "loup_sanguinaire": "fiendish wolf, bristling hackles, small horns, burning red eyes",
    "rat_sanguinaire": "fiendish giant rat, diseased fur, small horns, burning red eyes",
    "sanglier_sanguinaire": "fiendish boar, bristly hide, curved tusks, small horns, burning red eyes",
    "tigre_sanguinaire": "fiendish tiger, orange striped coat, small horns, burning red eyes",
    "deinonychus": "deinonychus raptor, feathered sickle-clawed dinosaur",
    "elasmosaure": "elasmosaurus, long-necked plesiosaur, flippers",
    "megaraptor": "megaraptor, huge sickle-clawed raptor dinosaur",
    "ours_hibou": "owl bear, massive bear body, owl-like beaked face, feathered shoulders",
    "pegase": "pegasus, white winged horse, feathered wings",
    "feu_follet": "floating sphere of ghostly glowing light, sinister aura",
    "homoncule": "small winged homunculus, gargoyle-like familiar, bat wings",
    "pseudo_dragon": "tiny red pseudo-dragon, impish dragon with wings",
    # Créatures DRS importées sans type — noms français canoniques.
    "horreur_chasseresse": "insectoid hunter horror, chitinous stalker, grasping claws, multiple eyes",
    "destrakhan": "rolling skeletal construct, wheel-shaped undead spinning on bladed rim",
    "dragonne": "drake-lion hybrid, dragon head, winged lion body, scaled hindquarters",
    "etrangleur": "strangler fiend, translucent rubbery gray body, long strangling arms",
    "ettercap": "ettercap, hunched spider-humanoid, pale multi-eyed face, clumsy clawed hands",
    "felin_marin": "sea cat, feline head and forequarters, fish tail, finned flanks",
    "foreur": "burrowing subterranean horror, armored drill-like head, tunnel maw",
    "fumigon": "smoke dragon, semi-solid vapor body, smoldering ember eyes",
    "garde_anime": "animated guard construct, hollow suit of armor floating, glowing eyes within helm",
    "geant_du_feu": "fire giant, obsidian-skinned towering humanoid, forge-blackened armor",
    "geant_des_nuages": "cloud giant, towering elegant humanoid, sky-blue skin, floating citadel garb",
    "geant_des_pierres": "stone giant, gray granite-skinned humanoid, angular features, stone tablet",
    "geant_des_tempetes": "storm giant, towering humanoid, pearl-hued skin, crackling lightning aura",
    "girallon": "girallon, four-armed white-furred ape, fanged muzzle",
    "jann": "jann genie, noble human form, ornate desert vestments, shimmering air",
    "annis": "annis hag, blue-skinned giantess, black eyes and hair, iron claws",
    "hurleur": "howler fiend, porcupine-like beast bristling with quills, howling maw",
    "nymphe": "nymph, breathtakingly beautiful woman, flowing verdant hair, radiant grace",
    "plasme": "amorphous plasma ooze, flowing translucent body, crackling energy veins",
    "ravageur_gris": "hulking gray-skinned ravager, corded muscle, savage claws",
    "rhast": "grotesque undead rhast, ragged flesh, hunched predator build",
    "secreteur": "dripping fungal horror, oozing secretions, bloated sacs",
    "androsphynx": "androsphinx, lion body with bearded human head, great wings",
    "criocephale": "ram-headed criosphinx, lion body, curled ram horns",
    "hieracosphynx": "hieracosphinx, falcon-headed sphinx, lion body, dark plumage",
    "sphinge": "gynosphinx, lion body with serene woman head, ornate headdress",
    "tenebreux_aile": "winged dark creeper, shrouded in tattered shadow, bat-like wings",
    "tenebreux_bipede": "dark creeper humanoid, wrapped head to toe in shadow bandages",
    "tenebreux_rampant": "crawling dark creeper, low slinking shadow-wrapped body",
    "tertre_errant": "walking mound, shambling earthen hill with hidden maw and limbs",
    "criard": "shrieking pale creature, distended gaping mouth, spindly limbs",
    "thallophyte_violet": "violet fungal plant creature, spongy stalk body, grasping tendrils",
    "thallophyte_spectrale": "ghostly fungal plant creature, pale spore-laden fronds",
    "thoqqua": "thoqqua, elemental fire worm, molten serpentine body, superheated horn",
    "tormante": "tormenting fiend, flaying barbed tendrils, writhing form",
    "torve": "hunched twisted brute, warped frame, sidelong glaring eyes",
    "traqueur_invisible": "invisible stalker, humanoid outline shimmering in the air, barely visible",
    "triton": "triton, aquatic humanoid, green-scaled skin, ornate trident",
    "troglodyte": "troglodyte, reptilian humanoid, dull gray scaled skin, fetid musk",
    # Autres animaux courants présents dans le bestiaire (type « — »).
    "crocodile": "crocodile, armored scaly body, long tooth-filled jaws",
    "elephant": "elephant, gray trunked giant, ivory tusks",
    "guepard": "cheetah, spotted lithe hunter, tear-marked face",
    "hyene": "hyena, sloped hunched body, mangy spotted coat",
    "lion": "lion, golden-maned great cat",
    "ours_brun": "brown bear, massive grizzly, curved claws",
    "ours_polaire": "polar bear, white-furred arctic bear",
    "rat": "scurrilous brown rat, naked tail, beady eyes",
    "rhinoceros": "rhinoceros, thick gray hide, great horn",
    "serpent_constricteur": "giant constrictor snake, coiling muscular body",
    "singe": "monkey, agile tree primate, long tail",
    "tigre": "tiger, orange striped great cat",
    "kraken": "kraken, colossal deep-sea squid monster, ship-crushing tentacles",
    "calmar_geant": "giant squid, enormous pale mantle, grasping tentacles",
    "crocodile_geant": "giant crocodile, massive armored jaws",
    "gorille": "silverback gorilla, knuckle-walking giant ape",
    "pieuvre_geante": "giant octopus, eight huge suckered arms",
    "hibou_geant": "giant owl, huge silent raptor, luminous eyes",
    "gobelours": "bugbear, hulking furry goblinoid, long arms, fanged muzzle",
    "gorgone": "gorgon, iron-scaled bull with petrifying breath, metallic plates",
    "grick": "grick, worm-like serpent with beaked maw and tentacles",
    "guenaude_marine": "sea hag, bloated green-skinned crone, fishbelly pale eyes",
    "guenaude_verte": "green hag, warty green-skinned crone, clawed fingers",
    "naga_aquatique": "aquatic naga, serpent coil with human head, blue-green scales",
    "naga_corrupteur": "spirit naga, sinister serpent coil with human head, dark scales",
    "naga_gardien": "guardian naga, majestic serpent coil with human head, golden scales",
    "naga_tenebreux": "dark naga, serpent coil with human head, black-purple scales",
    "necrophage": "ghoul-like necrophage, corpse-pale scavenger, ragged claws",
    "derro": "derro, pale blue-white-skinned mad dwarf, wild white hair, jagged teeth",
    "nuee_d_araignees": "swarming mass of spiders, countless skittering bodies",
    "nuee_de_criquets": "swarming locust cloud, devouring chitin haze",
    "nuee_de_guepes_infernales": "swarming hell wasp cloud, red-eyed stinging bodies",
    "nuee_de_mille_pattes": "swarming centipede mass, countless chitinous legs",
    "nuee_de_rats": "swarming rat flood, countless gnawing bodies",
    "ombre": "living shadow undead, bodiless dark silhouette, smoky edges",
    "otyugh": "otyugh, bloated mound body, three legs, two tentacle arms, eye-stalk maw",
    "oxydeur": "rust-eating ooze construct, corroded metal veins",
    "rakshasa": "rakshasa, tiger-headed humanoid in fine robes, backwards hands",
    "ravid": "ravid undead, skeletal spark-bearer, glowing positive energy",
    "remorhaz": "remorhaz, gigantic arctic centipede worm, glowing hot segments",
    "rukh": "rukh, colossal iron raptor bird of war",
    "strige": "strige, winged vampire-ish stingbird, bloated blood-fed body",
    "sylvanien": "sylvan guardian, woodland fey warrior, leaf-woven garb",
    "tarasque": "tarasque, colossal armored beast, spined shell, gaping devouring maw",
    "tendriculaire": "tendriculos, enormous plant-bramble horror, grasping vines",
    "titan": "titan, towering godlike giant, regal and immense",
    "vampirien": "vampiric winged creature, leathery wings, blood-draining maw",
    "vargouille": "vargouille, flying severed-head vampire, trailing hair, dangling entrails",
    "pouding_noir": "black pudding ooze, glossy acidic black mass",
    "vase_grise": "gray ooze, stone-like slimy sludge mass",
    "ver_des_glaces": "ice worm, frost-rimed burrowing serpentine worm",
    "abeille_geante": "giant bee, furry striped bumble body, translucent wings",
    "charancon_geant": "giant weevil beetle, long snout, iridescent carapace",
    "guepe_geante": "giant wasp, yellow-black striped body, buzzing wings",
    "punaise_de_feu_geante": "giant fire beetle, glowing amber glands, armored shell",
    "scarabe_geant": "giant scarab beetle, armored iridescent shell",
    "wiverne": "wyvern, sinuous winged dragon, barbed stinging tail",
    "xill": "xill, four-armed reptilian planar humanoid, blue scaled skin",
    "yrthak": "yrthak, flightless blind dragon, sonic lance crest",
}

# Traduction FR→EN des types du bestiaire — le générateur d'images (Qwen-Image)
# réagit bien mieux aux mots-clés anglais (« undead », « giant »…).
_TYPES_EN: list[tuple[str, str]] = [
    ("mort-vivant", "undead creature, rotting gray flesh"),
    ("extérieur", "outsider"),
    ("exterieur", "outsider"),
    ("élémentaire", "elemental"),
    ("elementaire", "elemental"),
    ("créature magique", "magical beast"),
    ("creature magique", "magical beast"),
    ("créature monstrueuse", "monstrous beast"),
    ("créature artificielle", "construct"),
    ("créature feérique", "fey creature"),
    ("créature aberrante", "aberration"),
    ("monstre aberrant", "aberration"),
    ("humanoïde", "humanoid"),
    ("humanoide", "humanoid"),
    ("aberration", "aberration"),
    ("animal", "animal"),
    ("bête", "beast"),
    ("dragon", "dragon"),
    ("fée", "fey"),
    ("géant", "giant"),
    ("construct", "construct"),
    ("vase", "ooze"),
    ("ver", "vermin creature"),
    ("plante", "plant creature"),
]


def _type_en(type_fr: str) -> str:
    """Traduit un type de monstre FR (« Mort-vivant ») en mot-clé EN."""
    t = type_fr.strip().lower()
    for fr, en in _TYPES_EN:
        if t.startswith(fr):
            return en
    return ""


def _desc_locale(m: Optional[dict[str, Any]]) -> str:
    """Description visuelle d'un monstre depuis le bestiaire local.

    - `prompt_image` riche (ex. goule : « ghoul undead creature, gaunt
      human, elongated claws... ») — utilisé tel quel ;
    - sinon ANIMAL connu (type « — » : compagnons animaux, montures) →
      apparence anglaise curée `_ANIMAUX_EN` (le repli RAG renvoie des
      blocs de stats ou RIEN pour ces entrées — images aléatoires
      observées : « Ane » → démon cuirassé, « Belette » → chat) ;
    - sinon on retombe sur le type traduit en anglais (donne déjà
      « undead creature » à Qwen-Image au lieu de rien du tout).
    Renvoie "" si le monstre est absent ou sans information exploitable.
    """
    if m is None:
        return ""
    pi = str(m.get("prompt_image") or "").strip()
    if pi and not _GENERIC_PROMPT_RE.match(pi):
        return pi
    # Animaux et montures (type « — ») : apparence curée — le repli RAG
    # renvoie des blocs de stats ou rien pour ces entrées.
    animal = _APPARENCES_CUREES_EN.get(_normalise_nom(str(m.get("nom") or "")))
    if animal:
        return animal
    return _type_en(str(m.get("type", "")))


async def _desc_via_rag(nom: str) -> str:
    """Cherche la description physique du monstre dans la KB RAG.

    Le Manuel des Monstres 3.5 (ingéré dans le vector store) commence chaque
    entrée par un portrait : « Ce mort-vivant ressemble à un humain émacié,
    à la chair grise et décomposée... ». C'est la meilleure source quand le
    bestiaire local n'a pas de prompt visuel exploitable. Fail-safe : toute
    erreur (embedding server down, chroma absente...) renvoie "".
    """
    try:
        from ..config import get_config
        from ..rag.store import get_store

        store = get_store(get_config())
        hits = await store.query(
            f"{nom} description physique apparence aspect du monstre",
            top_k=8,
        )
    except Exception:                                        # noqa: BLE001
        return ""
    n = _normalise_nom(nom)
    morceaux: list[str] = []
    total = 0
    for h in hits:
        # On ne garde que les extraits qui parlent bien DE ce monstre.
        hay = _normalise_nom(h.title + " " + h.text[:300])
        if n not in hay and not any(p in hay for p in n.split("_") if len(p) >= 4):
            continue
        # Rejet des BLOCS DE STATS (imports DRS : « # Nom / Dés de vie / … »)
        # — nourrir Qwen-Image avec ça donne une créature aléatoire. On ne
        # garde que les vrais PORTRAITS (le MM VF commence par
        # « Ce ... ressemble à ... »).
        debut = _normalise_nom(h.text[:250])
        if "ressemble" not in debut and (
                "des_de_vie" in debut or "source" in debut):
            continue
        extrait = h.text.strip()
        if total + len(extrait) > 900:
            extrait = extrait[: max(0, 900 - total)]
        if not extrait:
            break
        morceaux.append(extrait)
        total += len(extrait)
        if total >= 900:
            break
    return "\n".join(morceaux)


def _cache_dir(ctx: ToolContext) -> str:
    path = os.path.join(ctx.data_dir, "bestiaire_cache")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _normalise_nom(nom: str) -> str:
    """Normalise un nom pour recherche insensible à la casse/accents."""
    nf = unicodedata.normalize("NFKD", nom.lower())
    return re.sub(r"[^a-z0-9]+", "_", "".join(c for c in nf if not unicodedata.combining(c)))


# Le MJ (LLM) nomme souvent les monstres en anglais (« Ghoul », « Goblin »…)
# alors que le bestiaire local est en français (« Goule », « Gobelin »).
# Sans ce pont, la fiche est introuvable ET le prompt d'image part vide →
# le générateur invente un monstre quelconque.
_ALIAS_EN_FR: dict[str, str] = {
    "ghoul": "goule",
    "ghast": "goule",
    "skeleton": "squelette",
    "zombie": "zombie",
    "goblin": "gobelin",
    "hobgoblin": "hobgobelin",
    "bugbear": "gobelours",
    "orc": "orque",
    "ogre": "ogre",
    "ogre_mage": "ogre-mage",
    "troll": "troll",
    "gnoll": "gnoll",
    "minotaur": "minotaure",
    "gargoyle": "gargouille",
    "basilisk": "basilic",
    "chimera": "chimere",
    "cockatrice": "cocatrix",
    "djinni": "djinn",
    "efreeti": "efrit",
    "ettin": "ettin",
    "griffin": "griffon",
    "griffon": "griffon",
    "harpy": "harpie",
    "hippogriff": "hippogriffe",
    "manticore": "manticore",
    "medusa": "meduse",
    "mimic": "mimique",
    "mummy": "momie",
    "unicorn": "licorne",
    "vampire": "vampire",
    "wyvern": "wyverne",
    "succubus": "succube",
    "pixie": "pixie",
    "nymph": "nymphe",
    "pegasus": "pegase",
    "satyr": "satyre",
    "centaur": "centaure",
    "werewolf": "loup_garou",
    "wolf": "loup",
    "dire_wolf": "loup_terrible",
    "worg": "worg",
    "owlbear": "ours_hibou",
    "shadow": "ombre",
    "wraith": "spectre",
    "spectre": "spectre",
    "green_hag": "guenaude_verte",
    "sea_hag": "guenaude_marine",
    "purple_worm": "ver_pourpre",
    "flesh_golem": "golem_de_chair",
    "clay_golem": "golem_d_argile",
    "iron_golem": "golem_de_fer",
    "stone_golem": "golem_de_pierre",
    "kraken": "kraken",
    "lamia": "lamie",
    "rakshasa": "rakshasa",
    "tarasque": "tarasque",
    "tarrasque": "tarasque",
    "hydra": "hydre_5_tetes",
    "sahuagin": "sahuagin",
    "locathah": "locathah",
    "troglodyte": "troglodyte",
    "ettercap": "ettercap",
    "otyugh": "otyugh",
    "remorhaz": "remorhaz",
}

# Traduction mot à mot (secours pour les noms composés non listés ci-dessus :
# « young_red_dragon » → « jeune_rouge_dragon » ≈ inclusion dans
# `dragon_rouge_jeune` grâce à la recherche par inclusion).
_MOTS_EN_FR: dict[str, str] = {
    "red": "rouge", "black": "noir", "blue": "bleu", "green": "vert",
    "white": "blanc", "brass": "laiton", "bronze": "bronze",
    "copper": "cuivre", "gold": "or", "silver": "argent",
    "young": "jeune", "adult": "adulte", "old": "vieux",
    "ancient": "ancien", "elder": "ancien",
    "giant": "geant", "dire": "terrible", "great": "grand",
    "hill": "collines", "frost": "givre", "fire": "feu",
    "cloud": "nuages", "stone": "pierres", "storm": "tempetes",
    "bear": "ours", "spider": "araignee", "bat": "chauve_souris",
    "rat": "rat", "scorpion": "scorpion", "crocodile": "crocodile",
    "octopus": "pieuvre", "squid": "calmar", "snake": "serpent",
    "constrictor": "constricteur", "bee": "abeille", "wasp": "guepe",
    "beetle": "scarabee", "ant": "fourmi", "eagle": "aigle",
    "lion": "lion", "tiger": "tigre", "hyena": "hyene",
    "dragon": "dragon", "dragonne": "dragonne",
}

# Adversaires humains / PNJ « inventés » par un scénario → fiche officielle du
# bestiaire. Le MJ ne doit JAMAIS inventer les stats d'un garde, bandit ou
# chenapan de la foule : on les ramène à leur entrée canonique dans le
# bestiaire (gardes, chenaille, bandits, hommes d'armes). Clés = variantes
# génériques FR (normalisées), valeurs = clé canonique du bestiaire.
_ALIAS_HUMAIN_GENERIQUE: dict[str, str] = {
    # Gardes / soldats / surveillance
    "gardes": "garde", "garde": "garde", "garde_de_la_ville": "garde",
    "gardien_humain": "garde", "soldat_humain": "garde",
    "milice_humaine": "garde", "homme_d_armes": "garde",
    "hommes_d_armes": "garde", "guerrier_humain": "garde",
    "sergent_humain": "garde",
    # Foule / populace / chenapan
    "chenaille": "chenaille", "cheneaille": "chenaille",
    "chenapan": "chenaille", "chenapans": "chenaille",
    "chenapane": "chenaille", "chenapanes": "chenaille",
    "foule_humaine": "chenaille", "foule": "chenaille", "populace": "chenaille",
    "paysan_humain": "chenaille", "paysan": "chenaille",
    "paysans_humains": "chenaille", "paysans": "chenaille",
    "paysanne": "chenaille", "paysannes": "chenaille",
    "gredin_humain": "chenaille", "gredins_humains": "chenaille",
    "gredin": "chenaille", "gredins": "chenaille",
    "ruffian_humain": "chenaille", "ruffians_humains": "chenaille",
    "ruffian": "chenaille", "ruffians": "chenaille",
    "voyou_humain": "chenaille", "voyous_humains": "chenaille",
    "voyou": "chenaille", "voyous": "chenaille",
    "malfrat_humain": "chenaille", "malfrats_humains": "chenaille",
    "malfrat": "chenaille", "malfrats": "chenaille",
    # Bandits / brigands
    "bandit": "bandit", "bandits": "bandit", "brigand_humain": "bandit",
    "brigand": "bandit", "brigands_humains": "bandit", "brigands": "bandit",
    "pillard_humain": "bandit", "pillard": "bandit", "pillards_humains": "bandit",
    "pillards": "bandit", "maraudeur_humain": "bandit",
    "maraudeur": "bandit", "maraudeurs_humains": "bandit",
    "maraudeurs": "bandit", "voleur_humain": "bandit",
    "voleur": "bandit", "voleurs_humains": "bandit", "voleurs": "bandit",
    "assassin_humain": "bandit", "assassin": "bandit", "assassins": "bandit",
    # Hommes d'armes aasimar / gardes d'élite
    "aasimar": "aasimar_homme_d_armes_de_niveau_1",
    "aasimar_homme_d_armes": "aasimar_homme_d_armes_de_niveau_1",
    "homme_d_armes_aasimar": "aasimar_homme_d_armes_de_niveau_1",
    "chevalier_aasimar": "aasimar_homme_d_armes_de_niveau_1",
    "paladin_aasimar": "aasimar_homme_d_armes_de_niveau_1",
}


def _candidats_noms(nom: str) -> list[str]:
    """Noms candidats pour la recherche : original + alias EN→FR + traduction
    mot à mot (dédupe en conservant l'ordre)."""
    n = _normalise_nom(nom)
    cands = [nom, n]
    alias = _ALIAS_EN_FR.get(n)
    if alias:
        cands.append(alias)
        cands.append(_normalise_nom(alias))
    # Alias « PNJ humains génériques » → fiche officielle du bestiaire
    # (garde, chenaille, bandit, aasimar…). On ajoute la clique canonique pour
    # que garde_de_la_ville → garde_humain_guerrier_2, etc. On balaie aussi
    # chaque mot significatif du nom (« une meute de chenapans » → chenaille).
    generique = _ALIAS_HUMAIN_GENERIQUE.get(n)
    if generique:
        cands.append(generique)
        cands.append(_normalise_nom(generique))
    else:
        for w in n.split("_"):
            if len(w) < 3:
                continue
            g = _ALIAS_HUMAIN_GENERIQUE.get(w)
            if g:
                cands.append(g)
                cands.append(_normalise_nom(g))
    trad = "_".join(_MOTS_EN_FR.get(w, w) for w in n.split("_"))
    if trad != n:
        cands.append(trad)
    return list(dict.fromkeys(cands))


def _find_monstre(ctx: ToolContext, nom: str) -> Optional[dict[str, Any]]:
    """Cherche un monstre par nom FR ou EN (insensible casse/accents/alias,
    y compris noms composés réordonnés : « Young Red Dragon » →
    « dragon_rouge_jeune » via sous-ensemble de mots traduits)."""
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {})
    cands = _candidats_noms(nom)
    # 1. clé exacte (slugifiée) — sur chaque candidat (alias inclus)
    for cand in cands:
        n = _normalise_nom(cand)
        for k, m in monstres.items():
            if _normalise_nom(k) == n or _normalise_nom(m.get("nom", "")) == n:
                return m
    # 2. inclusion (« dragon rouge » → « dragon_rouge_jeune »)
    for cand in cands:
        n = _normalise_nom(cand)
        if len(n) < 4:
            continue
        for k, m in monstres.items():
            if n in _normalise_nom(k) or n in _normalise_nom(m.get("nom", "")):
                return m
    # 3. sous-ensemble de mots traduits — gère les ordres différents
    #    ({jeune,rouge,dragon} ⊆ {dragon,rouge,jeune}) ; la clé la plus
    #    longue gagne.
    meilleur: Optional[tuple[int, dict[str, Any]]] = None
    for cand in cands:
        mots = [
            w for w in _normalise_nom(cand).split("_")
            if len(w) >= 3 and w != "monstre"
        ]
        if not mots:
            continue
        for k, m in monstres.items():
            nk = _normalise_nom(k)
            if all(w in nk for w in mots):
                if meilleur is None or len(nk) > meilleur[0]:
                    meilleur = (len(nk), m)
    if meilleur:
        return meilleur[1]
    # 4. mot-clé partagé : UN mot significatif de la requête correspond à un
    #    MOT ENTIER de la clé (« squelette armé d'une hache » → « squelette »,
    #    « goule des cryptes » → « goule »). Meilleur recouvrement d'abord,
    #    clé la plus longue pour départager. Singulier/pluriel naïf.
    def _sing(w: str) -> str:
        return w[:-1] if w.endswith("s") and len(w) > 3 else w

    meilleur4: Optional[tuple[int, int, dict[str, Any]]] = None
    for k, m in monstres.items():
        nk_tokens = {_sing(w) for w in _normalise_nom(k).split("_")}
        if not nk_tokens:
            continue
        score = 0
        for cand in cands:
            qwords = [w for w in _normalise_nom(cand).split("_") if len(w) >= 4]
            score += sum(1 for w in qwords if _sing(w) in nk_tokens)
        # on ne garde que les recouvrements réels (au moins un mot entier)
        if score == 0:
            continue
        # à recouvrement égal : la clé la plus COURTE gagne (nom canonique
        # simple « Squelette » plutôt que « … hommes d'armes de niveau 1 »)
        if meilleur4 is None or (score, -len(k)) > (meilleur4[0], -meilleur4[1]):
            meilleur4 = (score, len(k), m)
    return meilleur4[2] if meilleur4 else None


def _est_monstre_generique(m: Optional[dict[str, Any]]) -> bool:
    """Vrai pour les entrées PLACEHOLDER du bestiaire (« Mort-vivant de
    taille M », « Aberration de taille G »…) : nom = type + taille, champ
    `type` = « — ». Importées d'un corpus DRS, ces 72 fiches n'ont NI
    identité NI illustration correcte — ce ne sont pas des monstres
    jouables. Le MJ doit choisir une créature réelle (zombie, goule…) ou un
    ennemi officiel du scénario (partie fa4e7366/44b02cfc : un combat entier
    tourné autour d'un « Mort-vivant de taille M » jamais prévu par le
    module, avec image générique inadaptée)."""
    if not isinstance(m, dict):
        return False
    if str(m.get("type") or "").strip() not in ("—", "-", ""):
        return False
    return "_de_taille_" in _normalise_nom(str(m.get("nom") or ""))


def _generique_autorise_scenario(ctx: ToolContext, nom: str) -> bool:
    """Vrai si ce gabarit générique est RÉFÉRENCÉ par la partie active.

    Les scénarios utilisent ces fiches DRS comme GABARITS officiels de
    créatures absentes du bestiaire 3.5 : « Crypts Kelemvor » (flameskull →
    « Mort-vivant de taille M », FP 3) et « Army of the Damned » (3 gabarits
    extérieurs/artificiels). Le refus des placeholders ne doit PAS casser ces
    rencontres légitimes : on cherche le nom dans les ennemis déclarés
    (bible de quête, salles/étages du donjon chargé, monstres déjà engagés).
    Comparaison sur la chaîne normalisée du JSON : tolère les variantes
    (« Mort-vivant_de_taille_M ×1 », « Mort-vivant de taille M »…).
    """
    n = _normalise_nom(nom)
    if not n:
        return False
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        etat = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id
        ).load()
    except Exception:                                        # noqa: BLE001
        return False
    # ⚠️ Seules les DONNÉES DE SCÉNARIO autorisent le gabarit (donjon chargé,
    # bible de quête) — PAS `monstres_combat` : un placeholder déjà engagé
    # dans une vieille partie doit rester refusé (forcer la correction) au
    # lieu d'être accommodé indéfiniment.
    donjon_txt = _normalise_nom(
        json.dumps(etat.get("donjon") or {}, ensure_ascii=False)
    )
    quete_txt = _normalise_nom(
        json.dumps((etat.get("quete") or {}).get("bible") or {},
                   ensure_ascii=False)
    )
    return n in donjon_txt or n in quete_txt


def _ennemis_scenario(ctx: ToolContext) -> list[str]:
    """Ennemis officiels du scénario en cours (quete.bible.ennemis)."""
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        etat = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id
        ).load()
        return [
            str(x) for x in (
                ((etat.get("quete") or {}).get("bible") or {})
                .get("ennemis") or []
            ) if str(x).strip()
        ]
    except Exception:                                        # noqa: BLE001
        return []


def _suggestions_meme_type(
    ctx: ToolContext, m: dict[str, Any], limite: int = 6
) -> list[str]:
    """Vrais monstres du bestiaire du MÊME TYPE que l'entrée générique
    (« Mort-vivant de taille M » → zombie, squelette, goule, spectre…),
    triés par écart de FP quand c'est évaluable."""
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {}) or {}
    type_cible = str(m.get("nom") or "").split(" de taille ")[0].strip().lower()

    def _fp_num(fp: str) -> Optional[float]:
        try:
            fp = str(fp).strip()
            if "/" in fp:
                a, b = fp.split("/")
                return float(a) / float(b)
            return float(fp)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    cibles_fp = _fp_num(m.get("fp") or m.get("dv") or "")
    votes: list[tuple[float, float, str]] = []
    for k, cand in monstres.items():
        if _est_monstre_generique(cand) or k == m.get("nom"):
            continue
        if str(cand.get("type") or "").strip().lower() != type_cible:
            continue
        fp = _fp_num(cand.get("fp") or "")
        ecart = abs(fp - cibles_fp) if (fp is not None and cibles_fp is not None) else 99.0
        votes.append((ecart, len(k), str(cand.get("nom") or k)))
    votes.sort()
    return [nom for _, _, nom in votes[:limite]]


def _refus_generique_texte(ctx: ToolContext, nom: str, m: dict[str, Any]) -> str:
    """Message de refus pour une entrée placeholder : oriente vers les
    ennemis officiels du scénario PUIS les monstres réels du même type."""
    scen = _ennemis_scenario(ctx)
    sugg = _suggestions_meme_type(ctx, m)
    lignes = [
        f"⛔ **« {m.get('nom') or nom} » est une fiche PLACEHOLDER** (type+"
        "taille sans identité, sans illustration correcte) : ce n'est pas un "
        "monstre jouable.",
    ]
    if scen:
        lignes.append(
            "🎯 Ennemis officiels DU SCÉNARIO : " + ", ".join(scen) + " — "
            "utilise ces créatures en priorité (ou leurs proches parents du "
            "bestiaire, ex. les morts-vivants classiques du module)."
        )
    if sugg:
        lignes.append(
            "📚 Monstres réels du même type dans le bestiaire : "
            + ", ".join(sugg) + "."
        )
    lignes.append(
        "_Rejoue `combat_ajouter_combattant` / `engager_combat` avec un nom "
        "de créature RÉELLE du bestiaire._"
    )
    return "\n".join(lignes)


def _find_monstre_strict(ctx: ToolContext, nom: str) -> Optional[dict[str, Any]]:
    """Résolution STRICTE : le nom demandé doit être couvert par l'entrée
    (tous ses mots significatifs présents dans la clé). Refuse le repli « mot
    partagé seul » de `_find_monstre` : « Archer gobelin » ne résout PAS car
    aucune entrée ne contient « archer » ET « gobelin ». « dragon rouge » →
    « dragon_rouge_jeune » reste accepté (l'entrée est un sur-ensemble)."""
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {})

    def _sing(w: str) -> str:
        return w[:-1] if w.endswith("s") and len(w) > 3 else w

    for cand in _candidats_noms(nom):
        n = _normalise_nom(cand)
        for k, m in monstres.items():
            if _normalise_nom(k) == n or _normalise_nom(m.get("nom", "")) == n:
                return m
        if len(n) >= 4:
            for k, m in monstres.items():
                if n in _normalise_nom(k) or n in _normalise_nom(
                        m.get("nom", "")):
                    return m
    mots: list[str] = []
    for cand in _candidats_noms(nom):
        for w in _normalise_nom(cand).split("_"):
            if len(w) >= 3 and w != "monstre":
                wok = _sing(w)
                if wok not in mots:
                    mots.append(wok)
    if not mots:
        return None
    meilleur: Optional[tuple[int, dict[str, Any]]] = None
    for k, m in monstres.items():
        nk = _normalise_nom(k)
        if all(w in nk for w in mots):
            if meilleur is None or len(nk) > meilleur[0]:
                meilleur = (len(nk), m)
    return meilleur[1] if meilleur else None


def _find_monstre_with_fallback(ctx: ToolContext, nom: str) -> Optional[dict[str, Any]]:
    """Cherche un monstre dans le bestiaire. Si aucun match exact, tente un
    monstre générique basé sur le type et la taille demandés."""
    m = _find_monstre(ctx, nom)
    if m is not None:
        return m
    # Tenter un monstre générique (ex. "Créature magique de taille G")
    generique = _generer_monstre_genérique(nom, ctx)
    return generique


def _suggestions(monstres: dict[str, Any], cands: list[str],
                 limite: int = 3) -> list[str]:
    """Noms du bestiaire les plus proches d'une requête non résolue
    (recouvrement de mots entiers, puis longueur)."""
    def _sing(w: str) -> str:
        return w[:-1] if w.endswith("s") and len(w) > 3 else w

    scores: list[tuple[int, int, str]] = []
    for k in monstres:
        nk_tokens = {_sing(w) for w in _normalise_nom(k).split("_")}
        score = 0
        for cand in cands:
            qwords = [w for w in _normalise_nom(cand).split("_")
                      if len(w) >= 4]
            score += sum(1 for w in qwords if _sing(w) in nk_tokens)
        if score:
            scores.append((-score, len(k), k))
    scores.sort()
    return [k for _, _, k in scores[:limite]]


# Mapping tailles FR → facteur de mise à l'échelle des PV / CA
_TAILLE_ECHELLE: dict[str, dict[str, Any]] = {
    # taille → {pv_factor, ca_mod, label}
    "T":  {"pv_factor": 0.5, "ca_mod": -1, "label": "petite"},
    "P":  {"pv_factor": 0.75, "ca_mod": 0, "label": "petite"},
    "M":  {"pv_factor": 1.0, "ca_mod": 0, "label": "moyenne"},
    "G":  {"pv_factor": 1.5, "ca_mod": 1, "label": "grande"},
    "TG": {"pv_factor": 2.0, "ca_mod": 2, "label": "très grande"},
    "Gig":{"pv_factor": 3.0, "ca_mod": 3, "label": "gigantesque"},
    "Col":{"pv_factor": 4.0, "ca_mod": 4, "label": "colossale"},
}

# Alias de tailles (texte libre → clé)
_TAILLE_ALIAS: dict[str, str] = {}
for _k, _v in _TAILLE_ECHELLE.items():
    _TAILLE_ALIAS[_k.lower()] = _k
    _TAILLE_ALIAS[_v["label"].lower()] = _k
_taille_extra = {
    "t": "T", "p": "P", "m": "M", "g": "G", "tg": "TG",
    "gigantesque": "Gig", "colossal": "Col", "colossale": "Col",
    "tiny": "T", "small": "P", "medium": "M", "large": "G",
    "huge": "TG", "gargantuan": "Gig", "colossal": "Col",
    "minuscule": "T", "très petite": "P", "très grande": "TG",
}
_TAILLE_ALIAS.update(_taille_extra)


def _extraire_taille(nom: str) -> Optional[str]:
    """Extrait la taille d'un nom de monstre (ex. 'Créature magique de taille G' → 'G')."""
    n = nom.lower()
    # Cherche "taille X" ou "size X"
    m = re.search(r"(?:taille|size)\s+([A-Za-z]+)", n)
    if m:
        t = _TAILLE_ALIAS.get(m.group(1).lower())
        if t:
            return t
    # Cherche la taille seule en fin de chaîne
    for alias, cle in sorted(_TAILLE_ALIAS.items(), key=lambda x: -len(x[0])):
        if n.endswith(alias):
            return cle
    return None


def _generer_monstre_genérique(
    nom: str, ctx: ToolContext
) -> Optional[dict[str, Any]]:
    """Génère un monstre générique basé sur le nom et la taille demandés.

    Quand aucun monstre du bestiaire ne correspond, on crée une fiche
    minimaliste avec des stats de base proportionnées à la taille demandée.
    On tente aussi de copier la description visuelle (prompt_image) d'un
    monstre du même type dans le bestiaire pour que l'image générée
    ressemble à quelque chose de cohérent.
    Renvoie None si même le type ne peut pas être déterminé.
    """
    taille_cle = _extraire_taille(nom) or "M"
    echelle = _TAILLE_ECHELLE.get(taille_cle, _TAILLE_ECHELLE["M"])

    # Extraire le type de créature du nom
    type_fr = ""
    n_lower = nom.lower()
    for type_key, _ in _TYPES_EN:
        if type_key in n_lower:
            type_fr = type_key
            break

    # PV de base selon la taille (fourchette D&D 3.5 standard)
    pv_base = {
        "T": 3, "P": 6, "M": 10, "G": 20, "TG": 35, "Gig": 60, "Col": 100,
    }
    pv = max(1, int(pv_base.get(taille_cle, 10) * echelle["pv_factor"]))
    ca = 10 + echelle["ca_mod"]

    # Chercher un monstre du même type dans le bestiaire pour copier sa description
    prompt_description = ""
    if type_fr:
        best = _load_bestiaire(ctx)
        monstres_dict = best.get("monstres", {})
        for m in monstres_dict.values():
            if m.get("type", "").lower().startswith(type_fr.lower()):
                pi = str(m.get("prompt_image") or "").strip()
                if pi and not _GENERIC_PROMPT_RE.match(pi):
                    prompt_description = pi
                    break

    fiche = {
        "nom": nom,
        "type": type_fr or "inconnu",
        "taille": taille_cle,
        "dv": "1d8",
        "pv": pv,
        "pv_max": pv,
        "ca": ca,
        "vitesse": "9m",
        "bab": "+0",
        "init": "+0",
        "attaques": "1 attaque corpo",
        "degs": "1d6",
        "sauvegardes": "Vig +0, Réf +0, Vol +0",
        "carac": "For 10, Dex 10, Con 10, Int 2, Sag 10, Cha 10",
        "comp": "",
        "dons": "",
        "capacites": "",
        "faiblesses": "",
        "fp": "1/4",
        "alignement": "Neutre",
        "cle": _slug(nom),
        "prompt_image": prompt_description or (
            f"fantasy {type_fr or 'creature'} creature, "
            f"D&D style illustration, ink style, dramatic lighting"
        ),
        "generique": True,
    }

    # Persister dans le bestiaire pour les prochains appels
    try:
        best_path = _bestiaire_path(ctx)
        try:
            with open(best_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            raw = {}
        cle = _slug(nom)
        raw[cle] = fiche
        if "_meta" in raw and isinstance(raw["_meta"], dict):
            raw["_meta"]["nb_monstres"] = sum(
                1 for k in raw if k != "_meta"
                and isinstance(raw[k], dict) and "nom" in raw[k]
            )
        with open(best_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        global _BESTIAIRE_CACHE
        _BESTIAIRE_CACHE = None
    except Exception:
        pass

    return fiche


def _find_image(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie le chemin d'une vraie image (PNG/JPG/WebP) en cache.

    On exclut le `.svg` car c'est par convention un placeholder affiché en
    dépannage — pas une « vraie » image. On préfère générer un vrai PNG
    via ComfyUI plutôt que resservir un vieux SVG. Le cache est indexé sur le
    nom de TYPE (`_cache_key`) pour que les homonymes partagent une image.
    """
    slug = _cache_key(nom)
    cache_dir = _cache_dir(ctx)
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = os.path.join(cache_dir, f"{slug}.{ext}")
        if os.path.isfile(p):
            return p
    return None


def _find_svg(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie le SVG placeholder existant (le cas échéant)."""
    slug = _cache_key(nom)
    p = os.path.join(_cache_dir(ctx), f"{slug}.svg")
    return p if os.path.isfile(p) else None


def _placeholder_svg(nom: str) -> str:
    """Génère un SVG placeholder stylé D&D pour un monstre."""
    initiales = "".join(w[0].upper() for w in re.findall(r"[A-Za-zÀ-ÿ]+", nom)[:2]) or "?"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
        f'width="256" height="256" role="img" aria-label="{nom}">'
        f'<rect width="256" height="256" fill="#1a1a23" />'
        f'<rect x="4" y="4" width="248" height="248" fill="none" '
        f'stroke="#8a6d3b" stroke-width="2" rx="10" ry="10" />'
        f'<text x="128" y="135" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="100" font-weight="bold" '
        f'fill="#c4a96a">{initiales}</text>'
        f'<text x="128" y="200" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="20" fill="#b0b0b5">'
        f'<tspan>{nom}</tspan></text>'
        f'<text x="128" y="230" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="11" '
        f'font-style="italic" fill="#777">D&D 3.5 — placeholder</text>'
        f'</svg>'
    )


def _write_placeholder(ctx: ToolContext, nom: str) -> str:
    """Écrit le placeholder SVG et renvoie son chemin."""
    path = os.path.join(_cache_dir(ctx), f"{_cache_key(nom)}.svg")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_placeholder_svg(nom))
    except OSError:
        pass
    return path


def _url_for(path: str, data_dir: str) -> str:
    """Retourne l'URL publique pour servir l'image (StaticFiles sur `/data`).

    `path` est un chemin absolu sous `data_dir` ; on le rend relatif à `data_dir`
    et on préfixe par `/data/` (mount StaticFiles ajouté dans `main.py`).
    """
    from pathlib import Path
    try:
        rel = Path(path).relative_to(Path(data_dir))
        return "/data/" + rel.as_posix().lstrip("/")
    except ValueError:
        # En cas de chemin hors data_dir, on retombe sur le basename seul.
        return "/data/" + Path(path).name


def _format_fiche(m: dict[str, Any]) -> str:
    """Formate la fiche d'un monstre en Markdown lisible."""
    lignes = [
        f"🐉 **{m.get('nom','?')}** — FP {m.get('fp','?')}",
        f"- Type : {m.get('type','?')} ( taille {m.get('taille','?')} )",
        f"- DV : {m.get('dv','?')} — PV : {m.get('pv','?')} — CA : {m.get('ca','?')}",
        f"- Vitesse : {m.get('vitesse','?')} — Initiative : {m.get('init','?')}",
        f"- Attaques : {m.get('attaques','?')}",
        f"- Dégâts : {m.get('degs','?')}",
        f"- Sauvegardes : {m.get('sauvegardes','?')}",
        f"- Carac : {m.get('carac','?')}",
        f"- Compétences : {m.get('comp','?')}",
        f"- Dons : {m.get('dons','?')}",
        f"- Capacités : {m.get('capacites','—')}",
        f"- Faiblesses : {m.get('faiblesses','—')}",
        f"- Alignement : {m.get('alignement','?')}",
    ]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
#  Description + invalidation du cache d'images
# --------------------------------------------------------------------------- #
async def _description_monstre(m: Optional[dict[str, Any]], nom: str) -> str:
    """Meilleure description visuelle disponible : prompt du bestiaire local,
    sinon portrait du Manuel des Monstres via la KB RAG (requête avec le nom
    canonique FR — les manuels ingérés sont en VF)."""
    description = _desc_locale(m)
    if description:
        return description
    return await _desc_via_rag(str((m or {}).get("nom") or nom))


def _hash_desc(description: str) -> str:
    """Hash court de la description (invalidation du cache d'images).

    Inclut la version du prompt template (PROMPT_VERSION) pour forcer la
    régénération de toutes les images quand le prompt change (ex. ajout
    d'anti-texte renforcé).
    """
    try:
        from ..image.helpers import PROMPT_VERSION
        version = PROMPT_VERSION
    except ImportError:
        version = "v1"
    combined = f"{version}|{description}"
    return (
        hashlib.sha1(combined.encode("utf-8")).hexdigest()[:12]
        if description else ""
    )


def _meta_path_for(ctx: ToolContext, slug: str) -> str:
    return os.path.join(_cache_dir(ctx), f"{slug}.meta.json")


def _read_desc_hash(meta_path: str) -> Optional[str]:
    """Lit le hash de la description stocké (`desc_hash_v2`).

    La clé en v2 invalide tous les métadonnées anciennes (v1) : les images
    générées avec l'ancien template de prompt — souvent ornées d'écritures —
    sont ainsi régénérées automatiquement.
    """
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f).get("desc_hash_v2")
    except (OSError, ValueError):
        return None


def _write_desc_hash(meta_path: str, h: str, nom: str) -> None:
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"desc_hash_v2": h, "nom": nom}, f, ensure_ascii=False)
    except OSError:
        pass


async def image_pour(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie l'URL de l'illustration du monstre `nom`, en la générant au
    besoin via ComfyUI (cache prioritaire ; régénération si la fiche du
    bestiaire a été enrichie ou si le PNG date de l'ancien template).

    Utilisé par le hook post-tour de `main.py` : quand un monstre entre en
    jeu sans que le MJ ait appelé `monstre_consulter`, la table voit quand
    même son portrait. Renvoie toujours une URL (placeholder SVG en dernier
    recours), None seulement si même le placeholder est impossible.
    """
    # Toggle images de MONSTRES (config.yaml × maître GUI) : coupé → aucune
    # génération, aucune URL servie (la galerie « Monstres » est masquée).
    try:
        from ..config import get_config
        if not get_config().image.effective("monstres"):
            return None
    except Exception:                                            # noqa: BLE001
        pass
    m = _find_monstre(ctx, nom)
    # Nom canonique du bestiaire : le cache d'images est indexé DESSUS, pas
    # sur le nom demandé — sinon « Golem » et « Golem de chair » (même
    # créature) généreraient deux PNG distincts pour un hash identique.
    nom_canonique = str((m or {}).get("nom") or nom) if m is not None else nom
    if m is None:
        # Nom non résolu dans le bestiaire → PAS de génération ComfyUI : la
        # requête RAG renverrait le portrait d'un AUTRE monstre (bug
        # « Goule » affichée avec l'image d'un golem…). On purge l'éventuelle
        # image erronée du cache et on sert un placeholder clair.
        stale = _find_image(ctx, nom)
        if stale is not None:
            try:
                os.remove(stale)
            except OSError:
                pass
        _log.warning("bestiaire : monstre inconnu « %s » — image placeholder", nom)
        img_path = _write_placeholder(ctx, nom)
        return _url_for(img_path, ctx.data_dir)
    slug = _cache_key(nom_canonique)
    dest = os.path.join(_cache_dir(ctx), f"{slug}.png")
    meta_path = _meta_path_for(ctx, slug)
    img_path = _find_image(ctx, nom_canonique)
    description = ""
    if img_path is not None:
        meta_hash = _read_desc_hash(meta_path)
        hash_local = _hash_desc(_desc_locale(m))
        if meta_hash is not None and meta_hash == hash_local and hash_local:
            return _url_for(img_path, ctx.data_dir)
        description = await _description_monstre(m, nom)
        if meta_hash is not None and _hash_desc(description) == meta_hash:
            return _url_for(img_path, ctx.data_dir)
        # Image périmée → on supprime le fichier AVANT l'appel, sinon
        # generer_averti le voit déjà présent et court-circuite (cache hit)
        # sans rien régénérer.
        try:
            os.remove(img_path)
        except OSError:
            pass
    else:
        description = await _description_monstre(m, nom)
    prompt_text = monstre_prompt(_type_nom(nom_canonique), description)
    try:
        r = await generer_averti(ctx, "monstre", prompt_text, dest)
    except Exception:
        r = None
    if r is not None and os.path.isfile(r):
        _write_desc_hash(meta_path, _hash_desc(description), nom_canonique)
        return _url_for(r, ctx.data_dir)
    img_path = _find_image(ctx, nom) or _write_placeholder(ctx, nom)
    return _url_for(img_path, ctx.data_dir)


def _norm_nom_simple(s: Any) -> str:
    """Comparaison de noms insensible casse/accents (journal ↔ combat)."""
    s = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _phase_partie(ctx: ToolContext) -> str:
    """Phase courante de la partie (« combat », « exploration »…).

    Fail-safe : "" si l'état est illisible. Sert à réserver les
    illustrations de monstres aux phases de combat.
    """
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
        if "_erreur" in etat:
            return ""
        return str(etat.get("phase") or "")
    except Exception:                                            # noqa: BLE001
        return ""


def _fusionner_rencontres(
    etat: dict, rencontres: list[tuple[str, str]]
) -> bool:
    """Fusionne des (nom, url) dans le journal `rencontres_images` de l'état.

    Mute `etat` en place (pas de save ici — l'appelant persiste). Renvoie
    True si le journal a changé. Déduplique par nom normalisé, borne à 30.
    """
    connus = {
        _norm_nom_simple(r.get("nom"))
        for r in etat.get("rencontres_images") or []
    }
    changed = False
    for nom, url in rencontres:
        if not nom or not url:
            continue
        key = _norm_nom_simple(nom)
        if key in connus:
            continue
        etat.setdefault("rencontres_images", []).append(
            {"nom": str(nom), "url": url}
        )
        connus.add(key)
        changed = True
    if len(etat.get("rencontres_images") or []) > 30:
        etat["rencontres_images"] = etat["rencontres_images"][-30:]
        changed = True
    return changed


def _memoriser_rencontre(ctx: ToolContext, nom: str, url: str) -> None:
    """Persiste l'illustration d'une rencontre dans l'état de partie.

    - `monstres_combat[i].image_url` : si le monstre est engagé au combat,
      son portrait survit aux rechargements de page (le front le réaffiche
      jusqu'à sa mort) ;
    - `rencontres_images` : journal des monstres croisés en jeu (exploration
      comprise) pour réhydrater la galerie « Monstres rencontrés ».

    Fail-safe : toute erreur est silencieusement ignorée.
    """
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        st = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        etat = st.load()
        if "_erreur" in etat:
            return
        changed = False
        for m in etat.get("monstres_combat") or []:
            if (
                _norm_nom_simple(m.get("nom")) == _norm_nom_simple(nom)
                and m.get("image_url") != url
            ):
                m["image_url"] = url
                changed = True
        if _fusionner_rencontres(etat, [(nom, url)]):
            changed = True
        if changed:
            st.save(etat)
    except Exception:                                            # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def monstre_consulter(ctx: ToolContext, nom: str) -> ToolResult:
    """
    Renvoie la fiche complète (statistiques D&D 3.5) + l'URL d'une image d'un
    monstre. Cherche dans le bestiaire local (`data/bestiaire.json`) en
    acceptant les noms français OU anglais (alias : « Ghoul » → « Goule »).
    Si le monstre n'est pas trouvé localement, retourne un message invitant à
    interroger la base de connaissances RAG « D&D 3.5 — Manuels » et
    génère quand même un placeholder SVG. L'image est régénérée
    automatiquement si la fiche du bestiaire a été enrichie depuis.

    :param nom (str): nom du monstre, FR ou EN (ex. "Gobelin"/"Goblin",
        "Goule"/"Ghoul", "Dragon rouge jeune").
    """
    m = _find_monstre(ctx, nom)
    # PLACEHOLDER (« Mort-vivant de taille M »…) : refus SANS EXCEPTION, avec
    # orientation scénario/bestiaire réel — ni stats affichées, ni image
    # générique inadaptée. Avec le refus à l'engagement, plus aucun
    # placeholder ne doit arriver sur le plateau ; s'il y en a un (vieux
    # état), la consultation reste bloquée pour forcer la correction.
    # SAUF si le scénario actif référence ce gabarit (rencontre légitime).
    if (
        m is not None
        and _est_monstre_generique(m)
        and not _generique_autorise_scenario(ctx, nom)
    ):
        return ToolResult(text=_refus_generique_texte(ctx, nom, m))
    # Nom canonique du bestiaire : la clé de cache en dérive - sinon « Golem »
    # et « Golem de chair » (même créature) généreraient des PNG distincts.
    nom_canonique = str((m or {}).get("nom") or nom) if m is not None else nom
    if m is None:
        # Nom non résolu dans le bestiaire → PAS de génération rétro :
        # le RAG renverrait un portrait d'un AUTRE monstre (goule montrée
        # en golem…). On purge l'éventuelle image erronée du cache ; le bloc
        # « img_path is None » ci-dessous servira alors un placeholder clair.
        stale = _find_image(ctx, nom)
        if stale is not None:
            try:
                os.remove(stale)
            except OSError:
                pass
    # 🖼️ Images de monstres : réservées aux phases de COMBAT. Hors combat,
    # une consultation de stats ne génère rien, n'affiche rien et ne
    # journalise rien (consigne de table : pas d'illustration tant que le
    # monstre n'est pas réellement rencontré/engagé).
    en_combat = _phase_partie(ctx) == "combat"
    url: Optional[str] = None
    src = ""
    if en_combat:
        # Image : priorité PNG en cache à jour, sinon génération ComfyUI,
        # sinon placeholder SVG. « À jour » = le hash de la description
        # stocké dans <slug>.meta.json correspond à la description courante
        # — un vieux PNG généré sans description (monstre inventé par l'IA)
        # est régénéré dès qu'une vraie fiche est disponible. Le cache est
        # indexé sur le nom de TYPE canonique (`_cache_key(nom_canonique)`)
        # pour que les homonymes ET les alias (« Golem » → « Golem de
        # chair ») partagent une image.
        slug = _cache_key(nom_canonique)
        cache_dir = _cache_dir(ctx)
        dest = os.path.join(cache_dir, f"{slug}.png")
        meta_path = _meta_path_for(ctx, slug)

        img_path = _find_image(ctx, nom_canonique)
        src = "locale"
        description = ""
        if img_path is not None:
            meta_hash = _read_desc_hash(meta_path)
            # 1er test rapide avec la seule description locale (évite un appel
            # RAG systématique quand le bestiaire suffit).
            hash_local = _hash_desc(_desc_locale(m))
            if meta_hash is not None and meta_hash == hash_local and hash_local:
                src = "cache"
            else:
                # Description complète (locale ou portrait RAG du Manuel des
                # Monstres, requête avec le nom canonique FR).
                description = await _description_monstre(m, nom)
                if meta_hash is not None and _hash_desc(description) == meta_hash:
                    src = "cache"
                else:
                    # Image périmée → suppression AVANT régénération, sinon
                    # generer_averti court-circuite sur le fichier existant.
                    try:
                        os.remove(img_path)
                    except OSError:
                        pass
                    img_path = None  # image périmée → régénération
        if img_path is None:
            gen_ok = False
            if m is None:
                # Aucun match bestiaire : pas de génération (RAG = portrait
                # d'un autre monstre) → placeholder SVG direct.
                img_path = _write_placeholder(ctx, nom)
                src = "placeholder"
            else:
                try:
                    if not description:
                        description = await _description_monstre(m, nom)
                    prompt_text = monstre_prompt(nom_canonique, description)
                    r = await generer_averti(ctx, "monstre", prompt_text, dest)
                    if r is not None and os.path.isfile(r):
                        img_path = r
                        src = "comfyui"
                        gen_ok = True
                        _write_desc_hash(
                            meta_path, _hash_desc(description), nom_canonique
                        )
                except Exception as e:
                    # On ne casse pas le tour si ComfyUI échoue — fallback SVG.
                    src = f"comfyui_echec({type(e).__name__})"
                if not gen_ok:
                    if img_path is None:
                        img_path = _find_image(ctx, nom)
                    if img_path is None:
                        img_path = _write_placeholder(ctx, nom)
                        if "comfyui" not in src:
                            src = "placeholder"
        url = _url_for(img_path, ctx.data_dir)
        # Persiste la rencontre (journal + combat éventuel) pour que le
        # portrait survive aux rechargements de page, jusqu'à la mort du
        # monstre.
        _memoriser_rencontre(
            ctx, str((m or {}).get("nom") or nom), url
        )
    if m is None:
        # Suggestions de noms proches dans le bestiaire (fail-safe : jamais
        # bloquant si le bestiaire est illisible).
        try:
            sugg = _suggestions(
                _load_bestiaire(ctx).get("monstres", {}),
                _candidats_noms(nom),
            )
        except Exception:                                        # noqa: BLE001
            sugg = []
        texte = (
            f"❓ Monstre **{nom}** absent du bestiaire local. "
            f"Pour les stats, interrogez la KB « D&D 3.5 — Manuels » "
            f"(RAG activé). "
        )
        if url:
            texte += f"Image ({src}) : {url}"
        if sugg:
            texte += f"\n🔎 Noms proches dans le bestiaire : {', '.join(sugg)}."
        texte += (
            "\n⚔️ Ce monstre engage le groupe ? Appelle "
            "`calculer_initiative` puis `demarrer_combat` AVANT toute "
            "attaque ou action de combat."
        )
        # Pas d'image hors combat : aucun patch, la galerie ne bouge pas.
        patch = {"image_monstre": url} if url else None
        return ToolResult(text=texte, state_patch=patch)
    fiche = _format_fiche(m)
    texte = fiche + (
        f"\n\n🖼️ Image ({src}) : {url}\n" if url else "\n"
    )
    texte += (
        "\n[JSON complet]\n" + json.dumps(m, ensure_ascii=False, indent=2)
        + "\n⚔️ Ce monstre engage le groupe ? Appelle "
          "`calculer_initiative` puis `demarrer_combat` AVANT toute "
          "attaque ou action de combat."
    )
    patch = {"image_monstre": url} if url else None
    return ToolResult(text=texte, state_patch=patch)


@tool
async def monstre_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste tous les monstres pré-remplis du bestiaire local, avec leur FP.
    Aucun argument. Utile pour que le MJ choisisse enemis crédibles sans
    improviser les stats.
    """
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {})
    if not monstres:
        return ToolResult(text="ℹ️ Bestiaire local vide.")
    lignes = []
    # Tri par FP (parsing approximatif : 1/3 < 1 < 2 ...)
    def fp_key(m: dict[str, Any]) -> float:
        fp = str(m.get("fp", "0"))
        try:
            if "/" in fp:
                a, b = fp.split("/")
                return float(a) / float(b)
            return float(fp)
        except (ValueError, ZeroDivisionError):
            return 99.0
    for m in sorted(monstres.values(), key=fp_key):
        lignes.append(f"- **{m.get('nom','?')}** — FP {m.get('fp','?')}")
    return ToolResult(
        text=(
            f"🐉 Bestiaire local ({len(monstres)} monstres, triés par FP) :\n"
            + "\n".join(lignes)
        )
    )


@tool
async def monstre_ajouter_bestiaire(
    ctx: ToolContext,
    nom: str,
    type_monstre: str,
    taille: str,
    dv: str,
    pv: int,
    ca: int,
    vitesse: str,
    bab: str,
    init: str,
    attaques: str,
    degs: str,
    sauvegardes: str,
    carac: str,
    comp: str,
    dons: str,
    capacites: str,
    faiblesses: str,
    fp: str,
    alignement: str,
) -> ToolResult:
    """
    Enrichit le bestiaire local en y ajoutant un monstre custom (pour les
    créatures non couvertes par le Manuel des Monstres 3.5 d'origine). La
    fiche est persistée dans `data/bestiaire.json` et réutilisable à
    l'avenir.

    :param nom (str): nom usuel (ex. "Rois des glaces").
    :param type_monstre (str): ex. "EI (froid)".
    :param taille (str): T/P/M/G/C (cf. MJ 3.5).
    :param dv (str): ex. "4d8+8".
    :param pv (int): points de vie moyens.
    :param ca (int): classe d'armure.
    :param vitesse (str): ex. "9 m (6 cases)".
    :param bab (str): ex. "+4".
    :param init (str): ex. "+1".
    :param attaques (str): description des armes/modes d'attaque.
    :param degs (str): dégâts par attaque.
    :param sauvegardes (str): "Réfl +X, Vig +Y, Vol +Z".
    :param carac (str): "For X, Dex Y, Con Z, Int A, Sag B, Cha C".
    :param comp (str): compétences.
    :param dons (str): dons.
    :param capacites (str): capacités spéciales.
    :param faiblesses (str): faiblesses (— si aucune).
    :param fp (str): facteur de puissance (ex. "1/2", "3").
    :param alignement (str): ex. "Neutre mauvais".
    """
    best_path = _bestiaire_path(ctx)
    try:
        with open(best_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return ToolResult(text=f"❌ Bestiaire illisible : {best_path}")

    cle = _slug(nom)
    fiche = {
        "nom": nom,
        "type": type_monstre,
        "taille": taille,
        "dv": dv,
        "pv": int(pv),
        "ca": int(ca),
        "vitesse": vitesse,
        "bab": bab,
        "init": init,
        "attaques": attaques,
        "degs": degs,
        "sauvegardes": sauvegardes,
        "carac": carac,
        "comp": comp,
        "dons": dons,
        "capacites": capacites,
        "faiblesses": faiblesses,
        "fp": fp,
        "alignement": alignement,
        "cle": cle,
        "prompt_image": f"fantasy {_type_en(type_monstre) or type_monstre.lower()} creature, "
                        f"D&D style illustration, ink style, dramatic lighting",
    }
    raw[cle] = fiche
    # Met à jour le meta nb_monstres
    if "_meta" in raw and isinstance(raw["_meta"], dict):
        raw["_meta"]["nb_monstres"] = sum(1 for k in raw if k != "_meta" and isinstance(raw[k], dict) and "nom" in raw[k])

    try:
        with open(best_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return ToolResult(text=f"❌ Erreur écriture bestiaire : {e}")
    # Invalide le cache
    global _BESTIAIRE_CACHE
    _BESTIAIRE_CACHE = None
    return ToolResult(
        text=f"✅ Monstre **{nom}** ajouté au bestiaire (clé `{cle}`, FP {fp}).",
    )
