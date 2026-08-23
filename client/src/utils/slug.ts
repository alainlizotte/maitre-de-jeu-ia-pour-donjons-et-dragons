// Slug identique au `_slug` serveur (server/tools/fiches.py) : NFKD → ASCII,
// non-alphanumériques → « _ », minuscules, 60 caractères max.

export function slugify(texte: string): string {
  const norm = texte.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const nettoye = norm.trim().replace(/[^A-Za-z0-9_-]+/g, "_");
  return nettoye.slice(0, 60).replace(/^_+|_+$/g, "").toLowerCase() || "perso";
}
