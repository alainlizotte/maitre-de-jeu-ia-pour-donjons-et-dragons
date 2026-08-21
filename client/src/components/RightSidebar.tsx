import { useState } from "react";
import { DiceRoller } from "./DiceRoller";
import { DungeonView } from "./DungeonView";
import { WorldMap } from "./WorldMap";
import { Bestiary } from "./Bestiary";
import { TeamChat } from "./TeamChat";
import { useParty } from "../store";

type Tab = "des" | "equipe" | "monde" | "donjon" | "bestiaire";

const TAB_LABELS: Record<Tab, string> = {
  des: "Dés",
  equipe: "Équipe",
  monde: "Monde",
  donjon: "Donjon",
  bestiaire: "Bestiaire",
};

interface RightSidebarProps {
  sendTeamSay?: (text: string) => void;
}

/** Moitié basse de la colonne : dernière image de monstre rencontrée + historique. */
function EncounterGallery() {
  const monsters = useParty((s) => s.monsters);
  const [selected, setSelected] = useState(0);
  // L'index pointe la vignette affichée en grand (les plus récentes en tête).
  const idx = Math.min(selected, Math.max(0, monsters.length - 1));
  const current = monsters[idx];

  return (
    <div className="h-1/2 min-h-0 border-t border-stone-800 bg-stone-900/70 flex flex-col p-2">
      <h3 className="text-xs uppercase text-stone-500 mb-1.5 shrink-0">
        Monstres rencontrés {monsters.length > 0 && <span className="text-amber-400">({monsters.length})</span>}
      </h3>
      {!current && (
        <div className="flex-1 flex items-center justify-center text-center text-stone-600 text-xs italic px-4">
          Les images des monstres croisés en jeu s'afficheront ici.
        </div>
      )}
      {current && (
        <>
          <div className="text-center text-stone-200 text-sm font-medium mb-1 shrink-0 truncate" title={current.nom}>
            {current.nom}
          </div>
          <div className="flex-1 min-h-0 rounded border border-stone-700 bg-stone-950/60 overflow-hidden flex items-center justify-center">
            <img
              src={current.url}
              alt={current.nom}
              className="max-w-full max-h-full object-contain"
              onError={(e) => {
                // PNG manquant → placeholder SVG du même slug.
                const el = e.target as HTMLImageElement;
                if (!el.src.endsWith(".svg")) {
                  el.src = current.url.replace(/\.(png|jpg|jpeg|webp)$/i, ".svg");
                }
              }}
            />
          </div>
          {monsters.length > 1 && (
            <div className="flex gap-1.5 mt-1.5 overflow-x-auto shrink-0">
              {monsters.map((m, i) => (
                <button
                  key={m.url}
                  onClick={() => setSelected(i)}
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
    </div>
  );
}

export function RightSidebar({ sendTeamSay }: RightSidebarProps) {
  const [tab, setTab] = useState<Tab>("des");
  const teamUnread = useParty((s) => s.teamUnread);
  const resetTeamUnread = useParty((s) => s.resetTeamUnread);

  const handleTabChange = (t: Tab) => {
    setTab(t);
    if (t === "equipe") resetTeamUnread();
  };

  return (
    <aside className="w-80 shrink-0 border-l border-stone-800 bg-stone-900/50 flex flex-col">
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
        {tab === "des" && <DiceRoller />}
        {tab === "equipe" && <TeamChat sendTeamSay={sendTeamSay ?? (() => {})} />}
        {tab === "monde" && <WorldMap />}
        {tab === "donjon" && <DungeonView />}
        {tab === "bestiaire" && <Bestiary />}
      </div>
      <EncounterGallery />
    </aside>
  );
}
