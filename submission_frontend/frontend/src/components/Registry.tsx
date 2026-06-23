import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import type { CSSProperties } from "react";
import { ArrowUpRight, BadgeCheck, CheckCircle, FileText, Sparkles, UploadCloud } from "lucide-react";
import type { A2UIComponent, A2UISurface, DocumentReviewItem, ReadinessState } from "../lib/types";
import { SUPPORTED_COMPONENTS } from "../lib/a2ui";

type IntakeViewState = "initial" | "active" | "recommendation";

type RegistryProps = {
  surface: A2UISurface | null;
  knownFacts: Record<string, unknown>;
  readiness: ReadinessState;
  missingFacts: string[];
  candidateEntities: string[];
  documentItems: DocumentReviewItem[];
  viewState: IntakeViewState;
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
  if (!props.surface?.components.length) return null;

  const hasFacts = Object.keys(props.knownFacts).length > 0;
  const hasDocuments = props.documentItems.length > 0;
  const showSideRail = props.viewState !== "recommendation" && (hasFacts || hasDocuments);

  return (
    <LayoutGroup id="tax-concierge-intake">
      <div className={`workflow-shell workflow-${props.viewState}`}>
        <main className="workflow-main">
          <ProgressStrip
            knownFacts={props.knownFacts}
            readiness={props.readiness}
            missingFacts={props.missingFacts}
          />
          <SurfaceStage {...props} />
        </main>
        {showSideRail ? (
          <aside className="side-rail" aria-label="Tax intake status">
            {props.viewState === "recommendation" ? (
              <ReadinessStatus readiness={props.readiness} missingFacts={props.missingFacts} />
            ) : null}
            {hasFacts ? <ConfirmedFactsRail facts={props.knownFacts} /> : null}
            {hasDocuments ? (
              <DocumentUploadCard onUpload={props.onUpload} documentItems={props.documentItems} />
            ) : null}
          </aside>
        ) : null}
      </div>
    </LayoutGroup>
  );
}

function SurfaceStage(props: RegistryProps) {
  const surface = props.surface;
  const reduce = useReducedMotion();

  if (!surface?.components.length) return null;

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

function ProgressStrip({
  knownFacts,
  readiness,
  missingFacts
}: {
  knownFacts: Record<string, unknown>;
  readiness: ReadinessState;
  missingFacts: string[];
}) {
  const completed = Math.min(Object.keys(knownFacts).length, 3);
  const activeQuestion = completed + 1;
  const ready = readiness.includes("Ready");

  return (
    <motion.section layout className="progress-strip" aria-label="Intake progress">
      <div>
        <span className="progress-kicker">{ready ? "Payoff" : `Question ${activeQuestion}`}</span>
        <strong>{readiness}</strong>
      </div>
      <div className="progress-segments" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <span
            key={index}
            className={ready || index < completed ? "is-complete" : index === completed ? "is-active" : ""}
          />
        ))}
      </div>
      <p>
        {ready
          ? "Your recommendation is assembled from the facts you confirmed."
          : missingFacts.length
          ? `${missingFacts.length} detail${missingFacts.length === 1 ? "" : "s"} still needed`
          : "We have enough to keep moving."}
      </p>
    </motion.section>
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
  if (component.component === "RecommendationWorkbench") {
    return <RecommendationWorkbench component={component} {...props} />;
  }
  if (component.component === "DocumentFieldReviewCard") {
    return <DocumentFieldReviewCard documentItems={props.documentItems} />;
  }
  return null;
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
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
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

