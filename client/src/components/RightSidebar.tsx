import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DiceRoller } from "./DiceRoller";
import { DungeonView } from "./DungeonView";
import { WorldMap } from "./WorldMap";
import { Bestiary, MonsterSheetModal } from "./Bestiary";
import { TeamChat } from "./TeamChat";
import { Journal } from "./Journal";
import { useParty } from "../store";
import { api } from "../api/rest";
import type { EncounterMonster } from "../api/types";

type Tab = "des" | "equipe" | "monde" | "donjon" | "bestiaire" | "journal";

const TAB_LABELS: Record<Tab, string> = {
  des: "Dés",
  equipe: "Équipe",
  monde: "Monde",
  donjon: "Donjon",
  bestiaire: "Bestiaire",
  journal: "Journal",
};

interface RightSidebarProps {
  sendSay?: (text: string) => void;
  sendTeamSay?: (text: string) => void;
  socket?: React.RefObject<{ send: (payload: Record<string, unknown>) => void } | null>;
}

/** Image de la galerie Monstres avec retry anti-cache : si le PNG n'est pas
 *  encore prêt (ComfyUI en cours de génération) au moment où le patch arrive,
 *  on re-tente quelques fois avec cache-busting avant de basculer sur le
 *  placeholder SVG. Même logique que les portraits de personnages. */
function MonsterGalleryImg({
  url,
  nom,
  onClick,
}: {
  url: string;
  nom: string;
  onClick?: () => void;
}) {
  const base = url.replace(/\?.*$/, "");
  const [failed, setFailed] = useState(0); // -1 = placeholder définitif
  const maxRetries = 5;

  useEffect(() => {
    if (failed <= 0 || failed > maxRetries) return;
    const delay = Math.min(2000 * Math.pow(2, failed - 1), 30000);
    const timer = setTimeout(() => setFailed(failed + 1), delay);
    return () => clearTimeout(timer);
  }, [failed, maxRetries]);

  const isSvg = base.endsWith(".svg");
  if (failed > maxRetries) {
    // Échec prolongé → placeholder SVG (si pas déjà) ou image masquée.
    if (isSvg) {
      return (
        <div className="text-xs text-stone-500 px-2 text-center">
          {nom}
        </div>
      );
    }
    const svg = base.replace(/\.(png|jpg|jpeg|webp)$/i, ".svg");
    return (
      <img
        src={`${svg}?t=${Date.now()}`}
        alt={nom}
        onClick={onClick}
        className="max-w-full max-h-full object-contain"
      />
    );
  }
  if (failed > 0) {
    // Re-monte le <img> avec cache-busting pour forcer le re-téléchargement.
    return (
      <img
        key={`${base}-${failed}`}
        src={`${base}?t=${Date.now()}`}
        alt={nom}
        title={nom}
        onClick={onClick}
        className="max-w-full max-h-full object-contain cursor-zoom-in"
        onLoad={() => setFailed(0)}
        onError={() => {
          if (failed === 0) setFailed(1);
        }}
      />
    );
  }
  return (
    <img
      src={url}
      alt={nom}
      title={nom}
      onClick={onClick}
      className="max-w-full max-h-full object-contain cursor-zoom-in"
      onError={() => {
        setFailed(1);
      }}
    />
  );
}

/** Moitié basse de la colonne : galerie à onglets — monstres rencontrés,
 *  pièces de donjon illustrées et scènes marquantes. Trois onglets
 *  séparés ; chaque catégorie peut être coupée individuellement via
 *  config.yaml (verrou dur) et le bouton maître coupe les trois d'un coup. */
