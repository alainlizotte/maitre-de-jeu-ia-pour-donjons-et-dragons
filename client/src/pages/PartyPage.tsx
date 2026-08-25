// Page de partie — layout 3 colonnes (état/chat/outils). Charge l'état REST,
// branche le WS via useChatSocket, synchronise les state_patches WS.

import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/rest";
import { useParty } from "../store";
import { useChatSocket } from "../hooks/useChatSocket";
import { StateSidebar } from "../components/StateSidebar";
import { ChatPanel } from "../components/ChatPanel";
import { RightSidebar } from "../components/RightSidebar";
import { RessourcesBar } from "../components/RessourcesBar";
import { ScenarioPicker } from "../components/ScenarioPicker";

export function PartyPage() {
  const { partie_id } = useParams<{ partie_id: string }>();
  const navigate = useNavigate();
  const setPartieId = useParty((s) => s.setPartieId);
  const setState = useParty((s) => s.setState);
  const state = useParty((s) => s.state);
  const player = useParty((s) => s.player);

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
    const etat = partyQuery.data?.etat;
    if (etat && !("_erreur" in etat)) setState(etat);
  }, [partyQuery.data, setState]);

  const { sendSay, sendTeamSay, socket } = useChatSocket(partie_id ?? null);

  // set_quest enregistre toujours un objet quête (même à titre vide pour
  // « aventure libre ») ; tant que rien n'est enregistré, le sélecteur reste
  // affiché quelle que soit la phase — aucune limite de temps.
  const quete = state?.quete ?? null;
  const queteChoisie = Boolean(quete && (quete.titre || quete.source));
  const showPicker = Boolean(partie_id) && !queteChoisie;

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="flex-1 min-h-0 flex overflow-hidden">
        <StateSidebar />
        <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
          {quete?.titre && (
            <div className="m-4 mb-2 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2">
              <span className="text-amber-300 font-semibold">📜 Quête : {quete.titre}</span>
              {quete.pitch && (
                <span className="text-stone-400 text-sm"> — {quete.pitch}</span>
              )}
            </div>
          )}
          {showPicker && partie_id && (
            <div className="p-4 shrink-0">
              <ScenarioPicker partieId={partie_id} />
            </div>
          )}
          <ChatPanel sendSay={sendSay} />
        </div>
        <RightSidebar sendSay={sendSay} sendTeamSay={sendTeamSay} socket={socket} />
      </div>
      <RessourcesBar partie_id={partie_id} />
    </div>
  );
}
