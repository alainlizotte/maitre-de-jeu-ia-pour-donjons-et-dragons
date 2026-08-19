// Client REST minimal pour /api/* (parties, health, tools, rag).
// Pas d'auth — app locale mono-utilisateur. fetch relatif (proxy Vite ou même origine).

import type { HealthStatus, PartiesList, PartyState } from "./types";

const API = "/api";

async function jq<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return (await resp.json()) as T;
}

export const api = {
  // -- Health ------------------------------------------------------------- //
  health: () => fetch(`${API}/health`).then(jq<HealthStatus>),

  // -- Parties ------------------------------------------------------------ //
  // /api/parties renvoie { active:[ids], persisted:[ids] } — IDs seuls.
  // Le détail (titre/phase/tour) se charge à la demande via getParty().
  listParties: () => fetch(`${API}/parties`).then(jq<PartiesList>),
  createParty: (titre: string) =>
    fetch(`${API}/parties`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ titre }),
    }).then(jq<{ partie_id: string }>),
  getParty: (id: string) => fetch(`${API}/parties/${id}`).then(jq<PartyState | { _erreur: string }>),

  // -- Tools (introspection / docs) -------------------------------------- //
  listTools: () =>
    fetch(`${API}/tools`).then(jq<{ names: string[]; schemas: Record<string, unknown> }>),

  // -- RAG (admin) -------------------------------------------------------- //
  ragStats: () => fetch(`${API}/rag/stats`).then(jq<{ enabled: boolean; collections: Record<string, number> }>),
  ragIngest: (force = false) =>
    fetch(`${API}/rag/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    }).then(jq<{ ingested: number; skipped: number; errors: number }> ),
};
