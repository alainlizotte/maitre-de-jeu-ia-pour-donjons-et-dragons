import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

export interface MonsterEntry {
  nom: string;
  type: string;
  taille: string;
  dv: string;
  pv: number;
  ca: number;
  vitesse: string;
  bab: string;
  init: string;
  attaques: string;
  degs: string;
  sauvegardes: string;
  carac: string;
  comp: string;
  dons: string;
  capacites: string;
  faiblesses: string;
  fp: string;
  alignement: string;
  cle: string;
}

interface BestiaireFile {
  _meta?: Record<string, unknown>;
  [key: string]: MonsterEntry | Record<string, unknown> | undefined;
}

function parseFp(fp: string): number {
  if (fp.includes("/")) {
    const [a, b] = fp.split("/");
    return parseFloat(a) / parseFloat(b);
  }
  return parseFloat(fp) || 0;
}

function fpColor(fp: string): string {
  const v = parseFp(fp);
  if (v < 0.5) return "text-stone-400";
  if (v < 1) return "text-emerald-400";
  if (v < 3) return "text-amber-400";
  if (v < 5) return "text-orange-400";
  return "text-rose-400";
}

const TAILLE_LABEL: Record<string, string> = {
  P: "Petit",
  M: "Moyen",
  G: "Grand",
  T: "Très grand",
  C: "Colossal",
};

