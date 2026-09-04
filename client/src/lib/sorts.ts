// Magie D&D 3.5 côté client — miroir de server/sorts.py.
// Tables d'emplacements par jour (slots) + bonus de caractéristique
// d'incantation, niveaux castables et budgets de sorts connus.

import type { ModelePerso, SortModele, SortsEtat } from "../api/types";

type Tables = Pick<
  ModelePerso,
  | "sorts"
  | "sorts_emplacements"
  | "sorts_connus_max"
  | "sorts_carac"
  | "sorts_prepare"
>;

export const modCarac = (v: number) => Math.floor((v - 10) / 2);

/** True si la classe apparaît dans les tables d'emplacements (lanceur). */
export function estLanceur(tables: Tables | undefined, classe: string): boolean {
  return Boolean((tables?.sorts_emplacements ?? {})[classe]?.length);
}

/** « préparé » (mémorisation quotidienne) ou « spontané » (connus à vie). */
export function typeLancement(tables: Tables | undefined, classe: string): string {
  return tables?.sorts_prepare?.includes(classe) ? "préparé" : "spontané";
}

/** Niveau de sort le plus haut castable par cette classe à ce niveau (-1 : aucun). */
export function niveauSortMax(
  tables: Tables | undefined,
  classe: string,
  niveau: number,
): number {
  const base = (tables?.sorts_emplacements ?? {})[classe]?.[(niveau || 1) - 1] ?? [];
  for (let i = base.length - 1; i >= 0; i--) {
    if ((base[i] ?? 0) > 0) return i;
  }
  return -1;
}

/** Emplacements de sorts par jour, bonus de caractéristique inclus
 *  (+1 par niveau L si mod >= 2L-1 ; +1/niveau castable pour le clerc :
 *  emplacement de domaine) — miroir de sorts.emplacements(). */
export function emplacements(
  tables: Tables | undefined,
  classe: string,
  niveau: number,
  mod: number,
): Record<number, number> {
  const base = (tables?.sorts_emplacements ?? {})[classe]?.[(niveau || 1) - 1] ?? [];
  const total: Record<number, number> = {};
  base.forEach((v, i) => {
    total[i] = v;
  });
  const nls = niveauSortMax(tables, classe, niveau);
  for (let lvl = 1; lvl <= nls; lvl++) {
    if (mod >= 2 * lvl - 1) total[lvl] = (total[lvl] ?? 0) + 1;
  }
  if (classe === "Clerc") {
    for (let lvl = 0; lvl <= nls; lvl++) total[lvl] = (total[lvl] ?? 0) + 1;
  }
  const res: Record<number, number> = {};
  for (const [k, v] of Object.entries(total)) {
    if (v > 0) res[Number(k)] = v;
  }
  return res;
}

/** Budget de sorts distincts connus (spontanés uniquement). */
export function sortsConnusMax(
  tables: Tables | undefined,
  classe: string,
  niveau: number,
): Record<number, number> {
  const ligne = (tables?.sorts_connus_max ?? {})[classe]?.[(niveau || 1) - 1] ?? [];
  const res: Record<number, number> = {};
  ligne.forEach((v, i) => {
    if (v > 0) res[i] = v;
  });
  return res;
}

/** Liste des sorts de la classe (triée), filtrée au niveau de sort max. */
export function sortsDisponibles(
  tables: Tables | undefined,
  classe: string,
  nlsMax: number,
): SortModele[] {
  const res = (tables?.sorts ?? []).filter((s) => s.classes.includes(classe));
  res.sort((a, b) => a.niveau - b.niveau || a.nom.localeCompare(b.nom));
  if (nlsMax >= 0) return res.filter((s) => s.niveau <= nlsMax);
  return res;
}

/** Détail de magie d'une fiche : slots, restants par niveau, sorts prêts. */
export function sortsEtat(
  tables: Tables | undefined,
  fiche: {
    classe: string;
    niveau: number;
    carac: Record<string, number>;
    sorts?: { connus?: string[]; prepares?: Record<string, number>; depenses?: Record<string, number> };
  },
): SortsEtat | null {
  if (!estLanceur(tables, fiche.classe)) return null;
  const cle = tables?.sorts_carac?.[fiche.classe] ?? "INT";
  const mod = modCarac(fiche.carac?.[cle] ?? 10);
  const slots = emplacements(tables, fiche.classe, fiche.niveau, mod);
  const connus = fiche.sorts?.connus ?? [];
  const prepares = fiche.sorts?.prepares ?? {};
  const depenses = fiche.sorts?.depenses ?? {};
  const restants: Record<number, number> = {};
  for (const [k, total] of Object.entries(slots)) {
    restants[Number(k)] = total - (depenses[String(k)] ?? 0);
  }
  return {
    connus,
    prepares,
    depenses,
    slots,
    restants,
  };
}
