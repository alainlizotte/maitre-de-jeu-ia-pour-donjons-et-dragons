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

export function PartyPage() {
  const { partie_id } = useParams<{ partie_id: string }>();
  const navigate = useNavigate();
  const setPartieId = useParty((s) => s.setPartieId);
  const setState = useParty((s) => s.setState);
  const player = useParty((s) => s.player);

  // Persistence de l'id courant + sert de clé au hook WS.
  useEffect(() => {
    if (partie_id) setPartieId(partie_id);
  }, [partie_id, setPartieId]);

  // Re-bascule à l'accueil si pas de pseudo (nécessaire pour le join).
  useEffect(() => {
    if (!player) navigate("/");
  }, [player, navigate]);

  // Charge l'état persisté côté REST (validation initiale + sidebar droite).
  const state = useQuery({
    queryKey: ["party", partie_id],
    queryFn: () => api.getParty(partie_id!),
    enabled: !!partie_id,
    refetchInterval: 15000, // rafraîchit les PJ/ lieu périodiquement
  });

  useEffect(() => {
    // L'API renvoie {partie_id, etat} — le store veut l'état lui-même
    // (l'enveloppe n'a ni meta, ni lieu, ni pj : la sidebar resterait vide).
    const etat = state.data?.etat;
    if (etat && !("_erreur" in etat)) setState(etat);
  }, [state.data, setState]);

  const { sendSay, sendTeamSay } = useChatSocket(partie_id ?? null);

  return (
    <div className="h-full flex flex-col">
      {/* Barre de ressources permanente : manuels, cartes, scénarios PDF. */}
      <div className="flex-1 min-h-0 flex">
        <StateSidebar />
        <ChatPanel sendSay={sendSay} />
        <RightSidebar sendTeamSay={sendTeamSay} />
      </div>
      <RessourcesBar partie_id={partie_id} />
    </div>
  );
}
