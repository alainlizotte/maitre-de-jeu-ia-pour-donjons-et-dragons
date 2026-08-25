// Barre de ressources — liens permanents (manuels, cartes, scénarios PDF)
// affichée en bas de l'écran de jeu, toujours visibles pendant la partie.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/rest";

/** Libellés courts pour les catégories de manuels. */
const CAT_ORDER = [
  "Manuels de base",
  "Références",
  "Codex",
  "Bestiaires",
  "Mondes",
];

export function RessourcesBar({ partie_id }: { partie_id?: string }) {
  const [scenariosOuverts, setScenariosOuverts] = useState(false);
  const [manuelsOuverts, setManuelsOuverts] = useState(false);
  const { data } = useQuery({
    queryKey: ["ressources", partie_id ?? null],
    queryFn: () => api.ressources(partie_id),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  // Regroupe les manuels par catégorie.
  const manuelsParCat = useMemo(() => {
    if (!data?.manuels) return [];
    const map = new Map<string, typeof data.manuels>();
    for (const m of data.manuels) {
      const cat = m.categorie || "Autre";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(m);
    }
    // Ordre souhaité, puis le reste.
    const ordered: { cat: string; items: typeof data.manuels }[] = [];
    for (const c of CAT_ORDER) {
      const items = map.get(c);
      if (items) ordered.push({ cat: c, items });
    }
    for (const [cat, items] of map) {
      if (!CAT_ORDER.includes(cat)) ordered.push({ cat, items });
    }
    return ordered;
  }, [data?.manuels]);

  if (!data) return null;

  return (
    <footer className="relative shrink-0 border-t border-stone-800 bg-stone-950/80 px-3 py-1.5 flex items-center gap-x-3 gap-y-1 flex-wrap text-xs">
      <span className="text-stone-500 uppercase tracking-wide text-[10px]">Ressources</span>

      {/* Manuels — dropdown par catégorie */}
      <div className="relative">
        <button
          onClick={() => setManuelsOuverts((v) => !v)}
          className="text-amber-300/90 hover:text-amber-200 underline decoration-amber-700 underline-offset-2"
        >
          📕 Manuels ({data.manuels.length})
          {manuelsOuverts ? " ▴" : " ▾"}
        </button>
        {manuelsOuverts && (
          <div className="absolute bottom-full mb-2 left-0 z-50 w-[420px] max-h-80 overflow-auto bg-stone-900 border border-stone-700 rounded-lg shadow-xl p-2">
            <div className="text-stone-500 text-[10px] uppercase mb-2">
              Manuels D&D 3.5 — clic pour consulter
            </div>
            {manuelsParCat.map(({ cat, items }) => (
              <div key={cat} className="mb-2">
                <div className="text-amber-500 text-[10px] font-semibold uppercase tracking-wide mb-0.5 px-1">
                  {cat}
                </div>
                {items.map((m) => (
                  <a
                    key={m.url}
                    href={m.url}
                    target="_blank"
                    rel="noreferrer"
                    title={m.description}
                    className="block px-2 py-1 rounded hover:bg-stone-800 text-stone-200"
                  >
                    📕 {m.titre}
                  </a>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Cartes — Faerûn, nord de Faerûn, Outreterre, Toril */}
      {data.cartes.map((c) => (
        <a
          key={c.url}
          href={c.url}
          target="_blank"
          rel="noreferrer"
          title={c.titre}
          className="text-sky-300/90 hover:text-sky-200 underline decoration-sky-800 underline-offset-2"
        >
          🗺️ {c.libelle ?? "Carte"}
        </a>
      ))}
      {data.donjon && (
        <a
          href={data.donjon}
          target="_blank"
          rel="noreferrer"
          title="Carte du donjon (SVG)"
          className="text-sky-300 hover:text-sky-200 font-medium underline decoration-sky-800 underline-offset-2"
        >
          🧭 Donjon
        </a>
      )}

      {/* Scénarios — popover */}
      <div className="relative">
        <button
          onClick={() => setScenariosOuverts((v) => !v)}
          className="text-emerald-300/90 hover:text-emerald-200 underline decoration-emerald-800 underline-offset-2"
        >
          📜 Scénarios ({data.scenarios.length})
          {scenariosOuverts ? " ▴" : " ▾"}
        </button>
        {scenariosOuverts && (
          <div className="absolute bottom-full mb-2 left-0 z-40 w-80 max-h-72 overflow-auto bg-stone-900 border border-stone-700 rounded-lg shadow-xl p-2">
            <div className="text-stone-500 text-[10px] uppercase mb-1">
              Scénarios PDF — clic pour consulter
            </div>
            {data.scenarios.map((s) => (
              <a
                key={s.id}
                href={s.url}
                target="_blank"
                rel="noreferrer"
                className="block px-2 py-1.5 rounded hover:bg-stone-800 text-stone-200"
                title={`Niveaux : ${s.niveau}`}
              >
                <span className="text-emerald-300 font-mono text-[10px] mr-1.5">
                  [{s.id}]
                </span>
                {s.titre}
                <span className="block text-[10px] text-stone-500">
                  Niveaux : {s.niveau}
                </span>
              </a>
            ))}
          </div>
        )}
      </div>
    </footer>
  );
}
