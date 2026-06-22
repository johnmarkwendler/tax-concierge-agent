import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import type { A2UIComponent, A2UISurface, DocumentReviewItem, ReadinessState } from "../lib/types";
import { SUPPORTED_COMPONENTS } from "../lib/a2ui";

type RegistryProps = {
  surface: A2UISurface | null;
  knownFacts: Record<string, unknown>;
  readiness: ReadinessState;
  missingFacts: string[];
  candidateEntities: string[];
  documentItems: DocumentReviewItem[];
  onAnswer: (fieldId: string, value: unknown) => void;
  onSecurityReview: (value: string) => void;
  onUpload: (file: File) => void;
};

const pretty = (value: string) =>
  value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

const asString = (value: unknown, fallback = "") => (typeof value === "string" ? value : fallback);

const optionsFor = (value: unknown): { label: string; value: unknown }[] =>
  Array.isArray(value)
    ? value.map((option) => {
        if (option && typeof option === "object") {
          const record = option as Record<string, unknown>;
          return {
            label: asString(record.label, String(record.value ?? "")),
            value: record.value ?? record.label
          };
        }
        return { label: String(option), value: option };
      })
    : [];

export function A2UISurfaceView(props: RegistryProps) {
  return (
    <LayoutGroup id="tax-concierge-intake">
      <div className="workbench">
        <main className="primary-flow">
          <StoryInputCard />
          <SurfaceStage {...props} />
        </main>
        <aside className="side-rail" aria-label="Tax intake status">
          <ReadinessStatus readiness={props.readiness} missingFacts={props.missingFacts} />
          <ConfirmedFactsRail facts={props.knownFacts} />
          <DocumentUploadCard onUpload={props.onUpload} documentItems={props.documentItems} />
        </aside>
      </div>
    </LayoutGroup>
  );
}

function SurfaceStage(props: RegistryProps) {
  const surface = props.surface;
  const reduce = useReducedMotion();

  if (!surface?.components.length) {
    return (
      <motion.section layout className="guided-card quiet-card">
        <h2>Follow-up questions will appear here one at a time.</h2>
        <p>Start with what you know. We will organize the details as they become useful.</p>
      </motion.section>
    );
  }

  const root = surface.components.find((component) => component.id === surface.root) ?? surface.components[0];
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={`${surface.surfaceId}:${root.id}`}
        layout
        initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12 }}
        animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
        exit={reduce ? { opacity: 0 } : { opacity: 0, y: -12 }}
        transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      >
        <RenderComponent component={root} {...props} />
      </motion.div>
    </AnimatePresence>
  );
}

function RenderComponent({ component, ...props }: RegistryProps & { component: A2UIComponent }) {
  if (!SUPPORTED_COMPONENTS.has(component.component)) {
    return (
      <section className="guided-card error-card">
        <h2>Unsupported intake component</h2>
        <p>{component.component}</p>
      </section>
    );
  }

  if (component.component === "SegmentedChoiceCards") {
    return <SegmentedChoiceCards component={component} onAnswer={props.onAnswer} />;
  }
  if (component.component === "SecurityReviewCard") {
    return <SecurityReviewCard component={component} onSecurityReview={props.onSecurityReview} />;
  }
  if (component.component === "RecommendationCard") {
    return <RecommendationCard component={component} candidates={props.candidateEntities} />;
  }
  if (component.component === "DocumentFieldReviewCard") {
    return <DocumentFieldReviewCard documentItems={props.documentItems} />;
  }
  return null;
}

export function StoryInputCard() {
  return (
    <motion.section layout className="hero-intake" aria-label="Tax Concierge intake">
      <div>
        <p className="product-name">Tax Concierge</p>
        <h1>Tell us about your business.</h1>
        <p className="hero-copy">Come as you are. We will figure out the entity path together.</p>
      </div>
    </motion.section>
  );
}

function SegmentedChoiceCards({
  component,
  onAnswer
}: {
  component: A2UIComponent;
  onAnswer: (fieldId: string, value: unknown) => void;
}) {
  const props = component.props ?? {};
  const fieldId = asString((component.action?.payload ?? {}).fieldId, component.id);
  const choices = optionsFor(props.options);

  return (
    <motion.section layout className="guided-card" aria-labelledby={`${component.id}-label`}>
      <div className="card-heading">
        <span className="status needs">{asString(props.readinessState, "Needs clarification")}</span>
        <h2 id={`${component.id}-label`}>{asString(props.label, "One more business detail")}</h2>
        <p>{asString(props.helperText, "Choose the closest answer. You can correct it later.")}</p>
      </div>
      <div className="choice-grid" role="radiogroup" aria-label={asString(props.label, component.id)}>
        {choices.map((option) => (
          <motion.button
            key={String(option.value)}
            layout
            type="button"
            className="choice-card"
            whileTap={{ scale: 0.99 }}
            onClick={() => onAnswer(fieldId, option.value)}
          >
            <span>{option.label}</span>
          </motion.button>
        ))}
      </div>
      <WhyAskingDrawer text={asString(props.whyWeAreAsking)} />
    </motion.section>
  );
}