function RecommendationWorkbench({
  component,
  knownFacts,
  candidateEntities,
  documentItems
}: RegistryProps & {
  component: A2UIComponent;
}) {
  const props = component.props ?? {};
  const recommendation = asString(props.recommendation, candidateEntities[0] ?? "Recommended tax path");
  const headline = asString(props.headline, "We have a recommendation.");
  const body = asString(props.body, "The key details are clear enough to prepare a recommendation.");
  const insights = stringList(props.insights, recommendationInsights(knownFacts, candidateEntities));
  const assumptions = stringList(props.assumptions, recommendationAssumptions(knownFacts));
  const nextSteps = stringList(props.nextSteps, [
    "Save the facts that led to this recommendation.",
    "Confirm the recommendation with a tax professional before filing or making an election."
  ]);
  const profile = profileProps(props.profile);
  const factEntries = Object.entries(knownFacts);
  const reviewedItems = documentItems.filter((item) => !item.needs_review).length;
  const needsReviewItems = documentItems.filter((item) => item.needs_review).length;
  const stats = [
    { value: factEntries.length, label: "Facts used" },
    { value: candidateEntities.filter((candidate) => candidate !== "Cannot Determine Yet").length || 1, label: "Tax paths" },
    { value: documentItems.length, label: "Docs reviewed" }
  ];

  return (
    <motion.section
      layout
      className="recommendation-workbench"
      aria-labelledby={`${component.id}-headline`}
    >
      <ConfettiBurst />
      <div className="recommendation-hero">
        <span className="status confident">
          <Sparkles size={16} />
          Ready for recommendation
        </span>
        <h2 id={`${component.id}-headline`}>{headline}</h2>
        <p>{body}</p>
        <div className="verdict-panel">
          <span>Likely path</span>
          <strong>{recommendation}</strong>
        </div>
      </div>

      <div className="payoff-grid">
        <div className="payoff-main">
          <div className="dynamic-stat-row" aria-label="Recommendation inputs">
            {stats.map((stat) => (
              <motion.div layout className="dynamic-stat" key={stat.label}>
                <strong>{stat.value}</strong>
                <span>{stat.label}</span>
              </motion.div>
            ))}
          </div>

          <PayoffPanel title="Why this fits" items={insights} tone="teal" />

          <div className="fact-pattern-panel">
            <div className="panel-title">Facts A2UI used</div>
            <div className="fact-token-grid">
              {factEntries.map(([key, value]) => (
                <motion.div layout className="fact-token" key={key}>
                  <span>{pretty(key)}</span>
                  <strong>{String(value)}</strong>
                </motion.div>
              ))}
            </div>
          </div>

          <div className="micro-panel-grid">
            <PayoffPanel title="What could change this" items={assumptions} tone="amber" />
            <PayoffPanel title="Next steps" items={nextSteps} tone="rose" />
          </div>

          {documentItems.length ? (
            <div className="document-signal-panel">
              <div>
                <div className="panel-title">Document signal</div>
                <strong>
                  {reviewedItems} reviewed, {needsReviewItems} needing a look
                </strong>
              </div>
              <DocumentFieldReviewCard documentItems={documentItems} />
            </div>
          ) : null}
        </div>

        <AdvisorProfileCard profile={profile} factsUsed={factEntries.length} />
      </div>
    </motion.section>
  );
}