/** Normalise un nom/slug pour comparaison (casse, accents, séparateurs). */
function normalizeKey(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/** Pont EN→FR : le MJ nomme souvent les monstres en anglais alors que le
 *  bestiaire local est en français (« ghoul » → « goule »). Mêmes alias
 *  que côté serveur (server/tools/monstres.py). */
const ALIAS_EN_FR: Record<string, string> = {
  ghoul: "goule",
  ghast: "goule",
  skeleton: "squelette",
  zombie: "zombie",
  goblin: "gobelin",
  hobgoblin: "hobgobelin",
  bugbear: "gobelours",
  orc: "orque",
  ogre: "ogre",
  ogre_mage: "ogre-mage",
  troll: "troll",
  gnoll: "gnoll",
  minotaur: "minotaure",
  gargoyle: "gargouille",
  basilisk: "basilic",
  chimera: "chimere",
  cockatrice: "cocatrix",
  djinni: "djinn",
  efreeti: "efrit",
  ettin: "ettin",
  griffin: "griffon",
  griffon: "griffon",
  harpy: "harpie",
  hippogriff: "hippogriffe",
  manticore: "manticore",
  medusa: "meduse",
  mimic: "mimique",
  mummy: "momie",
  unicorn: "licorne",
  vampire: "vampire",
  wyvern: "wyverne",
  succubus: "succube",
  pixie: "pixie",
  nymph: "nymphe",
  pegasus: "pegase",
  satyr: "satyre",
  centaur: "centaure",
  werewolf: "loup_garou",
  wolf: "loup",
  dire_wolf: "loup_terrible",
  worg: "worg",
  owlbear: "ours_hibou",
  shadow: "ombre",
  wraith: "spectre",
  spectre: "spectre",
  green_hag: "guenaude_verte",
  sea_hag: "guenaude_marine",
  purple_worm: "ver_pourpre",
  flesh_golem: "golem_de_chair",
  clay_golem: "golem_d_argile",
  iron_golem: "golem_de_fer",
  stone_golem: "golem_de_pierre",
  kraken: "kraken",
  lamia: "lamie",
  rakshasa: "rakshasa",
  tarasque: "tarasque",
  tarrasque: "tarasque",
  hydra: "hydre_5_tetes",
  sahuagin: "sahuagin",
  locathah: "locathah",
  troglodyte: "troglodyte",
  ettercap: "ettercap",
  otyugh: "otyugh",
  remorhaz: "remorhaz",
};

const MOTS_EN_FR: Record<string, string> = {
  red: "rouge", black: "noir", blue: "bleu", green: "vert",
  white: "blanc", brass: "laiton", bronze: "bronze",
  copper: "cuivre", gold: "or", silver: "argent",
  young: "jeune", adult: "adulte", old: "vieux",
  ancient: "ancien", elder: "ancien",
  giant: "geant", dire: "terrible", great: "grand",
  hill: "collines", frost: "givre", fire: "feu",
  cloud: "nuages", stone: "pierres", storm: "tempetes",
  bear: "ours", spider: "araignee", bat: "chauve_souris",
  rat: "rat", scorpion: "scorpion", crocodile: "crocodile",
  octopus: "pieuvre", squid: "calmar", snake: "serpent",
  constrictor: "constricteur", bee: "abeille", wasp: "guepe",
  beetle: "scarabee", ant: "fourmi", eagle: "aigle",
  lion: "lion", tiger: "tigre", hyena: "hyene",
  dragon: "dragon", dragonne: "dragonne",
};

/** Clés candidates pour un nom/slug : lui-même + alias EN→FR + traduction
 *  mot à mot (pour les composés non listés). */
function clesCandidates(s: string): string[] {
  const n = normalizeKey(s);
  const out = [n];
  const alias = ALIAS_EN_FR[n];
  if (alias) out.push(normalizeKey(alias));
  const trad = n
    .split("_")
    .map((w) => MOTS_EN_FR[w] ?? w)
    .join("_");
  if (trad !== n) out.push(trad);
  return [...new Set(out)];
}

async function fetchBestiaire(): Promise<MonsterEntry[]> {
  const resp = await fetch("/data/bestiaire.json");
  if (!resp.ok) throw new Error("bestiaire.json inaccessible");
  const raw: BestiaireFile = await resp.json();
  return Object.entries(raw)
    .filter(([k]) => k !== "_meta" && typeof raw[k] === "object" && "nom" in (raw[k] as Record<string, unknown>))
    .map(([, v]) => v as MonsterEntry)
    .sort((a, b) => parseFp(a.fp) - parseFp(b.fp));
}

/** Retrouve l'entrée du bestiaire correspondant à un monstre rencontré
 * (nom affiché + URL d'image `/data/bestiaire_cache/<slug>.<ext>`).
 * Accepte les noms FR et EN via les alias (ghoul → goule…). */
export function findMonsterEntry(
  monsters: MonsterEntry[],
  nom: string,
  url: string,
): MonsterEntry | undefined {
  const slug = url.match(/\/bestiaire_cache\/([^/.]+)\./)?.[1] ?? "";
  const candsSlug = clesCandidates(slug);
  const candsNom = clesCandidates(nom);
  // 1. correspondance exacte (clé du bestiaire ou nom normalisé)
  const exact = monsters.find(
    (m) =>
      candsSlug.includes(normalizeKey(m.cle)) ||
      candsNom.includes(normalizeKey(m.cle)) ||
      candsNom.includes(normalizeKey(m.nom)),
  );
  if (exact) return exact;
  // 2. inclusion — la clé la plus longue gagne (« rat_geant » > « rat »)
  let best: { m: MonsterEntry; len: number } | undefined;
  const considere = (m: MonsterEntry) => {
    const cle = normalizeKey(m.cle);
    if (!best || cle.length > best.len) best = { m, len: cle.length };
  };
  for (const m of monsters) {
    const cle = normalizeKey(m.cle);
    for (const cs of candsSlug) {
      if (cs && cle.includes(cs)) {
        considere(m);
        break;
      }
    }
    for (const cn of candsNom) {
      if (
        cn &&
        (cle.includes(cn) || normalizeKey(m.nom).includes(cn))
      ) {
        considere(m);
        break;
      }
    }
  }
  if (best) return best.m;
  // 3. sous-ensemble de mots traduits — noms composés réordonnés
  //    (« jeune_rouge_dragon » ⊆ « dragon_rouge_jeune »).
  for (const cn of [...candsNom, ...candsSlug]) {
    const mots = cn.split("_").filter((w) => w.length >= 3 && w !== "monstre");
    if (!mots.length) continue;
    let candidat: { m: MonsterEntry; len: number } | undefined;
    for (const m of monsters) {
      const cle = normalizeKey(m.cle);
      if (mots.every((w) => cle.includes(w))) {
        if (!candidat || cle.length > candidat.len)
          candidat = { m, len: cle.length };
      }
    }
    if (candidat) return candidat.m;
  }
  return undefined;
}

/** Fiche détaillée d'un monstre — utilisée par le panneau Bestiary et le popup. */
export function MonsterFiche({
  monster,
  imageUrl,
  onBack,
}: {
  monster: MonsterEntry;
  imageUrl?: string;
  onBack?: () => void;
}) {
  const src = imageUrl ?? `/data/bestiaire_cache/${monster.cle}.png`;
  return (
    <div className="text-sm">
      {onBack && (
        <button
          onClick={onBack}
          className="text-xs text-amber-400 hover:text-amber-300 mb-2"
        >
          ← Retour à la liste
        </button>
      )}
      <h3 className="font-serif text-amber-200 text-base font-bold mb-1">
        {monster.nom}
      </h3>
      <div className="flex items-center gap-2 mb-2">
        <span className={`text-xs font-mono ${fpColor(monster.fp)}`}>
          FP {monster.fp}
        </span>
        <span className="text-xs text-stone-500">
          {monster.type} — {TAILLE_LABEL[monster.taille] ?? monster.taille}
        </span>
      </div>
      <img
        src={src}
        alt={monster.nom}
        className="w-full max-h-40 object-contain rounded mb-2"
        onError={(e) => {
          // PNG/JPG manquant → placeholder SVG du même slug, sinon masqué.
          const el = e.target as HTMLImageElement;
          if (!el.src.endsWith(".svg")) {
            el.src = src.replace(/\.(png|jpg|jpeg|webp)$/i, ".svg");
          } else {
            el.style.display = "none";
          }
        }}
      />
      <StatLine label="PV" value={`${monster.pv} (${monster.dv})`} />
      <StatLine label="CA" value={String(monster.ca)} />
      <StatLine label="Vitesse" value={monster.vitesse} />
      <StatLine label="Init" value={monster.init} />
      <StatLine label="BAB" value={monster.bab} />
      <StatLine label="Attaques" value={monster.attaques} />
      <StatLine label="Dégâts" value={monster.degs} />
      <StatLine label="Sauvegardes" value={monster.sauvegardes} />
      <StatLine label="Carac." value={monster.carac} />
      <StatLine label="Compétences" value={monster.comp} />
      <StatLine label="Dons" value={monster.dons} />
      <StatLine label="Capacités" value={monster.capacites} />
      <StatLine label="Faiblesses" value={monster.faiblesses} />
      <StatLine label="Align." value={monster.alignement} />
    </div>
  );
}

/** Popup « fiche détaillée » d'un monstre rencontré (galerie colonne droite). */
export function MonsterSheetModal({
  nom,
  url,
  onClose,
}: {
  nom: string;
  url: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["bestiaire"],
    queryFn: fetchBestiaire,
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const entry = findMonsterEntry(data ?? [], nom, url);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="relative bg-stone-900 border border-stone-700 rounded-lg max-w-md w-full max-h-[85vh] overflow-auto p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Fiche de ${nom}`}
      >
        <button
          onClick={onClose}
          aria-label="Fermer"
          className="absolute top-2 right-2 w-7 h-7 rounded-full border border-stone-700 text-stone-400 hover:text-stone-100 hover:border-stone-500 flex items-center justify-center text-sm z-10"
        >
          ✕
        </button>
        {isLoading ? (
          <p className="text-stone-500 text-sm">Chargement de la fiche…</p>
        ) : entry ? (
          <MonsterFiche monster={entry} imageUrl={url} />
        ) : (
          <div className="text-sm">
            <h3 className="font-serif text-amber-200 text-base font-bold mb-1 pr-8">
              {nom}
            </h3>
            <div className="mb-2">
              <span className="text-xs text-stone-500">Monstre rencontré</span>
            </div>
            <img
              src={url}
              alt={nom}
              className="w-full max-h-40 object-contain rounded mb-2"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            <p className="text-xs italic text-stone-500">
              Ce monstre ne figure pas dans le bestiaire local — fiche
              détaillée indisponible.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export function Bestiary() {
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["bestiaire"],
    queryFn: fetchBestiaire,
  });

  if (isLoading) {
    return <p className="text-stone-500 text-sm">Chargement du bestiaire…</p>;
  }

  const monsters = data ?? [];
  const filtered = filter
    ? monsters.filter((m) => {
        const nf = filter.toLowerCase();
        return (
          m.nom.toLowerCase().includes(nf) ||
          m.type.toLowerCase().includes(nf) ||
          m.fp.includes(nf)
        );
      })
    : monsters;

  const monster = monsters.find((m) => m.cle === selected);

  if (monster) {
    return <MonsterFiche monster={monster} onBack={() => setSelected(null)} />;
  }

  return (
    <div className="text-sm">
      <input
        type="text"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filtrer…"
        className="w-full bg-stone-800 border border-stone-700 rounded px-2 py-1 text-xs mb-2 focus:outline-none focus:border-amber-400"
      />
      <div className="text-xs text-stone-500 mb-2">
        {filtered.length} monstre{filtered.length !== 1 ? "s" : ""}
      </div>
      <ul className="space-y-1 max-h-[50vh] overflow-auto">
        {filtered.map((m) => (
          <li key={m.cle}>
            <button
              onClick={() => setSelected(m.cle)}
              className="w-full text-left px-2 py-1.5 rounded hover:bg-stone-800 flex items-center justify-between"
            >
              <span className="text-stone-200 truncate">{m.nom}</span>
              <span className={`text-xs font-mono shrink-0 ml-2 ${fpColor(m.fp)}`}>
                FP {m.fp}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatLine({ label, value }: { label: string; value: string }) {
  if (!value || value === "—" || value === "?") return null;
  return (
    <div className="mb-1">
      <span className="text-stone-500 text-xs">{label} : </span>
      <span className="text-stone-300 text-xs">{value}</span>
    </div>
  );
}
