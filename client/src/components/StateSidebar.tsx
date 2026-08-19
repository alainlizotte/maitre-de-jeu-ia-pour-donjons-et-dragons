// Colonne gauche — état partie condensé : lieu, phase, tour, initiative, PJ.

import { useParty } from "../store";

function PhaseBadge({ phase }: { phase: string }) {
  const colors: Record<string, string> = {
    opening: "bg-sky-800 text-sky-200",
    creation: "bg-violet-800 text-violet-200",
    exploration: "bg-emerald-800 text-emerald-200",
    combat: "bg-rose-800 text-rose-200",
    epilogue: "bg-amber-800 text-amber-200",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${colors[phase] ?? "bg-stone-700"}`}>
      {phase}
    </span>
  );
}

export function StateSidebar() {
  const state = useParty((s) => s.state);
  if (!state) {
    return (
      <aside className="w-72 shrink-0 border-r border-stone-800 bg-stone-900/50 p-3 overflow-auto">
        <p className="text-stone-500 text-sm italic">Chargement de l'état…</p>
      </aside>
    );
  }
  if ("_erreur" in state) {
    return (
      <aside className="w-72 shrink-0 border-r border-stone-800 bg-stone-900/50 p-3 overflow-auto">
        <p className="text-rose-400 text-sm">⚠️ {state._erreur}</p>
      </aside>
    );
  }
  return (
    <aside className="w-72 shrink-0 border-r border-stone-800 bg-stone-900/50 p-3 overflow-auto">
      <div className="mb-3">
        <h2 className="font-serif text-amber-200 text-sm uppercase tracking-wide">
          {state.meta?.titre || "(sans titre)"}
        </h2>
        <div className="flex items-center gap-2 mt-1 text-xs text-stone-400">
          <PhaseBadge phase={state.phase} />
          <span>tour {state.tour}</span>
        </div>
      </div>

      <div className="mb-4">
        <h3 className="text-xs uppercase text-stone-500 mb-1">Lieu</h3>
        <div className="text-stone-100 font-medium">{state.lieu?.nom}</div>
        <div className="text-xs text-stone-400">{state.lieu?.type}</div>
        {state.lieu?.description && (
          <p className="text-xs text-stone-400 mt-1 italic line-clamp-3">
            {state.lieu.description}
          </p>
        )}
      </div>

      {state.initiative && state.initiative.length > 0 && (
        <div className="mb-4">
          <h3 className="text-xs uppercase text-stone-500 mb-1">Initiative</h3>
          <ul className="space-y-0.5 text-sm">
            {state.initiative.map((it, i) => (
              <li
                key={i}
                className={
                  "flex justify-between px-2 py-0.5 rounded " +
                  (state.courant_tour_pour === it.nom ? "bg-amber-900/50 text-amber-100" : "")
                }
              >
                <span>{it.nom}</span>
                <span className="text-stone-400 tabular-nums">{it.total}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h3 className="text-xs uppercase text-stone-500 mb-1">
          PJ ({state.pj?.length ?? 0})
        </h3>
        <ul className="space-y-1">
          {state.pj?.map((p, i) => (
            <li key={i} className="text-sm bg-stone-800/40 rounded p-1.5">
              <div className="flex justify-between">
                <span className="text-stone-100">{p.nom}</span>
                {p.pv !== undefined && p.pv_max !== undefined && (
                  <span className="text-xs tabular-nums text-stone-400">
                    {p.pv}/{p.pv_max}pv · CA {p.ca ?? "?"}
                  </span>
                )}
              </div>
              {(p as { conditions?: string[] }).conditions?.length ? (
                <div className="text-xs text-amber-400 mt-0.5">
                  {(p as { conditions: string[] }).conditions.join(", ")}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </div>

      {state.pnj && state.pnj.length > 0 && (
        <div className="mt-3">
          <h3 className="text-xs uppercase text-stone-500 mb-1">
            PNJ ({state.pnj.length})
          </h3>
          <ul className="space-y-1">
            {state.pnj.map((p, i) => (
              <li key={i} className="text-sm bg-stone-800/40 rounded p-1.5">
                <div className="flex justify-between">
                  <span className="text-stone-300">{p.nom}</span>
                  {p.pv !== undefined && p.pv_max !== undefined && (
                    <span className="text-xs tabular-nums text-stone-400">
                      {p.pv}/{p.pv_max}pv
                    </span>
                  )}
                </div>
                {(p as { conditions?: string[] }).conditions?.length ? (
                  <div className="text-xs text-amber-400 mt-0.5">
                    {(p as { conditions: string[] }).conditions.join(", ")}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
