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
  const removeMessage = useParty((s) => s.removeMessage);
  const appendDelta = useParty((s) => s.appendDelta);
  const finalizeStream = useParty((s) => s.finalizeStream);
  const setThinking = useParty((s) => s.setThinking);
  const setParticipants = useParty((s) => s.setParticipants);
  const addParticipant = useParty((s) => s.addParticipant);
  const addMonster = useParty((s) => s.addMonster);
  const removeMonsterByNom = useParty((s) => s.removeMonsterByNom);
  const addScene = useParty((s) => s.addScene);
  const addTeamMessage = useParty((s) => s.addTeamMessage);
  const setTeamMessages = useParty((s) => s.setTeamMessages);
  const applyPatches = useParty((s) => s.applyPatches);
  const bumpStateRev = useParty((s) => s.bumpStateRev);
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

  /** Route une URL d'image générée vers la bonne galerie :
   *  - bestiaire_cache → monstres rencontrés ;
   *  - images_salles / images_scenes → scènes illustrées.
   *  Renvoie true si l'image a été classée (elle sera aussi affichée en chat). */
  const classifyImage = useCallback(
    (url: string): boolean => {
      if (!url) return false;
      if (url.includes("/bestiaire_cache/")) {
        addMonster(encounterFromUrl(url));
        return true;
      }
      if (url.includes("/images_salles/") || url.includes("/images_scenes/")) {
        const fichier = decodeURIComponent(url.split("/").pop() ?? "");
        const nom =
          fichier
            .replace(/\.(png|jpe?g|webp|svg)$/i, "")
            .replace(/[_-]+/g, " ")
            .trim() || "scène";
        addScene({ nom, url });
        return true;
      }
      return false;
    },
    [addMonster, addScene],
  );

  useEffect(() => {
    if (!partie_id || !player) return;

    // Registre global pour les handlers audio WebRTC (relayés via WS).
    if (!(window as unknown as Record<string, unknown>).__audioHandlers) {
      (window as unknown as Record<string, unknown>).__audioHandlers = new Map();
    }

    const sock = new ChatSocket(partie_id);
    sockRef.current = sock;

    // Applique des patches d'état au store + effets de bord associés
    // (re-fetch REST sur pj_updated, galeries d'images, retrait des monstres
    // détruits). Utilisé par le push immédiat « state_patches » ET par le
    // batch final du message « dm ».
    const applyStatePatches = (patches: Record<string, unknown>[]) => {
      applyPatches(patches);
      for (const p of patches) {
        if (!p) continue;
        for (const cle of ["image_monstre", "image_scene"]) {
          const img = p[cle];
          if (typeof img === "string") classifyImage(img);
        }
      }
      if (patches.some((p) => p && "pj_updated" in p)) {
        bumpStateRev();
      }
      // Mort d'un monstre : son portrait quitte la galerie
      // (« les images restent affichées jusqu'à sa mort »).
      for (const p of patches) {
        if (!p) continue;
        const mc = p["monstres_combat"];
        if (!Array.isArray(mc)) continue;
        for (const m of mc as { nom?: string; conditions?: string[] }[]) {
          if (m?.nom && (m.conditions ?? []).includes("Détruit")) {
            removeMonsterByNom(m.nom);
          }
        }
      }
    };

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
              sock.join(player, useParty.getState().password, useParty.getState().personnage);
              lastJoinRef.current = partie_id;
            }
          } else if (msg.event === "participant_joined") {
            addParticipant(msg.player);
          } else if (msg.event === "auth_required") {
            // Partie protégée : le serveur attend le join avec mot de passe.
            // Marque lastJoinRef pour ne pas re-joiner au `joined` qui suivra.
            if (lastJoinRef.current !== partie_id) {
              sock.join(player, useParty.getState().password, useParty.getState().personnage);
              lastJoinRef.current = partie_id;
            }
          } else if (msg.event === "auth_failed") {
            addMessage({
              id: uid(),
              role: "system",
              content: `⛔ ${msg.detail} Retournez à l'accueil et retapez le mot de passe de la partie.`,
              ts: Date.now(),
            });
          } else if (msg.event === "join_refused") {
            // Refus : aucun personnage sélectionné ou fiche inconnue.
            addMessage({
              id: uid(),
              role: "system",
              content: `⛔ ${msg.detail}`,
              ts: Date.now(),
            });
          } else if (msg.event === "turn_blocked") {
            // Hors-tour en combat : le serveur refuse d'invoquer le MJ.
            addMessage({
              id: uid(),
              role: "system",
              content: msg.detail,
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
          // Libellé serveur (« Le MJ réfléchit... », « Le MJ finalise la
          // scène… ») affiché tel quel pendant le tour ; done → indicateur off.
          setThinking(
            msg.done ? false : Boolean(msg.description),
            msg.done ? undefined : (msg.description || undefined),
          );
          break;
        case "delta": {
          // On n'accumule que dans un bloc « streaming » encore vivant. Si le
          // streamId restant est périmé (bloc déjà finalisé ou disparu), on
          // repart sur un nouveau bloc plutôt que d'écraser un message ancien.
          if (
            !streamId.current ||
            !useParty
              .getState()
              .messages.some((m) => m.id === streamId.current && m.streaming)
          ) {
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
          // Image générée en direct → galerie adaptée (monstres / scènes).
          const ev = msg.event as ToolEvent;
          classifyImage(String(ev.image ?? ""));
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
        case "state_patches": {
          // Push immédiat (tool exécuté côté serveur) : PV, phase,
          // initiative… bougent à l'écran sans attendre le dm final.
          applyStatePatches((msg.patches || []) as Record<string, unknown>[]);
          break;
        }
        case "stream_reset": {
          // Le serveur a relancé le tour (simulation, répétition, rejeu) :
          // l'aperçu streamé est périmé → on l'efface pour ne garder que le
          // texte final qui va suivre (deltas → nouveau bloc, puis dm final).
          if (streamId.current) {
            removeMessage(streamId.current);
            streamId.current = null;
          }
          break;
        }
        case "dm": {
          // state_patches du tour : images → galerie de la colonne droite,
          // le reste (lieu.position_x, phase, pj.0.pv…) → état du store
          // en direct (sans attendre le polling REST de 15 s).
          applyStatePatches(
            (msg.state_patches || []) as Record<string, unknown>[],
          );
          const sid = streamId.current || uid();
          const streamingTarget = streamId.current
            ? useParty
                .getState()
                .messages.find((m) => m.id === streamId.current && m.streaming)
            : undefined;
          if (streamingTarget) {
            finalizeStream(
              sid,
              msg.text,
              msg.tool_events || [],
              (msg.tool_events || []).find((e) => (e as ToolEvent).image)?.image,
            );
          } else {
            addMessage({
              id: sid,
              role: "dm",
              content: msg.text,
              toolEvents: msg.tool_events || [],
              ts: Date.now(),
            });
          }
          streamId.current = null;
          setThinking(false);
          beep(520, 150);
          break;
        }
        case "team_msg": {
          addTeamMessage(msg.player, msg.text);
          // Son distinctif chat joueurs : double bip aigu
          beep(880, 80);
          setTimeout(() => beep(1100, 80), 100);
          break;
        }
        case "audio_signal": {
          // Signaux WebRTC relayés via le WS serveur (relay simple).
          const audioHandlers = (window as unknown as Record<string, unknown>).__audioHandlers as
            | Map<string, (signal: Record<string, unknown>, from: string) => void>
            | undefined;
          if (audioHandlers) {
            const handler = audioHandlers.get("signal");
            if (handler) handler(msg.signal as Record<string, unknown>, (msg as Record<string, unknown>).player as string);
          }
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
