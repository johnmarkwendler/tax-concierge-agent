import type { SessionState } from "./types";

const API_ORIGIN = import.meta.env.VITE_API_ORIGIN ?? "";

type IntakePayload = {
  session_id: string | null;
  user_story: string;
};

async function parseSession(response: Response): Promise<SessionState> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with status ${response.status}`);
  }
  return (await response.json()) as SessionState;
}

export async function fetchSession(sessionId: string): Promise<SessionState> {
  return parseSession(await fetch(`${API_ORIGIN}/api/session/${sessionId}`));
}

export async function submitStory(payload: IntakePayload): Promise<SessionState> {
  return parseSession(
    await fetch(`${API_ORIGIN}/api/intake`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
  );
}

export async function submitAction(
  sessionId: string,
  answers: Record<string, unknown>
): Promise<SessionState> {
  return parseSession(
    await fetch(`${API_ORIGIN}/api/action/${sessionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers })
    })
  );
}

export async function uploadDocument(
  sessionId: string | null,
  file: File
): Promise<SessionState> {
  const body = new FormData();
  body.append("file", file);
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  return parseSession(
    await fetch(`${API_ORIGIN}/api/upload${query}`, {
      method: "POST",
      body
    })
  );
}
