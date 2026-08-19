// Hook — branche une ChatSocket sur `/ws/{partie_id}` avec le store Zustand.
// Gère le rejeu de l'historique au `joined` et l'accumulation des deltas.

import { useEffect, useRef } from "react";
import { ChatSocket } from "../api/ws";
import type { ChatMessage, ToolEvent, WsMessage } from "../api/types";
import { useParty, uid } from "../store";

export function useChatSocket(partie_id: string | null) {
  const sockRef = useRef<ChatSocket | null>(null);
  const streamId = useRef<string | null>(null);

  // Callbacks du store capturés une fois (évite re-render storm).
  const addMessage = useParty((s) => s.addMessage);
  const appendDelta = useParty((s) => s.appendDelta);
  const finalizeStream = useParty((s) => s.finalizeStream);
  const setThinking = useParty((s) => s.setThinking);
  const setParticipants = useParty((s) => s.setParticipants);
  const addParticipant = useParty((s) => s.addParticipant);
  const player = useParty((s) => s.player);
  const lastJoinRef = useRef<string>("");

  useEffect(() => {
    if (!partie_id || !player) return;

    const sock = new ChatSocket(partie_id);
    sockRef.current = sock;

    const handle = (msg: WsMessage) => {
      switch (msg.type) {
        case "sys":
          if (msg.event === "joined") {
            // Rejeu de l'historique persisté (role user|assistant).
            const replayed: ChatMessage[] = (msg.history || [])
              .filter((h) => h.role === "user" || h.role === "assistant")
              .map((h, i) => ({
                id: `replay-${i}`,
                role: h.role === "assistant" ? "dm" : "user",
                content: h.content,
                ts: 0,
              }));
            replayed.forEach((m) => addMessage(m));
            setParticipants(msg.participants || []);
            // Dédup : ne pas re-joiner si le WS vient de ce client.
            if (lastJoinRef.current !== partie_id) {
              sock.join(player);
              lastJoinRef.current = partie_id;
            }
          } else if (msg.event === "participant_joined") {
            addParticipant(msg.player);
          } else if (msg.event === "error") {
            addMessage({
              id: uid(),
              role: "system",
              content: `⚠️ ${msg.detail}`,
              ts: Date.now(),
            });
          }
          break;
        case "player":
          // Ack du serveur — l'auteur verra son message. On évite le double
          // ici : le client ajoute déjà son propre message avant l'envoi.
          break;
        case "status":
          setThinking(msg.done ? false : Boolean(msg.description));
          break;
        case "delta": {
          if (!streamId.current) {
            streamId.current = uid();
            addMessage({
              id: streamId.current,
              role: "dm",
              content: "",
              streaming: true,
              ts: Date.now(),
            });
          }
          appendDelta(streamId.current, msg.text);
          break;
        }
        case "tool_event":
          // On accumule les events image/log sur le stream courant (non-final).
          if (streamId.current) {
            // On stocke via finalizeStream neutre sans casser le streaming.
            // Simplification : on push dans une liste à part via addMessage sys.
            addMessage({
              id: uid(),
              role: "system",
              content: msg.event.description || "(tool)",
              image: (msg.event as ToolEvent).image,
              ts: Date.now(),
            });
          }
          break;
        case "dm": {
          const sid = streamId.current || uid();
          if (!streamId.current) {
            addMessage({
              id: sid,
              role: "dm",
              content: msg.text,
              toolEvents: msg.tool_events || [],
              ts: Date.now(),
            });
          } else {
            finalizeStream(
              sid,
              msg.text,
              msg.tool_events || [],
              (msg.tool_events || []).find((e) => (e as ToolEvent).image)?.image,
            );
          }
          streamId.current = null;
          setThinking(false);
          break;
        }
      }
    };

    sock.on(handle);
    sock.connect();
    // Le serveur attribue les participants sur le `joined` ; pas de join auto
    // ici — déclenché sur réception du `joined` (cf. ci-dessus).

    return () => {
      sock.close();
      sockRef.current = null;
      streamId.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partie_id, player]);

  const sendSay = (text: string) => {
    if (!sockRef.current || !player) return;
    addMessage({
      id: uid(),
      role: "user",
      player,
      content: text,
      ts: Date.now(),
    });
    sockRef.current.say(player, text);
  };

  return { sendSay, socket: sockRef };
}
