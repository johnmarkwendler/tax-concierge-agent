import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CheckCircle, FileText, Lock, Send, ShieldCheck, Sparkles } from "lucide-react";
import { activeSurface, applyA2UIMessages } from "./lib/a2ui";
import { fetchSession, submitAction, submitStory, uploadDocument } from "./lib/api";
import type { SessionState } from "./lib/types";
import { A2UISurfaceView } from "./components/Registry";

const initialStory =
  "I changed jobs, have an LLC, and received tax forms I am not sure how to classify.";

const promptChips = [
  { label: "Which forms matter?", value: "I received tax forms and I am not sure which ones matter." },
  { label: "I changed jobs", value: "I changed jobs this year and need help understanding what changed for my taxes." },
  { label: "I have an LLC", value: "I have an LLC and want to know what tax details I need to organize." },
  { label: "I got a K-1", value: "I received a K-1 and am not sure how it fits with the rest of my return." },
  { label: "I sold investments", value: "I sold investments this year and need help figuring out what information matters." }
];

const trustItems = [
  { label: "No SSNs needed", icon: ShieldCheck },
  { label: "Secure and private", icon: Lock },
  { label: "One question at a time", icon: CheckCircle },
  { label: "Works with W-2s, LLCs, K-1s, investments, and more", icon: FileText }
];

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
  const hasSurface = Boolean(surface?.components.length);
  const viewState = !hasSurface
    ? "initial"
    : session?.readiness_state.includes("Ready")
      ? "recommendation"
      : "active";

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

  const submitCurrentStory = () => {
    void run(
      () =>
        submitStory({
          session_id: session?.session_id ?? null,
          user_story: story
        }),
      setSession
    );
  };

  return (
    <div className={`app-shell state-${viewState}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            <Sparkles size={18} />
          </span>
          <strong>Tax Concierge</strong>
        </div>
        <nav className="topbar-actions" aria-label="Primary">
          <span className="topbar-note">Private intake</span>
          {session?.session_id ? (
            <button
              type="button"
              className="secondary-button"
              disabled={busy}
              onClick={() => {
                void run(() => fetchSession(session.session_id), setSession);
              }}
            >
              Refresh
            </button>
          ) : null}
        </nav>
      </header>

      {!hasSurface ? (
        <InitialHeroState
          story={story}
          busy={busy}
          onStoryChange={setStory}
          onSubmit={submitCurrentStory}
        />
      ) : null}

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

      {hasSurface ? (
        <A2UISurfaceView
          surface={surface}
          knownFacts={session?.known_facts ?? {}}
          readiness={session?.readiness_state ?? "Getting to know you"}
          missingFacts={session?.missing_facts ?? []}
          candidateEntities={session?.candidate_entities ?? []}
          documentItems={session?.document_review_items ?? []}
          viewState={viewState}
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
      ) : null}
    </div>
  );
}

function InitialHeroState({
  story,
  busy,
  onStoryChange,
  onSubmit
}: {
  story: string;
  busy: boolean;
  onStoryChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const canSubmit = story.trim().length >= 8 && !busy;

  return (
    <motion.main
      className="hero-layout"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
    >
      <section className="hero-copy-block" aria-labelledby="hero-heading">
        <div className="expert-pill">
          <Sparkles size={16} />
          AI tax concierge
        </div>
        <h1 id="hero-heading">Tell us what’s going on—we’ll handle the tax complexity.</h1>
        <p className="hero-subcopy">
          No tax jargon. No forms to classify. We’ll guide you one question at a time.
        </p>
        <div className="trust-line" aria-label="Trust and support">
          {trustItems.map(({ label, icon: Icon }) => (
            <span key={label}>
              <Icon size={17} />
              {label}
            </span>
          ))}
        </div>
      </section>

      <motion.section
        className="assistant-spotlight"
        aria-labelledby="spotlight-title"
        initial={{ opacity: 0, y: 22 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.34, delay: 0.08, ease: [0.16, 1, 0.3, 1] }}
      >
        <div className="spotlight-heading">
          <div>
            <p className="product-name">Tax Concierge</p>
            <h2 id="spotlight-title">Start with your situation</h2>
          </div>
          <span className="assistant-badge" aria-hidden="true">
            <Sparkles size={18} />
          </span>
        </div>

        <div className="prompt-chips" aria-label="Starter prompts">
          {promptChips.map((chip, index) => (
            <motion.button
              key={chip.label}
              type="button"
              className="prompt-chip"
              onClick={() => onStoryChange(chip.value)}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, delay: 0.16 + index * 0.04 }}
            >
              {chip.label}
            </motion.button>
          ))}
        </div>

        <label className="sr-only" htmlFor="story-input">
          Describe your tax situation
        </label>
        <div className="spotlight-input-row">
          <textarea
            id="story-input"
            value={story}
            onChange={(event) => onStoryChange(event.target.value)}
            className="story-field spotlight-field"
            placeholder="Type or ask what's going on..."
          />
          <button
            type="button"
            className="send-button"
            aria-label="Start intake"
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            <Send size={24} />
          </button>
        </div>

        <button
          type="button"
          className="primary-button spotlight-cta"
          disabled={!canSubmit}
          onClick={onSubmit}
        >
          {busy ? "Working..." : "Start for free"}
        </button>
      </motion.section>
    </motion.main>
  );
}