function SecurityReviewCard({
  component,
  onSecurityReview
}: {
  component: A2UIComponent;
  onSecurityReview: (value: string) => void;
}) {
  const props = component.props ?? {};
  return (
    <motion.section layout className="guided-card warning-card">
      <div className="card-heading">
        <span className="status security">Security review required</span>
        <h2>{asString(props.title, "Security review")}</h2>
        <p>{asString(props.message, "Please continue with only business tax facts.")}</p>
      </div>
      <textarea
        className="story-field"
        minLength={12}
        placeholder={asString(props.helperText, "Describe the business situation without sensitive identifiers.")}
        onBlur={(event) => {
          const value = event.currentTarget.value.trim();
          if (value) onSecurityReview(value);
        }}
      />
      <WhyAskingDrawer text={asString(props.whyWeAreAsking)} />
    </motion.section>
  );
}

function RecommendationCard({
  component,
  candidates
}: {
  component: A2UIComponent;
  candidates: string[];
}) {
  const props = component.props ?? {};
  return (
    <motion.section layout className="guided-card success-card">
      <span className="status confident">Ready for recommendation</span>
      <h2>{asString(props.headline, "We have a recommendation.")}</h2>
      <p>{asString(props.body, "The key details are clear enough to continue.")}</p>
      {candidates.length > 0 ? (
        <div className="recommendation-tags">
          {candidates.map((candidate) => (
            <span key={candidate}>{candidate}</span>
          ))}
        </div>
      ) : null}
    </motion.section>
  );
}

function ReadinessStatus({
  readiness,
  missingFacts
}: {
  readiness: ReadinessState;
  missingFacts: string[];
}) {
  const className = readiness.includes("Ready")
    ? "confident"
    : readiness.includes("Security")
      ? "security"
      : readiness.includes("Needs")
        ? "needs"
        : "learning";

  return (
    <motion.section layout className="panel">
      <div className="panel-title">Readiness</div>
      <motion.div layout className={`status-card ${className}`}>
        <strong>{readiness}</strong>
        <span>
          {missingFacts.length
            ? `${missingFacts.length} detail${missingFacts.length === 1 ? "" : "s"} still needed`
            : "No blocking details right now"}
        </span>
      </motion.div>
    </motion.section>
  );
}

function ConfirmedFactsRail({ facts }: { facts: Record<string, unknown> }) {
  const entries = Object.entries(facts);
  return (
    <motion.section layout className="panel">
      <div className="panel-title">We understand</div>
      <motion.div layout className="facts-rail">
        <AnimatePresence initial={false}>
          {entries.length ? (
            entries.map(([key, value]) => (
              <motion.div
                key={key}
                layout
                layoutId={`fact-${key}`}
                className="fact-chip"
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.98 }}
              >
                <span>{pretty(key)}</span>
                <strong>{String(value)}</strong>
              </motion.div>
            ))
          ) : (
            <motion.p layout className="empty-copy">
              Facts you confirm will collect here.
            </motion.p>
          )}
        </AnimatePresence>
      </motion.div>
    </motion.section>
  );
}

function DocumentUploadCard({
  onUpload,
  documentItems
}: {
  onUpload: (file: File) => void;
  documentItems: DocumentReviewItem[];
}) {
  return (
    <motion.section layout className="panel">
      <div className="panel-title">Documents</div>
      <label className="upload-control">
        <input
          type="file"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            if (file) onUpload(file);
            event.currentTarget.value = "";
          }}
        />
        <span>Upload document</span>
      </label>
      <DocumentFieldReviewCard documentItems={documentItems} />
    </motion.section>
  );
}

function DocumentFieldReviewCard({ documentItems }: { documentItems: DocumentReviewItem[] }) {
  if (!documentItems.length) {
    return <p className="empty-copy">Uploaded details will appear as editable review rows.</p>;
  }
  return (
    <motion.div layout className="doc-list">
      {documentItems.map((item) => (
        <motion.div
          layout
          key={`${item.field_label}:${item.extracted_value}`}
          className="doc-row"
        >
          <span>{item.field_label}</span>
          <strong>{item.extracted_value}</strong>
          <small>{item.needs_review ? "Needs review" : item.confidence_state}</small>
        </motion.div>
      ))}
    </motion.div>
  );
}

function WhyAskingDrawer({ text }: { text: string }) {
  if (!text) return null;
  return (
    <details className="why-drawer">
      <summary>Why we are asking</summary>
      <p>{text}</p>
    </details>
  );
}