function EncounterGallery() {
  const monsters = useParty((s) => s.monsters);
  const salles = useParty((s) => s.salles);
  const scenes = useParty((s) => s.scenes);
  const [onglet, setOnglet] = useState<"monstres" | "salles" | "scenes">(
    "monstres",
  );
  const [selectedM, setSelectedM] = useState(0);
  const [selectedSl, setSelectedSl] = useState(0);
  const [selectedS, setSelectedS] = useState(0);
  // Repli de la galerie → toute la hauteur pour les onglets du haut.
  const [replie, setReplie] = useState(false);
  // Fiche détaillée ouverte (popup) — monstre cliqué dans la galerie.
  const [sheet, setSheet] = useState<EncounterMonster | null>(null);
  // Agrandissement plein écran d'une image (pièce ou scène).
  const [zoom, setZoom] = useState<EncounterMonster | null>(null);

  // Interrupteur MAÎTRE des images (persisté côté serveur) : coupe les
  // trois onglets d'un coup. Les toggles individuels vivent dans
  // config.yaml (image.monstres/salles/scenes_enabled).
  const queryClient = useQueryClient();
  const { data: imageSettings } = useQuery({
    queryKey: ["imageSettings"],
    queryFn: () => api.imageSettings(),
    staleTime: 60_000,
  });
  const toggleMaster = useMutation({
    mutationFn: (v: boolean) => api.setImageMaster(v),
    onSuccess: (s) => queryClient.setQueryData(["imageSettings"], s),
  });
  const masterOn = imageSettings?.all_enabled ?? true;
  const monstresOn = imageSettings?.monstres_enabled ?? true;
  const sallesOn = imageSettings?.salles_enabled ?? true;
  const scenesOn = imageSettings?.scenes_enabled ?? true;
  const unlocked =
    (imageSettings?.monstres_config_enabled ?? true) ||
    (imageSettings?.salles_config_enabled ?? true) ||
    (imageSettings?.scenes_config_enabled ?? true);
  // Onglets effectivement affichés (catégorie activée).
  const ongletsDispo = (
    [
      ["monstres", monstresOn],
      ["salles", sallesOn],
      ["scenes", scenesOn],
    ] as ["monstres" | "salles" | "scenes", boolean][]
  ).filter(([, on]) => on);
  const actif: "monstres" | "salles" | "scenes" =
    ongletsDispo.some(([t]) => t === onglet) && masterOn
      ? onglet
      : (ongletsDispo[0]?.[0] ?? "monstres");
  const isMonstres = masterOn && actif === "monstres";
  const isSalles = masterOn && actif === "salles";

  // Une nouvelle image arrive (ajout OU remise en tête — un même monstre
  // re-rencontré réutilise son URL) → on bascule sur son onglet
  // automatiquement (seulement si l'onglet existe, i.e. config + maître
  // l'autorisent). Priorité : monstres (début de combat) > pièces
  // (déplacement vers une nouvelle pièce) > scènes.
  // Les 5 premières secondes après le montage sont ignorées : c'est la
  // fenêtre de réhydratation depuis l'état serveur (sans quoi un simple
  // F5 déclencherait des bascules fantômes).
  const prevHeads = useRef({
    m: monsters[0]?.url,
    sl: salles[0]?.url,
    s: scenes[0]?.url,
  });
  const mountTime = useRef(Date.now());
  useEffect(() => {
    const heads = {
      m: monsters[0]?.url,
      sl: salles[0]?.url,
      s: scenes[0]?.url,
    };
    const prev = prevHeads.current;
    prevHeads.current = heads;
    if (!masterOn) return;
    if (Date.now() - mountTime.current < 5000) return; // réhydratation
    if (monstresOn && heads.m && heads.m !== prev.m) {
      setOnglet("monstres");
      setSelectedM(0);
      setReplie(false);
    } else if (sallesOn && heads.sl && heads.sl !== prev.sl) {
      setOnglet("salles");
      setSelectedSl(0);
      setReplie(false);
    } else if (scenesOn && heads.s && heads.s !== prev.s) {
      setOnglet("scenes");
      setSelectedS(0);
      setReplie(false);
    }
  }, [monsters, salles, scenes, masterOn, monstresOn, sallesOn, scenesOn]);

  // Fermeture de l'agrandissement au clavier.
  useEffect(() => {
    if (!zoom) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoom(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [zoom]);

  const items = isMonstres ? monsters : isSalles ? salles : scenes;
  const brutIdx = isMonstres ? selectedM : isSalles ? selectedSl : selectedS;
  const idx = Math.min(brutIdx, Math.max(0, items.length - 1));
  const current = items[idx];
  const selectItem = (i: number) =>
    isMonstres ? setSelectedM(i) : isSalles ? setSelectedSl(i) : setSelectedS(i);
  const titreOnglet = isMonstres
    ? `Monstres rencontrés${monsters.length > 0 ? ` (${monsters.length})` : ""}`
    : isSalles
      ? `Pièces explorées${salles.length > 0 ? ` (${salles.length})` : ""}`
      : `Scènes${scenes.length > 0 ? ` (${scenes.length})` : ""}`;
  const videTexte = isMonstres
    ? "Les images des monstres croisés en jeu s'afficheront ici."
    : isSalles
      ? "Les illustrations des pièces de donjon explorées s'afficheront ici."
      : "Les illustrations des scènes marquantes s'afficheront ici.";

  const ongletBtn = (t: "monstres" | "salles" | "scenes", label: string) => (
    <button
      key={t}
      onClick={() => setOnglet(t)}
      className={
        "px-2 py-1 " +
        (actif === t
          ? "bg-stone-700 text-amber-300 font-medium"
          : "bg-stone-900 text-stone-400 hover:text-stone-200")
      }
    >
      {label}
    </button>
  );

  return (
    <div
      className={
        (replie ? "h-auto" : "h-1/2 min-h-0") +
        " border-t border-stone-800 bg-stone-900/70 flex flex-col p-2"
      }
    >
      <div className="flex items-center gap-2 mb-1.5 shrink-0">
        <button
          onClick={() => setReplie((r) => !r)}
          className="text-stone-500 hover:text-amber-300 text-xs w-4"
          title={replie ? "Déplier la galerie" : "Replier la galerie"}
        >
          {replie ? "▸" : "▾"}
        </button>
        <h3 className="text-xs uppercase text-stone-500 truncate">
          {titreOnglet}
        </h3>
        {unlocked && (
          <>
            {masterOn && ongletsDispo.length > 0 && (
              <div className="ml-auto flex rounded overflow-hidden border border-stone-700 text-[10px] shrink-0">
                {ongletsDispo.map(([t]) =>
                  t === "monstres"
                    ? ongletBtn("monstres", "Monstres")
                    : t === "salles"
                      ? ongletBtn("salles", "Pièces")
                      : ongletBtn("scenes", "Scènes"),
                )}
              </div>
            )}
            <button
              onClick={() => toggleMaster.mutate(!masterOn)}
              disabled={toggleMaster.isPending}
              className={
                "shrink-0 w-6 h-6 rounded border text-[11px] leading-none flex items-center justify-center " +
                (masterOn
                  ? "border-amber-600/60 bg-stone-800 text-amber-300 hover:bg-stone-700"
                  : "border-stone-700 bg-stone-900 text-stone-600 hover:text-stone-400") +
                (toggleMaster.isPending ? " opacity-50 animate-pulse" : "")
              }
              title={
                masterOn
                  ? "Images (monstres, pièces, scènes) : ACTIVÉES — cliquer pour tout désactiver (toggles individuels dans config.yaml)"
                  : "Images : DÉSACTIVÉES — cliquer pour tout réactiver"
              }
            >
              {masterOn ? "🖼" : "🚫"}
            </button>
          </>
        )}
      </div>
      {masterOn && !replie && !current && (
        <div className="flex-1 flex items-center justify-center text-center text-stone-600 text-xs italic px-4">
          {ongletsDispo.length > 0
            ? videTexte
            : "Toutes les catégories d'images sont désactivées dans config.yaml."}
        </div>
      )}
      {!masterOn && !replie && (
        <div className="flex-1 flex items-center justify-center text-center text-stone-600 text-xs italic px-4">
          Affichage des images désactivé (bouton 🚫 pour réactiver).
        </div>
      )}
      {masterOn && !replie && current && (
        <>
          <div className="text-center text-stone-200 text-sm font-medium mb-1 shrink-0 truncate" title={current.nom}>
            {current.nom}
          </div>
          <div className="flex-1 min-h-0 rounded border border-stone-700 bg-stone-950/60 overflow-hidden flex items-center justify-center">
            <MonsterGalleryImg
              url={current.url}
              nom={current.nom}
              onClick={() => (isMonstres ? setSheet(current) : setZoom(current))}
            />
          </div>
          {items.length > 1 && (
            <div className="flex gap-1.5 mt-1.5 overflow-x-auto shrink-0">
              {items.map((m, i) => (
                <button
                  key={m.url}
                  onClick={() => selectItem(i)}
                  onDoubleClick={() =>
                    isMonstres ? setSheet(m) : setZoom(m)
                  }
                  title={m.nom}
                  className={
                    "shrink-0 w-10 h-10 rounded border overflow-hidden bg-stone-950 " +
                    (i === idx ? "border-amber-400" : "border-stone-700 opacity-60 hover:opacity-100")
                  }
                >
                  <img src={m.url} alt={m.nom} className="w-full h-full object-cover" />
                </button>
              ))}
            </div>
          )}
        </>
      )}
      {sheet && (
        <MonsterSheetModal
          nom={sheet.nom}
          url={sheet.url}
          onClose={() => setSheet(null)}
        />
      )}
      {zoom && (
        <div
          className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6"
          onClick={() => setZoom(null)}
        >
          <div
            className="relative max-w-3xl w-full flex flex-col items-center"
            onClick={(e) => e.stopPropagation()}
          >
            <img
              src={zoom.url}
              alt={zoom.nom}
              className="max-w-full max-h-[80vh] object-contain rounded border border-stone-700 shadow-2xl"
            />
            <div className="text-stone-200 text-sm mt-2 font-serif">{zoom.nom}</div>
            <button
              onClick={() => setZoom(null)}
              className="absolute -top-3 -right-3 w-8 h-8 rounded-full bg-stone-800 border border-stone-600 text-stone-300 hover:text-white"
              title="Fermer"
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function RightSidebar({ sendSay, sendTeamSay, socket }: RightSidebarProps) {
  const [tab, setTab] = useState<Tab>("des");
  const teamUnread = useParty((s) => s.teamUnread);
  const resetTeamUnread = useParty((s) => s.resetTeamUnread);
  const donjonId = useParty((s) => s.state?.donjon?.id);

  const handleTabChange = (t: Tab) => {
    setTab(t);
    if (t === "equipe") resetTeamUnread();
  };

  // Auto-switch : entrée dans un donjon → onglet "Donjon" actif
  //              sortie du donjon → onglet "Monde" actif
  const prevDonjon = useRef(donjonId);
  useEffect(() => {
    const wasNull = prevDonjon.current == null;
    const isNull = donjonId == null;
    if (wasNull && !isNull) {
      // Entrée dans un donjon
      setTab("donjon");
    } else if (!wasNull && isNull) {
      // Sortie du donjon
      setTab("monde");
    }
    prevDonjon.current = donjonId;
  }, [donjonId]);

  return (
    <aside className="w-full md:w-80 h-full shrink-0 min-h-0 border-l-0 md:border-l border-stone-800 bg-stone-900/50 flex flex-col overflow-hidden">
      <div className="flex border-b border-stone-800 text-xs shrink-0">
        {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => handleTabChange(t)}
            className={
              "flex-1 px-1.5 py-2 relative " +
              (tab === t
                ? "bg-stone-800 text-amber-300 font-medium border-b-2 border-amber-400"
                : "text-stone-400 hover:text-stone-200")
            }
          >
            {TAB_LABELS[t]}
            {t === "equipe" && teamUnread > 0 && (
              <span className="absolute top-1 right-0.5 w-4 h-4 bg-red-500 text-white rounded-full text-[9px] flex items-center justify-center font-bold animate-bounce">
                {teamUnread > 9 ? "9+" : teamUnread}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-3">
        {tab === "des" && <DiceRoller sendSay={sendSay} />}
        {tab === "equipe" && <TeamChat sendTeamSay={sendTeamSay ?? (() => {})} socket={socket} />}
        {tab === "monde" && <WorldMap />}
        {tab === "donjon" && <DungeonView sendSay={sendSay} />}
        {tab === "bestiaire" && <Bestiary />}
        {tab === "journal" && <Journal />}
      </div>
      <EncounterGallery />
    </aside>
  );
}
