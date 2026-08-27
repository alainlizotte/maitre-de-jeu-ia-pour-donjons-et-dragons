// Détecte les écrans étroits (smartphones). En dessous de 768 px (breakpoint
// Tailwind md), PartyPage bascule en vue mobile : un seul panneau à la fois,
// navigation par onglets en bas + balayage horizontal. Au-dessus, la grille
// 3 colonnes « desktop » est conservée telle quelle.

import { useEffect, useState } from "react";

const QUERY = "(max-width: 767px)";

export function useIsMobile() {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    setIsMobile(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
