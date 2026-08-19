import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

interface MonsterEntry {
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

export function Bestiary() {
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["bestiaire"],
    queryFn: async () => {
      const resp = await fetch("/data/bestiaire.json");
      if (!resp.ok) throw new Error("bestiaire.json inaccessible");
      const raw: BestiaireFile = await resp.json();
      const monsters: MonsterEntry[] = Object.entries(raw)
        .filter(([k]) => k !== "_meta" && typeof raw[k] === "object" && "nom" in (raw[k] as Record<string, unknown>))
        .map(([, v]) => v as MonsterEntry)
        .sort((a, b) => parseFp(a.fp) - parseFp(b.fp));
      return monsters;
    },
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
    return (
      <div className="text-sm">
        <button
          onClick={() => setSelected(null)}
          className="text-xs text-amber-400 hover:text-amber-300 mb-2"
        >
          ← Retour à la liste
        </button>
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
          src={`/data/bestiaire_cache/${monster.cle}.png`}
          alt={monster.nom}
          className="w-full max-h-40 object-contain rounded mb-2"
          onError={(e) => {
            (e.target as HTMLImageElement).src = `/data/bestiaire_cache/${monster.cle}.svg`;
            (e.target as HTMLImageElement).onerror = () => {
              (e.target as HTMLImageElement).style.display = "none";
            };
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
