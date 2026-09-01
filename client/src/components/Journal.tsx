// Calepin du MJ — journal de notes persistant par partie.
// Chaque note est un item de liste avec case à cocher (raye la tâche faite),
// éditable en place et supprimable. Persisté via les endpoints /calepin dans
// l'état de la partie (server/game/state.py), donc partagé avec tous les MJ.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/rest";
import { useParty } from "../store";

export function Journal() {
  const partieId = useParty((s) => s.partie_id);
  const queryClient = useQueryClient();
  const [texte, setTexte] = useState("");
  const [editionId, setEditionId] = useState<string | null>(null);
  const [editionTexte, setEditionTexte] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["calepin", partieId ?? null],
    queryFn: () => api.calepinLire(partieId!),
    enabled: !!partieId,
  });

  const add = useMutation({
    mutationFn: (t: string) => api.calepinAjouter(partieId!, t),
    onSuccess: () => {
      setTexte("");
      invalidateCalepin(queryClient, partieId);
    },
  });

  const toggle = useMutation({
    mutationFn: (n: { id: string; fait: boolean }) =>
      api.calepinMaj(partieId!, n.id, { fait: !n.fait }),
    onSuccess: () => invalidateCalepin(queryClient, partieId),
  });

  const upd = useMutation({
    mutationFn: (p: { id: string; texte: string }) =>
      api.calepinMaj(partieId!, p.id, { texte: p.texte }),
    onSuccess: () => {
      setEditionId(null);
      invalidateCalepin(queryClient, partieId);
    },
  });

  const del = useMutation({
    mutationFn: (id: string) => api.calepinSupprimer(partieId!, id),
    onSuccess: () => invalidateCalepin(queryClient, partieId),
  });

  const notes = (data?.notes ?? []).sort(
    (a, b) => Number(a.fait) - Number(b.fait) || a.id.localeCompare(b.id),
  );

  if (isLoading) {
    return <div className="text-stone-400 text-sm py-4 text-center">Chargement du calepin…</div>;
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <p className="text-stone-400 text-xs mb-3 italic shrink-0">
        📓 Notes du joueur — cochez pour rayer les tâches accomplies.
      </p>

      {/* Saisie d'une note */}
      <form
        className="flex gap-2 mb-3 shrink-0"
        onSubmit={(e) => {
          e.preventDefault();
          if (texte.trim()) add.mutate(texte.trim());
        }}
      >
        <input
          value={texte}
          onChange={(e) => setTexte(e.target.value)}
          placeholder="Nouvelle note / tâche…"
          className="flex-1 min-w-0 rounded-md border border-stone-700 bg-stone-800/70 px-2.5 py-1.5 text-sm text-stone-100 placeholder:text-stone-500 focus:outline-none focus:border-amber-600"
        />
        <button
          type="submit"
          disabled={add.isPending || !texte.trim()}
          className="shrink-0 rounded-md bg-amber-700/80 px-3 py-1.5 text-sm text-stone-100 hover:bg-amber-700 disabled:opacity-40"
        >
          +
        </button>
      </form>

      {/* Liste des notes */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1 space-y-1.5">
        {notes.length === 0 && (
          <div className="text-stone-500 text-sm text-center py-6">
            Calepin vide. Ajoutez une note ci-dessus.
          </div>
        )}
        {notes.map((n) => (
          <div
            key={n.id}
            className="flex items-start gap-2 rounded-md border border-stone-700/70 bg-stone-800/40 px-2 py-1.5"
          >
            <button
              onClick={() => toggle.mutate(n)}
              aria-label={n.fait ? "Marquer à faire" : "Marquer faite"}
              className={
                "mt-0.5 w-4.5 h-4.5 shrink-0 w-5 h-5 rounded border flex items-center justify-center text-xs " +
                (n.fait
                  ? "bg-emerald-600/70 border-emerald-500 text-white"
                  : "bg-stone-700/60 border-stone-500 text-transparent hover:text-stone-300")
              }
            >
              ✓
            </button>

            {editionId === n.id ? (
              <form
                className="flex-1 min-w-0 flex gap-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (editionTexte.trim()) upd.mutate({ id: n.id, texte: editionTexte.trim() });
                  else setEditionId(null);
                }}
              >
                <input
                  autoFocus
                  value={editionTexte}
                  onChange={(e) => setEditionTexte(e.target.value)}
                  className="flex-1 min-w-0 rounded border border-stone-600 bg-stone-800/70 px-2 py-0.5 text-sm text-stone-100 focus:outline-none"
                />
                <button type="submit" className="text-emerald-400 text-xs">✓</button>
                <button type="button" onClick={() => setEditionId(null)} className="text-stone-500 text-xs">✕</button>
              </form>
            ) : (
              <>
                <span
                  onDoubleClick={() => {
                    setEditionId(n.id);
                    setEditionTexte(n.texte);
                  }}
                  className={
                    "flex-1 min-w-0 text-sm " +
                    (n.fait ? "text-stone-500 line-through" : "text-stone-100")
                  }
                >
                  {n.texte}
                </span>
                <button
                  onClick={() => {
                    setEditionId(n.id);
                    setEditionTexte(n.texte);
                  }}
                  className="text-stone-500 hover:text-stone-300 text-xs shrink-0"
                  aria-label="Modifier"
                >
                  ✎
                </button>
                <button
                  onClick={() => del.mutate(n.id)}
                  className="text-stone-500 hover:text-red-400 text-xs shrink-0"
                  aria-label="Supprimer"
                >
                  🗑
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function invalidateCalepin(queryClient: ReturnType<typeof useQueryClient>, partieId: string | null) {
  queryClient.invalidateQueries({ queryKey: ["calepin", partieId ?? null] });
  queryClient.invalidateQueries({ queryKey: ["party", partieId ?? null] });
}
