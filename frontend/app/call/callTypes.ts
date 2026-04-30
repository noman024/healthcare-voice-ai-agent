import type { MutableRefObject } from "react";

export const DEFAULT_API_URL = "http://localhost:8000";

export type FeedTone = "neutral" | "ok" | "warn";
export type FeedItem = { id: string; label: string; tone: FeedTone };
export type AgentPayload = Record<string, unknown>;

export type TranscriptEntry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: number;
};

export type CallSummaryPayload = {
  summary: string;
  generated_at?: string;
  appointments?: Array<{
    id: number;
    name: string;
    phone: string;
    date: string;
    time: string;
    status: string;
    created_at: string;
  }>;
  user_preferences?: string[];
  phone?: string | null;
  cost_hints?: Record<string, unknown>;
  conversation_id?: string;
};

export type PlaybackAnalyserRef = MutableRefObject<AnalyserNode | null>;
export type TtsLifecycleRef = MutableRefObject<{ stop: () => void } | null>;
