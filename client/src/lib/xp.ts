// Progression XP D&D 3.5 — miroir de server/game/xp.py.
// Table officielle 3-2 : N·(N−1)/2 × 1000 XP cumulés pour atteindre le niveau N.

export function xpMinNiveau(niveau: number): number {
  const n = Math.max(1, Math.floor(niveau || 1));
  return Math.floor((n * (n - 1)) / 2) * 1000;
}

export interface XpProgression {
  /** XP déjà accumulés dans le niveau courant. */
  dans: number;
  /** XP cumulés requis pour atteindre le niveau suivant. */
  prochain: number;
  /** XP cumulés requis pour le niveau suivant uniquement (écart à franchir). */
  requis: number;
  /** Pourcentage de progression vers le niveau suivant (0–100). */
  pct: number;
}

export function xpProgression(xp: number, niveau: number): XpProgression {
  const base = xpMinNiveau(niveau);
  const prochain = xpMinNiveau(niveau + 1);
  const dans = Math.max(0, (Number(xp) || 0) - base);
  const requis = Math.max(1, prochain - base);
  const pct = Math.min(100, Math.max(0, (dans / requis) * 100));
  return { dans, prochain, requis, pct };
}
