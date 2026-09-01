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

/** Moitié basse de la colonne : galerie à onglets — monstres rencontrés et
 *  scènes illustrées (salles de donjon + moments clés générés par le MJ).
 *  L'onglet Scènes s'active tout seul quand une nouvelle image arrive. */
function EncounterGallery() {
  const monsters = useParty((s) => s.monsters);
  const scenes = useParty((s) => s.scenes);
  const [onglet, setOnglet] = useState<"monstres" | "scenes">("monstres");
  const [selectedM, setSelectedM] = useState(0);
  const [selectedS, setSelectedS] = useState(0);
  // Repli de la galerie → toute la hauteur pour les onglets du haut.
  const [replie, setReplie] = useState(false);
  // Fiche détaillée ouverte (popup) — monstre cliqué dans la galerie.
  const [sheet, setSheet] = useState<EncounterMonster | null>(null);
  // Agrandissement plein écran d'une scène.
  const [zoom, setZoom] = useState<EncounterMonster | null>(null);

  // Toggle « génération des scènes » (persisté côté serveur). Monstres,
  // portraits et illustrations de donjon ne sont pas affectés.
  const queryClient = useQueryClient();
  const { data: imageSettings } = useQuery({
    queryKey: ["imageSettings"],
    queryFn: () => api.imageSettings(),
    staleTime: 60_000,
  });
  const toggleScenes = useMutation({
    mutationFn: (v: boolean) => api.setImageScenes(v),
    onSuccess: (s) => queryClient.setQueryData(["imageSettings"], s),
  });
  const scenesOn = imageSettings?.scenes_enabled ?? true;
  // Verrou dur config.yaml : à false, ni l'onglet « Scènes » ni le bouton
  // de toggle ne sont rendus — seule la galerie Monstres reste.
  const scenesAvailable = imageSettings?.scenes_config_enabled ?? true;
  const isMonstres = !scenesAvailable || onglet === "monstres";

  // Une nouvelle scène arrive → on bascule dessus automatiquement
  // (seulement si l'onglet Scènes existe, i.e. config l'autorise).
  const prevScenes = useRef(scenes.length);
  useEffect(() => {
    if (scenesAvailable && scenes.length > prevScenes.current) {
      setOnglet("scenes");
      setSelectedS(0);
      setReplie(false);
    }
    prevScenes.current = scenes.length;
  }, [scenes.length, scenesAvailable]);

  // Fermeture de l'agrandissement au clavier.
  useEffect(() => {
    if (!zoom) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoom(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [zoom]);

  const items = isMonstres ? monsters : scenes;
  const brutIdx = isMonstres ? selectedM : selectedS;
  const idx = Math.min(brutIdx, Math.max(0, items.length - 1));
  const current = items[idx];
  const selectItem = (i: number) =>
    isMonstres ? setSelectedM(i) : setSelectedS(i);

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
          {isMonstres ? (
            <>Monstres rencontrés {monsters.length > 0 && <span className="text-amber-400">({monsters.length})</span>}</>
          ) : (
            <>Scènes {scenes.length > 0 && <span className="text-amber-400">({scenes.length})</span>}</>
          )}
        </h3>
        {scenesAvailable && (
          <>
            <div className="ml-auto flex rounded overflow-hidden border border-stone-700 text-[10px] shrink-0">
              <button
                onClick={() => setOnglet("monstres")}
                className={
                  "px-2 py-1 " +
                  (onglet === "monstres"
                    ? "bg-stone-700 text-amber-300 font-medium"
                    : "bg-stone-900 text-stone-400 hover:text-stone-200")
                }
              >
                Monstres
              </button>
              <button
                onClick={() => setOnglet("scenes")}
                className={
                  "px-2 py-1 relative " +
                  (onglet === "scenes"
                    ? "bg-stone-700 text-amber-300 font-medium"
                    : "bg-stone-900 text-stone-400 hover:text-stone-200")
                }
              >
                Scènes
              </button>
            </div>
            <button
              onClick={() => toggleScenes.mutate(!scenesOn)}
              disabled={toggleScenes.isPending}
              className={
                "shrink-0 w-6 h-6 rounded border text-[11px] leading-none flex items-center justify-center " +
                (scenesOn
                  ? "border-amber-600/60 bg-stone-800 text-amber-300 hover:bg-stone-700"
                  : "border-stone-700 bg-stone-900 text-stone-600 hover:text-stone-400") +
                (toggleScenes.isPending ? " opacity-50 animate-pulse" : "")
              }
              title={
                scenesOn
                  ? "Illustration des scènes : ACTIVÉE — cliquer pour désactiver (les monstres, portraits et donjons restent illustrés)"
                  : "Illustration des scènes : DÉSACTIVÉE — cliquer pour réactiver"
              }
            >
              {scenesOn ? "🖼" : "🚫"}
            </button>
          </>
        )}
      </div>
      {!replie && !current && (
        <div className="flex-1 flex items-center justify-center text-center text-stone-600 text-xs italic px-4">
          {isMonstres
            ? "Les images des monstres croisés en jeu s'afficheront ici."
            : "Les illustrations des salles explorées et des scènes marquantes s'afficheront ici."}
        </div>
      )}
      {!replie && current && (
        <>
          <div className="text-center text-stone-200 text-sm font-medium mb-1 shrink-0 truncate" title={current.nom}>
            {current.nom}
          </div>
          <div className="flex-1 min-h-0 rounded border border-stone-700 bg-stone-950/60 overflow-hidden flex items-center justify-center">
            <img
              src={current.url}
              alt={current.nom}
              title={isMonstres ? `Voir la fiche de ${current.nom}` : "Agrandir"}
              onClick={() => (isMonstres ? setSheet(current) : setZoom(current))}
              className="max-w-full max-h-full object-contain cursor-zoom-in"
              onError={(e) => {
                // PNG manquant → placeholder SVG du même slug.
                const el = e.target as HTMLImageElement;
                if (!el.src.endsWith(".svg")) {
                  el.src = current.url.replace(/\.(png|jpg|jpeg|webp)$/i, ".svg");
                }
              }}
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