function PayoffPanel({
  title,
  items,
  tone
}: {
  title: string;
  items: string[];
  tone: "teal" | "amber" | "rose";
}) {
  return (
    <motion.div layout className={`payoff-panel payoff-${tone}`}>
      <div className="panel-title">{title}</div>
      <ul>
        {items.map((item) => (
          <li key={item}>
            <CheckCircle size={16} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </motion.div>
  );
}

function AdvisorProfileCard({
  profile,
  factsUsed
}: {
  profile: AdvisorProfile;
  factsUsed: number;
}) {
  return (
    <motion.aside layout className="advisor-profile-card" aria-label="Tax advisor profile">
      <img src={profile.avatar} alt="" className="advisor-avatar" />
      <div className="advisor-info">
        <h3>{profile.name}</h3>
        <span>
          <BadgeCheck size={16} />
          {profile.credential}
        </span>
      </div>
      <p>{profile.bio}</p>
      <div className="advisor-stats" aria-label="Advisor details">
        <div>
          <strong>{profile.years}</strong>
          <span>Years tax experience</span>
        </div>
        <div>
          <strong>CPA</strong>
          <span>Credential</span>
        </div>
        <div>
          <strong>{factsUsed}</strong>
          <span>Facts reviewed</span>
        </div>
      </div>
      <a className="advisor-primary-link" href={profile.website} target="_blank" rel="noreferrer">
        Get tax advice
        <ArrowUpRight size={17} />
      </a>
      <a className="advisor-secondary-link" href={profile.linkedin} target="_blank" rel="noreferrer">
        <ArrowUpRight size={16} />
        LinkedIn profile
      </a>
    </motion.aside>
  );
}

function ConfettiBurst() {
  return (
    <div className="confetti-burst" aria-hidden="true">
      {Array.from({ length: 120 }).map((_, index) => (
        <span
          key={index}
          style={
            {
              "--x": `${(index % 24) * 4.35 + 1}%`,
              "--delay": `${(index % 18) * 0.025}s`,
              "--fall": `${900 + (index % 10) * 54}px`,
              "--drift": `${((index % 13) - 6) * 13}px`,
              "--spin": `${180 + (index % 9) * 55}deg`
            } as CSSProperties
          }
        />
      ))}
    </div>
  );
}

type AdvisorProfile = {
  avatar: string;
  name: string;
  credential: string;
  bio: string;
  years: number;
  website: string;
  linkedin: string;
};

function profileProps(value: unknown): AdvisorProfile {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  return {
    avatar: asString(record.avatar, "/images/john-mark-wendler.jpg"),
    name: asString(record.name, "John Mark Wendler"),
    credential: asString(record.credential, "Certified Public Accountant"),
    bio: asString(record.bio, "17 years of tax experience helping business owners make careful filing decisions."),
    years: typeof record.years === "number" ? record.years : 17,
    website: asString(record.website, "https://www.johnmarkwendler.com"),
    linkedin: asString(record.linkedin, "https://linkedin.com/in/johnmarkwendler")
  };
}

function stringList(value: unknown, fallback: string[]) {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : fallback;
}

function recommendationInsights(
  knownFacts: Record<string, unknown>,
  candidateEntities: string[]
) {
  const insights = [];
  const structure = asString(knownFacts.business_structure);
  const owners = asString(knownFacts.owner_count);
  const election = asString(knownFacts.s_corp_election_status);
  if (structure) insights.push(`Your business setup is marked as ${structure}.`);
  if (owners) insights.push(`Ownership is marked as ${owners.toLowerCase()}.`);
  if (election) insights.push(`S-Corp election status is ${election.toLowerCase()}.`);
  if (candidateEntities.length) insights.push(`The matching path is ${candidateEntities.join(" or ")}.`);
  return insights.length ? insights : ["The facts you confirmed are enough to prepare a likely tax path."];
}

function recommendationAssumptions(knownFacts: Record<string, unknown>) {
  const assumptions = [
    "This is based only on the facts provided in this session.",
    "State filing rules and late elections may change the next action."
  ];
  if (asString(knownFacts.s_corp_election_status).toLowerCase() === "not sure") {
    assumptions.unshift("The S-Corp election should be confirmed before relying on this path.");
  }
  return assumptions;
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
            : "Nothing's blocking us yet"}
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
              Anything you confirm shows up here
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
        <UploadCloud size={18} />
        <span>Upload document</span>
      </label>
      <DocumentFieldReviewCard documentItems={documentItems} />
    </motion.section>
  );
}

function DocumentFieldReviewCard({ documentItems }: { documentItems: DocumentReviewItem[] }) {
  if (!documentItems.length) {
    return <p className="empty-copy">Anything you upload becomes editable below</p>;
  }
  return (
    <motion.div layout className="doc-list">
      {documentItems.map((item) => (
        <motion.div
          layout
          key={`${item.field_label}:${item.extracted_value}`}
          className="doc-row"
        >
          <span>
            <FileText size={16} />
            {item.field_label}
          </span>
          <strong>{item.extracted_value}</strong>
          <small>
            {!item.needs_review ? <CheckCircle size={14} /> : null}
            {item.needs_review ? "Needs review" : item.confidence_state}
          </small>
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
