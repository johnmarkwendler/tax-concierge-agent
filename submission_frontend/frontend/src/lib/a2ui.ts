import type { A2UIMessage, A2UISurface } from "./types";

export const SUPPORTED_COMPONENTS = new Set([
  "SegmentedChoiceCards",
  "SecurityReviewCard",
  "RecommendationCard",
  "RecommendationWorkbench",
  "DocumentFieldReviewCard"
]);

export function applyA2UIMessages(messages: A2UIMessage[]): A2UISurface[] {
  const surfaces = new Map<string, A2UISurface>();

  for (const message of messages) {
    const surface = getOrCreateSurface(surfaces, message);
    if (message.message === "createSurface") {
      surface.root = message.root ?? surface.root;
      surface.catalogId = message.catalogId ?? surface.catalogId;
    }
    if (message.message === "updateDataModel") {
      surface.data = message.data ?? {};
    }
    if (message.message === "updateComponents") {
      surface.components = message.components ?? [];
    }
  }

  return [...surfaces.values()];
}

export function activeSurface(surfaces: A2UISurface[]): A2UISurface | null {
  return surfaces.find((surface) => surface.components.length > 0) ?? surfaces.at(-1) ?? null;
}

function getOrCreateSurface(
  surfaces: Map<string, A2UISurface>,
  message: A2UIMessage
): A2UISurface {
  const existing = surfaces.get(message.surfaceId);
  if (existing) return existing;

  const surface: A2UISurface = {
    surfaceId: message.surfaceId,
    catalogId: message.catalogId,
    root: message.root ?? "",
    data: {},
    components: []
  };
  surfaces.set(message.surfaceId, surface);
  return surface;
}
