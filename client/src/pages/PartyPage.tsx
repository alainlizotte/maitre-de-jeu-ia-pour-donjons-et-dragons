// Page de partie — layout 3 colonnes (état/chat/outils) sur grand écran ;
// sur smartphone (< md), un seul panneau à la fois, chat par défaut, avec
// onglets en bas + balayage horizontal pour changer de panneau.
// Charge l'état REST, branche le WS via useChatSocket, synchro state_patches.

import { useEffect, useRef, useState } from "react";
import type { CSSProperties, TouchEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/rest";
import { useParty } from "../store";
import { useChatSocket } from "../hooks/useChatSocket";
import { useIsMobile } from "../hooks/useIsMobile";
import { StateSidebar } from "../components/StateSidebar";
import { ChatPanel } from "../components/ChatPanel";
import { RightSidebar } from "../components/RightSidebar";
import { RessourcesBar } from "../components/RessourcesBar";
import { ScenarioPicker } from "../components/ScenarioPicker";

// Ordre des panneaux en vue mobile (le balayage suit cet ordre, chat au centre).
const MOBILE_VIEWS = ["etat", "chat", "outils"] as const;
type MobileView = (typeof MOBILE_VIEWS)[number];

// Balayages ignorés quand le geste part d'un élément interactif ou d'une
// zone qui gère elle-même le tactile (carte zoomable → .touch-none).
const SWIPE_EXCLUSIONS = "button, a, input, textarea, select, label, .touch-none";

export function PartyPage() {
  const { partie_id } = useParams<{ partie_id: string }>();
  const navigate = useNavigate();
  const setPartieId = useParty((s) => s.setPartieId);
  const setState = useParty((s) => s.setState);
  const state = useParty((s) => s.state);
  const player = useParty((s) => s.player);
  // Signal « l'état serveur a changé » (pj_updated reçu en WS) : on
  // re-fetch immédiatement l'état REST au lieu d'attendre le poll 15 s.
  const stateRev = useParty((s) => s.stateRev);
  const queryClient = useQueryClient();
  const addMonster = useParty((s) => s.addMonster);
  const addScene = useParty((s) => s.addScene);
  const removeMonsterByNom = useParty((s) => s.removeMonsterByNom);

  useEffect(() => {
    if (partie_id) setPartieId(partie_id);
  }, [partie_id, setPartieId]);

  useEffect(() => {
    if (!player) navigate("/");
  }, [player, navigate]);

  const partyQuery = useQuery({
    queryKey: ["party", partie_id],
    queryFn: () => api.getParty(partie_id!),
    enabled: !!partie_id,
    refetchInterval: 15000,
  });

  useEffect(() => {
    if (!stateRev || !partie_id) return;
    queryClient.invalidateQueries({ queryKey: ["party", partie_id] });
  }, [stateRev, partie_id, queryClient]);

  useEffect(() => {
    const etat = partyQuery.data?.etat;
    if (etat && !("_erreur" in etat)) setState(etat);
  }, [partyQuery.data, setState]);

  // ── Réhydratation des galeries (monstres rencontrés + scène courante) ── //
  // Après un rechargement de page ou une reconnexion, les galeries de la
  // colonne droite sont vides (état Zustand en mémoire). On les reconstruit
  // depuis l'état persisté : journal des rencontres + monstres de combat
  // vivants (leur portrait reste affiché jusqu'à leur mort), et illustration
  // de la salle où se trouve le groupe (tant qu'on est dans la pièce).
  const hydratedFor = useRef<string | null>(null);
  useEffect(() => {
    const etat = partyQuery.data?.etat;
    if (!etat || "_erreur" in etat || !partie_id) return;
    if (hydratedFor.current === partie_id) return;
    hydratedFor.current = partie_id;

    // Monstres : journal d'abord (anciens → récents), puis combattants
    // vivants dont l'image ne serait pas encore au journal.
    // ⚔️ Le journal n'est réhydraté qu'en phase de combat : hors combat,
    // aucun portrait de monstre ne doit s'afficher (même d'anciens).
    const urls = new Set<string>();
    if (etat.phase === "combat") {
      for (const r of etat.rencontres_images ?? []) {
        if (r?.url && !urls.has(r.url)) {
          urls.add(r.url);
          addMonster({ nom: r.nom, url: r.url });
        }
      }
    }
    for (const m of etat.monstres_combat ?? []) {
      if (!m.image_url || urls.has(m.image_url)) continue;
      if ((m.conditions ?? []).includes("Détruit")) continue;
      urls.add(m.image_url);
      addMonster({ nom: m.nom, url: m.image_url });
    }
    // Monstres morts : leur portrait ne réapparaît pas après rechargement.
    for (const m of etat.monstres_combat ?? []) {
      if ((m.conditions ?? []).includes("Détruit") && m.nom) {
        removeMonsterByNom(m.nom);
      }
    }

    // Scène courante : l'illustration de la pièce où est le groupe
    // (l'image est en cache disque — re-visiter la salle la réaffiche).
    const donjon = etat.donjon;
    const [cx, cy] = donjon?.courant ?? [];
    if (cx !== undefined && cy !== undefined) {
      const salle = (donjon?.grille ?? []).find(
        (s) =>
          (s as { x?: number } | null)?.x === cx &&
          (s as { y?: number } | null)?.y === cy,
      ) as { image_url?: string; type?: string } | undefined;
      if (salle?.image_url) {
        const label = `${salle.type ?? "salle"} (${cx},${cy})`;
        addScene({
          nom: label.charAt(0).toUpperCase() + label.slice(1),
          url: salle.image_url,
        });
      }
    }
  }, [partyQuery.data, partie_id, addMonster, addScene, removeMonsterByNom]);

  const { sendSay, sendTeamSay, socket } = useChatSocket(partie_id ?? null);

  // set_quest enregistre toujours un objet quête (même à titre vide pour
  // « aventure libre ») ; tant que rien n'est enregistré, le sélecteur reste
  // affiché quelle que soit la phase — aucune limite de temps.
  const quete = state?.quete ?? null;
  const queteChoisie = Boolean(quete && (quete.titre || quete.source));
  const showPicker = Boolean(partie_id) && !queteChoisie;

  // ── Vue mobile : un panneau à la fois ── //
  const isMobile = useIsMobile();
  const [mobileView, setMobileView] = useState<MobileView>("chat");
  const [panelDir, setPanelDir] = useState(0);
  const [ressourcesOuvertes, setRessourcesOuvertes] = useState(false);
  const teamUnread = useParty((s) => s.teamUnread);
  const touch = useRef<{ x: number; y: number } | null>(null);

  const goToMobileView = (v: MobileView) => {
    setPanelDir(MOBILE_VIEWS.indexOf(v) - MOBILE_VIEWS.indexOf(mobileView));
    setMobileView(v);
    setRessourcesOuvertes(false);
  };

  const onTouchStart = (e: TouchEvent) => {
    const t = e.touches[0];
    touch.current =
      t.target instanceof Element && !t.target.closest(SWIPE_EXCLUSIONS)
        ? { x: t.clientX, y: t.clientY }
        : null;
  };

  // Balayage horizontal → panneau voisin (état ← chat → outils).
  const onTouchEnd = (e: TouchEvent) => {
    const start = touch.current;
    touch.current = null;
    if (!start) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    const idx = MOBILE_VIEWS.indexOf(mobileView);
    const next = dx < 0 ? idx + 1 : idx - 1;
    if (next < 0 || next >= MOBILE_VIEWS.length) return;
    setPanelDir(next - idx);
    setMobileView(MOBILE_VIEWS[next]);
  };

  // ── Bannière de quête ── //
  // Desktop : nom + résumé affichés. Smartphone : seul le nom est affiché,
  // un clic sur la bannière déplie/replie le résumé.
  const QuestBanner = () => {
    const q = quete!;
    const [ouvert, setOuvert] = useState(false);
    const clickable = isMobile && Boolean(q.pitch);
    const title = (
      <span className="text-amber-300 font-semibold">📜 {q.titre}</span>
    );
    return (
      <div
        role={clickable ? "button" : undefined}
        tabIndex={clickable ? 0 : undefined}
        onClick={() => clickable && setOuvert((o) => !o)}
        onKeyDown={
          clickable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOuvert((o) => !o);
                }
              }
            : undefined
        }
        className={
          "m-4 mb-2 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 " +
          (clickable ? "select-none cursor-pointer" : "")
        }
      >
        <div className="flex items-center justify-between gap-2">
          {title}
          {clickable && (
            <span className="text-stone-400 text-xs shrink-0">
              {ouvert ? "▴" : "▾"}
            </span>
          )}
        </div>
        {q.pitch && (!isMobile || ouvert) && (
          <div className="text-stone-400 text-sm mt-1">{q.pitch}</div>
        )}
      </div>
    );
  };

  // Colonne centrale, commune aux deux layouts : bannière de quête,
  // sélecteur de scénario (avant le premier choix), puis le chat.
  const colonneCentrale = (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
      {quete?.titre && <QuestBanner />}
      {showPicker && partie_id && (
        <div
          className={
            isMobile
              ? "px-4 pt-4 pb-0 flex-1 min-h-0 flex flex-col overflow-hidden"
              : "p-4 shrink-0"
          }
        >
          <ScenarioPicker partieId={partie_id} />
        </div>
      )}
      {/* Sur smartphone, avant le choix de quête, le sélecteur occupe toute
          la hauteur (le chat est masqué pour ne pas le réduire de moitié). */}
      {!(showPicker && isMobile) && <ChatPanel sendSay={sendSay} />}
    </div>
  );

  if (isMobile) {
    return (
      <div className="relative flex-1 min-h-0 flex flex-col overflow-hidden">
        <div
          key={mobileView}
          style={{ "--panel-dir": panelDir } as CSSProperties}
          className="panel-swap flex-1 min-h-0 flex flex-col overflow-hidden"
          onTouchStart={onTouchStart}
          onTouchEnd={onTouchEnd}
        >
          {mobileView === "etat" && <StateSidebar />}
          {mobileView === "chat" && colonneCentrale}
          {mobileView === "outils" && (
            <RightSidebar sendSay={sendSay} sendTeamSay={sendTeamSay} socket={socket} />
          )}
        </div>

        {/* Fond assombri : un tap referme les ressources. */}
        {ressourcesOuvertes && (
          <div
            className="absolute inset-0 z-30 bg-black/50"
            onClick={() => setRessourcesOuvertes(false)}
          />
        )}

        {/* Nav en bas + volet Ressources juste au-dessus d'elle. Le volet
            reste monté (masqué) pour que la requête soit déjà chaude quand
            on l'ouvre → affichage instantané. */}
        <div className="relative shrink-0 z-40">
          <div
            className={
              ressourcesOuvertes
                ? "absolute bottom-full inset-x-0 shadow-[0_-8px_24px_rgba(0,0,0,0.6)]"
                : "hidden"
            }
          >
            <RessourcesBar partie_id={partie_id} />
          </div>
          <nav className="grid grid-cols-4 border-t border-stone-800 bg-stone-950 pb-[env(safe-area-inset-bottom)]">
          {(
            [
              ["etat", "⚔️", "État"],
              ["chat", "💬", "Chat"],
              ["outils", "🎲", "Outils"],
            ] as [MobileView, string, string][]
          ).map(([vue, icone, label]) => (
            <button
              key={vue}
              onClick={() => goToMobileView(vue)}
              className={
                "relative flex flex-col items-center gap-0.5 py-1.5 text-[10px] border-t-2 " +
                (mobileView === vue
                  ? "text-amber-300 border-amber-400"
                  : "text-stone-500 border-transparent")
              }
            >
              <span className="text-lg leading-none">{icone}</span>
              {label}
              {vue === "outils" && teamUnread > 0 && (
                <span className="absolute top-1 right-1/4 w-2 h-2 bg-red-500 rounded-full" />
              )}
            </button>
          ))}
          <button
            onClick={() => setRessourcesOuvertes((v) => !v)}
            className={
              "relative flex flex-col items-center gap-0.5 py-1.5 text-[10px] border-t-2 " +
              (ressourcesOuvertes
                ? "text-amber-300 border-amber-400"
                : "text-stone-500 border-transparent")
            }
          >
            <span className="text-lg leading-none">📚</span>
            Ressources
          </button>
          </nav>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="flex-1 min-h-0 flex overflow-hidden">
        <StateSidebar />
        {colonneCentrale}
        <RightSidebar sendSay={sendSay} sendTeamSay={sendTeamSay} socket={socket} />
      </div>
      <RessourcesBar partie_id={partie_id} />
    </div>
  );
}
