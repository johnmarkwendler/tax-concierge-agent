import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { activeSurface, applyA2UIMessages } from "./lib/a2ui";
import { fetchSession, submitAction, submitStory, uploadDocument } from "./lib/api";
import type { SessionState } from "./lib/types";
import { A2UISurfaceView } from "./components/Registry";

const initialStory =
  "I changed jobs, have an LLC, and received tax forms I am not sure how to classify.";

export function App() {
  const [session, setSession] = useState<SessionState | null>(null);
  const [story, setStory] = useState(initialStory);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const surfaces = useMemo(
    () => applyA2UIMessages(session?.a2ui_messages ?? []),
    [session?.a2ui_messages]
  );
  const surface = activeSurface(surfaces);

  async function run<T>(operation: () => Promise<T>, after: (result: T) => void) {
    setBusy(true);
    setError(null);
    try {
      after(await operation());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <strong>Tax Concierge</strong>
          <span>{session?.session_id ? `Session ${session.session_id.slice(0, 8)}` : "New intake"}</span>
        </div>
        <button
          type="button"
          className="secondary-button"
          disabled={!session?.session_id || busy}
          onClick={() => {
            if (!session?.session_id) return;
            void run(() => fetchSession(session.session_id), setSession);
          }}
        >
          Refresh
        </button>
      </header>

      <section className="story-composer">
        <div>
          <p className="product-name">Guided worktable</p>
          <h1>Not sure where to start? Just tell us what's going on</h1>
          <p>Describe your situation in your own words. We'll figure out the rest, one question at a time.</p>
        </div>
        <textarea
          value={story}
          onChange={(event) => setStory(event.target.value)}
          className="story-field"
          placeholder="Describe your business, owners, documents, and anything that changed this tax year."
        />
        <div className="composer-actions">
          <button
            type="button"
            className="primary-button"
            disabled={busy || story.trim().length < 8}
            onClick={() =>
              void run(
                () =>
                  submitStory({
                    session_id: session?.session_id ?? null,
                    user_story: story
                  }),
                setSession
              )
            }
          >
            {busy ? "Working..." : "Get started"}
          </button>
          <span>Skip sensitive info — no SSNs, EINs, or account numbers needed.</span>
        </div>
      </section>

      <AnimatePresence>
        {error ? (
          <motion.div
            role="alert"
            className="error-banner"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
          >
            {error}
          </motion.div>
        ) : null}
      </AnimatePresence>

      <A2UISurfaceView
        surface={surface}
        knownFacts={session?.known_facts ?? {}}
        readiness={session?.readiness_state ?? "Getting to know you"}
        missingFacts={session?.missing_facts ?? []}
        candidateEntities={session?.candidate_entities ?? []}
        documentItems={session?.document_review_items ?? []}
        onAnswer={(fieldId, value) => {
          if (!session?.session_id) return;
          void run(() => submitAction(session.session_id, { [fieldId]: value }), setSession);
        }}
        onSecurityReview={(value) => {
          if (!session?.session_id) return;
          void run(() => submitAction(session.session_id, { user_story: value }), setSession);
        }}
        onUpload={(file) => {
          void run(() => uploadDocument(session?.session_id ?? null, file), setSession);
        }}
      />
    </div>
  );
}
