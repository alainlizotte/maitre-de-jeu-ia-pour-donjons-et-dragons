// Hook — branche une ChatSocket sur `/ws/{partie_id}` avec le store Zustand.
// Gère le rejeu de l'historique au `joined` et l'accumulation des deltas.

import { useEffect, useRef, useCallback } from "react";
import { ChatSocket } from "../api/ws";
import { encounterFromUrl } from "../api/rest";
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
  const addMonster = useParty((s) => s.addMonster);
  const addTeamMessage = useParty((s) => s.addTeamMessage);
  const setTeamMessages = useParty((s) => s.setTeamMessages);
  const player = useParty((s) => s.player);
  const lastJoinRef = useRef<string>("");

  const beep = useCallback((freq = 800, duration = 120, vol = 0.15) => {
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.value = vol;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration / 1000);
      osc.stop(ctx.currentTime + duration / 1000);
      setTimeout(() => ctx.close(), duration + 100);
    } catch { /* audio non disponible */ }
  }, []);

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
            // Historique chat d'équipe.
            if (msg.team_history && msg.team_history.length > 0) {
              setTeamMessages(msg.team_history);
            }
            // Dédup : ne pas re-joiner si le WS vient de ce client.
            if (lastJoinRef.current !== partie_id) {
              sock.join(player, useParty.getState().password);
              lastJoinRef.current = partie_id;
            }
          } else if (msg.event === "participant_joined") {
            addParticipant(msg.player);
          } else if (msg.event === "auth_required") {
            // Partie protégée : le serveur attend le join avec mot de passe.
            // Marque lastJoinRef pour ne pas re-joiner au `joined` qui suivra.
            if (lastJoinRef.current !== partie_id) {
              sock.join(player, useParty.getState().password);
              lastJoinRef.current = partie_id;
            }
          } else if (msg.event === "auth_failed") {
            addMessage({
              id: uid(),
              role: "system",
              content: `⛔ ${msg.detail} Retournez à l'accueil et retapez le mot de passe de la partie.`,
              ts: Date.now(),
            });
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
        case "tool_event": {
          // Image de monstre en direct → galerie « monstres rencontrés ».
          const ev = msg.event as ToolEvent;
          if (ev.image && ev.image.includes("/bestiaire_cache/")) {
            addMonster(encounterFromUrl(ev.image));
          }
          // Affiche les messages d'info (image_pending, etc.) même hors streaming
          // pour que le joueur voie le délai de génération d'image.
          if (ev.msg || ev.description || ev.image) {
            addMessage({
              id: uid(),
              role: "system",
              content: String(ev.msg ?? ev.description ?? "(tool)"),
              image: ev.image,
              ts: Date.now(),
            });
          }
          break;
        }
        case "dm": {
          // state_patches du tour : image_monstre → galerie de la colonne droite.
          const patches = (msg.state_patches || []) as Record<string, unknown>[];
          for (const p of patches) {
            const img = p && p.image_monstre;
            if (typeof img === "string" && img.includes("/bestiaire_cache/")) {
              addMonster(encounterFromUrl(img));
            }
          }
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
          beep(520, 150);
          break;
        }
        case "team_msg": {
          addTeamMessage(msg.player, msg.text);
          beep(660, 100);
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

  const sendTeamSay = (text: string) => {
    if (!sockRef.current || !player) return;
    sockRef.current.send({ type: "team_say", player, text });
  };

  return { sendSay, sendTeamSay, socket: sockRef };
}
