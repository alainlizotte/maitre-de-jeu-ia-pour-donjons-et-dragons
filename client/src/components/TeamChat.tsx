import { useEffect, useRef, useState } from "react";
import { useParty } from "../store";

interface TeamChatProps {
  sendTeamSay: (text: string) => void;
  socket?: React.RefObject<{ send: (payload: Record<string, unknown>) => void } | null>;
}

export function TeamChat({ sendTeamSay, socket }: TeamChatProps) {
  const teamMessages = useParty((s) => s.teamMessages);
  const player = useParty((s) => s.player);
  const audioEnabled = useParty((s) => s.audioEnabled);
  const setAudioEnabled = useParty((s) => s.setAudioEnabled);
  const [text, setText] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [teamMessages]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !player) return;
    sendTeamSay(t);
    setText("");
  };

  return (
    <div className="flex flex-col h-full bg-stone-900">
      <div className="px-3 py-2 border-b border-stone-700 text-xs text-stone-400 flex items-center gap-2">
        <span>💬 Chat d'équipe</span>
        {socket && (
          <div className="ml-auto">
            <AudioToggle audioEnabled={audioEnabled} setAudioEnabled={setAudioEnabled} socket={socket} />
          </div>
        )}
      </div>
      <div className="flex-1 overflow-auto p-3 space-y-2">
        {teamMessages.length === 0 && (
          <p className="text-stone-500 text-xs italic text-center mt-4">
            Aucun message. Discutez avec vos coéquipiers !
          </p>
        )}
        {teamMessages.map((m, i) => (
          <div key={i} className="text-sm">
            <span className="text-amber-300 font-medium">{m.player}</span>
            <span className="text-stone-500 text-xs ml-1">
              {m.ts ? new Date(m.ts).toLocaleTimeString("fr", { hour: "2-digit", minute: "2-digit" }) : ""}
            </span>
            <div className="text-stone-200 ml-1">{m.text}</div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <form onSubmit={submit} className="border-t border-stone-700 p-2 flex gap-2">
        <input
          className="flex-1 bg-stone-800 border border-stone-600 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-amber-400"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Message équipe…"
          disabled={!player}
        />
        <button
          type="submit"
          className="px-3 py-1.5 bg-amber-700 hover:bg-amber-600 disabled:opacity-40 rounded text-sm font-medium text-stone-900"
          disabled={!player || !text.trim()}
        >
          Envoyer
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Bouton micro — audio WebRTC peer-to-peer
// ---------------------------------------------------------------------------

interface AudioToggleProps {
  audioEnabled: boolean;
  setAudioEnabled: (v: boolean) => void;
  socket: React.RefObject<{ send: (payload: Record<string, unknown>) => void } | null>;
}

function AudioToggle({ audioEnabled, setAudioEnabled, socket }: AudioToggleProps) {
  const streamRef = useRef<MediaStream | null>(null);
  const peersRef = useRef<Map<string, RTCPeerConnection>>(new Map());
  const audioCtxRef = useRef<AudioContext | null>(null);

  const cleanup = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    peersRef.current.forEach((pc) => pc.close());
    peersRef.current.clear();
    audioCtxRef.current?.close();
    audioCtxRef.current = null;
    const handlers = (window as unknown as Record<string, unknown>).__audioHandlers as
      | Map<string, (signal: Record<string, unknown>, from: string) => void>
      | undefined;
    if (handlers) handlers.delete("signal");
  };

  const toggleAudio = async () => {
    if (audioEnabled) {
      cleanup();
      setAudioEnabled(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      setAudioEnabled(true);

      const createPeer = async (remotePlayer: string, localStream: MediaStream) => {
        const pc = new RTCPeerConnection({
          iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });
        localStream.getAudioTracks().forEach((track) => pc.addTrack(track, localStream));

        pc.onicecandidate = (e) => {
          if (e.candidate && socket.current) {
            socket.current.send({
              type: "audio_signal",
              signal: { type: "ice-candidate", candidate: e.candidate.toJSON(), target: remotePlayer },
              player: useParty.getState().player,
            });
          }
        };
        pc.ontrack = (e) => {
          if (!audioCtxRef.current) audioCtxRef.current = new AudioContext();
          const audio = new Audio();
          audio.srcObject = e.streams[0];
          audio.play().catch(() => {});
        };
        pc.onconnectionstatechange = () => {
          if (pc.connectionState === "disconnected" || pc.connectionState === "failed") {
            pc.close();
            peersRef.current.delete(remotePlayer);
          }
        };
        peersRef.current.set(remotePlayer, pc);
        return pc;
      };

      const handlers = (window as unknown as Record<string, unknown>).__audioHandlers as
        | Map<string, (signal: Record<string, unknown>, from: string) => void>
        | undefined;
      if (handlers) {
        handlers.set("signal", async (signal: Record<string, unknown>, from: string) => {
          if (!streamRef.current) return;
          if (signal.type === "offer-request") {
            const pc = await createPeer(from, streamRef.current);
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            socket.current?.send({
              type: "audio_signal",
              signal: { type: "offer", sdp: pc.localDescription?.toJSON(), target: from },
              player: useParty.getState().player,
            });
          } else if (signal.type === "offer") {
            const pc = await createPeer(from, streamRef.current);
            await pc.setRemoteDescription(new RTCSessionDescription(signal.sdp as RTCSessionDescriptionInit));
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            socket.current?.send({
              type: "audio_signal",
              signal: { type: "answer", sdp: pc.localDescription?.toJSON(), target: from },
              player: useParty.getState().player,
            });
          } else if (signal.type === "answer") {
            const pc = peersRef.current.get(from);
            if (pc) await pc.setRemoteDescription(new RTCSessionDescription(signal.sdp as RTCSessionDescriptionInit));
          } else if (signal.type === "ice-candidate" && signal.candidate) {
            const pc = peersRef.current.get(from);
            if (pc) await pc.addIceCandidate(new RTCIceCandidate(signal.candidate as RTCIceCandidateInit));
          }
        });
      }

      socket.current?.send({
        type: "audio_signal",
        signal: { type: "offer-request" },
        player: useParty.getState().player,
      });
    } catch {
      setAudioEnabled(false);
      cleanup();
    }
  };

  return (
    <button
      onClick={toggleAudio}
      className={`px-2 py-1 rounded text-xs font-medium flex items-center gap-1 transition-colors ${
        audioEnabled
          ? "bg-emerald-600 text-white animate-pulse"
          : "bg-stone-700 text-stone-400 hover:bg-stone-600"
      }`}
      title={audioEnabled ? "Couper le microphone" : "Activer le microphone (WebRTC)"}
    >
      <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
        <path d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" />
      </svg>
      {audioEnabled ? "ON" : "Mic"}
    </button>
  );
}
