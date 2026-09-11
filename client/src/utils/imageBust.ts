// Cache-busting STABLE des images runtime (bestiaire, salles, scènes).
//
// Les PNG sont régénérés à la MÊME URL (ComfyUI écrase le fichier) : un
// cache-buster est nécessaire pour rafraîchir l'affichage. Mais un buster
// calculé AU RENDER (`?t=${Date.now()}`) change à chaque re-render — le
// navigateur rechargeait l'image ~2×/seconde (clignotement observé en jeu,
// partie 44b02cfc). Ici : le timestamp est figé par URL, et n'est invalidé
// que sur événement réel (nouvelle image poussée par le serveur).

const busts = new Map<string, number>();

/** Timestamp stable par URL (ne change pas entre les renders). */
export function busteImage(url: string): number {
  let t = busts.get(url);
  if (!t) {
    t = Date.now();
    busts.set(url, t);
  }
  return t;
}

/** Force le rechargement de l'image à son prochain affichage. */
export function invaliderImage(url: string): void {
  if (url) busts.set(url, Date.now());
}
