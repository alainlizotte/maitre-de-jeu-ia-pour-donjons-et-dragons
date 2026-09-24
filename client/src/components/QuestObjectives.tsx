// Onglet « Objectifs de quête » du panneau latéral : suivi mécanique de la
// trame (server/game/objectifs.py). Chaque objectif du manifeste du donjon
// expose son statut (accompli / en cours / bloqué / à venir) et ses objets
// REQUIS : les « Objets de quête » (portee="quete") de l'inventaire de LA
// PARTIE courante qui débloquent la suite du scénario.
//
// ⛔ GATING : tant qu'un objectif est « bloqué » (objet requis manquant),
//  carte_donjon_explorer / voyage_demarrer refusent d'avancer (sauf
//  `forcer=true` du choix explicite de la table).

import { useParty } from "../store";
import type { QuestObjective, QuestRequis } from "../api/types";

const TYPE_LABELS: Record<string, string> = {
  objet: "objet requis",
  lieu: "lieu à atteindre",
  pnj: "rencontre",
  enigme: "énigme",
  combat: "épreuve",
  etape: "étape",
};

const STATUT_STYLE: Record<string, { carre: string; badge: string }> = {
  complet: { carre: "bg-emerald-500", badge: "bg-emerald-900/60 text-emerald-300" },
  en_cours: { carre: "bg-sky-500", badge: "bg-sky-900/60 text-sky-300" },
  bloque: { carre: "bg-red-500", badge: "bg-red-900/60 text-red-300" },
  a_venir: { carre: "bg-stone-600", badge: "bg-stone-800 text-stone-400" },
};

const STATUT_LABEL: Record<string, string> = {
  complet: "accompli",
  en_cours: "en cours",
  bloque: "bloqué — objets requis manquants",
  a_venir: "à venir",
};

function RequisChip({ requis }: { requis: QuestRequis }) {
  return (
    <span
      className={
        "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border " +
        (requis.present
          ? "border-emerald-700/60 bg-emerald-950/40 text-emerald-300"
          : "border-red-800/70 bg-red-950/30 text-red-300")
      }
      title={
        requis.present
          ? `Possédé par ${requis.porteur ?? "un PJ"} (inventaire de quête de cette partie)`
          : "Manquant à l'inventaire de quête de la partie — la suite du scénario est verrouillée"
      }
    >
      {requis.present ? "✅" : "❌"} {requis.nom}
      {requis.present && requis.porteur && (
        <span className="text-stone-400 font-normal">({requis.porteur})</span>
      )}
    </span>
  );
}

function ObjectifCard({ obj }: { obj: QuestObjective }) {
  const statut = STATUT_STYLE[obj.statut] ?? STATUT_STYLE.a_venir;
  const bloque = obj.statut === "bloque";
  return (
    <div
      className={
        "rounded border p-2 " +
        (bloque
          ? "border-red-800/70 bg-red-950/20"
          : obj.statut === "en_cours"
            ? "border-sky-800/70 bg-sky-950/20"
            : "border-stone-800 bg-stone-950/40")
      }
    >
      <div className="flex items-start gap-2">
        <span className={`mt-1 shrink-0 w-2.5 h-2.5 rounded-full ${statut.carre}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span
              className={"px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide " + statut.badge}
            >
              {STATUT_LABEL[obj.statut] ?? obj.statut}
            </span>
            {obj.type && (
              <span className="text-[10px] italic text-stone-500">
                {TYPE_LABELS[obj.type] ?? obj.type}
              </span>
            )}
            {typeof obj.xp === "number" && obj.xp > 0 && (
              <span
                className="text-[10px] text-amber-500/80"
                title="Récompense d'histoire DMG 3.5 INDICATIVE — à accorder par le MJ via fiche_perso_gagner_xp, jamais attribuée automatiquement."
              >
                ✨ XP histoire : {obj.xp}
              </span>
            )}
          </div>
          <div className="text-sm text-stone-100 font-medium mt-1 leading-snug">
            {obj.titre || "Objectif"}
          </div>
          {obj.detail && (
            <div className="text-xs text-stone-400 mt-0.5 leading-snug">{obj.detail}</div>
          )}
          {obj.requis && obj.requis.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {obj.requis.map((r) => (
                <RequisChip key={r.nom} requis={r} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function QuestObjectives() {
  const bible = useParty((s) => s.state?.quete?.bible);
  const objectifs = bible?.objectifs ?? [];
  const manquants = bible?.manquants ?? [];
  const objetsQuete = bible?.objets_quete ?? [];
  const progression = bible?.progression_objectifs ?? "";
  const courant = bible?.objectif_courant ?? "";

  if (objectifs.length === 0) {
    return (
      <div className="text-xs text-stone-500 italic leading-relaxed">
        Aucun objectif de quête actif : la trame courante ne déclare pas
        d'étapes structurées (objets requis, lieux ou étapes à accomplir).
        Dès qu'un scénario les déclare, son suivi s'affiche ici.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {progression && (
        <div className="text-[11px] text-stone-400 flex items-center justify-between">
          <span>
            Progression :{" "}
            <span className="text-amber-300 font-medium">{progression}</span>
          </span>
          {courant && (
            <span className="truncate ml-2">
              Objectif courant :{" "}
              <span className="text-stone-200">{courant}</span>
            </span>
          )}
        </div>
      )}

      {objectifs.map((o) => (
        <ObjectifCard key={o.cle} obj={o} />
      ))}

      {manquants.length > 0 && (
        <div className="rounded border border-red-800/70 bg-red-950/20 p-2 text-xs text-red-300 leading-relaxed">
          <span className="font-medium">
            ⛔ Objets REQUIS manquants à l'inventaire de quête
          </span>
          <div className="mt-1 text-red-200/90">
            {manquants.join("  •  ")}
          </div>
          <div className="mt-1 text-red-300/70 italic">
            La mécanique a VERROUILLÉ la suite du scénario : déplacement et
            voyage vers la zone suivante restent refusés tant que ces objets
            ne sont pas enregistrés ({" "}
            <span className="whitespace-nowrap">inventaire_ajouter portee="quete"</span>
            ) dans la partie en cours.
          </div>
        </div>
      )}

      {objetsQuete.length > 0 && (
        <div className="rounded border border-stone-800 bg-stone-950/40 p-2">
          <div className="text-[10px] uppercase tracking-wide text-stone-500 mb-1">
            📜 Objets de quête possédés par cette partie
          </div>
          <div className="flex flex-wrap gap-1">
            {objetsQuete.map((o) => (
              <span
                key={o.nom}
                className="px-1.5 py-0.5 rounded border border-emerald-800/50 bg-emerald-950/30 text-[10px] text-emerald-300"
                title={`Porté par ${o.porteur}`}
              >
                {o.nom}{" "}
                <span className="text-emerald-500/70 font-normal">({o.porteur})</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}