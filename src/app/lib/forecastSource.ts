// Which mechanism produced one blood type's forecast, for the small tag in the Dashboard's
// per-type outlook. The tag names the mechanism and nothing more.

export type ForecastSourceTag = { text: string; title: string };

type TagInput = {
  forecast_source: string;
  pending_types?: string[];
  sarimax_orders?: Record<string, string>;
};

// The bracket suffix of each label in /forecast's sarimax_orders, and the words shown for it. Mirrors
// ORDER_SOURCE_SUFFIX in server/main.py; the two must stay in sync (the server also normalizes
// older cache rows into these three spellings before they reach the API).
const ORDER_SOURCE_TEXT: Record<string, string> = {
  "grid-searched": "grid-searched",
  "ai-recommended": "AI-recommended",
  "default-order": "default order",
};

const LINEAR_TREND_TAG: ForecastSourceTag = {
  text: "linear trend",
  title: "Linear trend: a straight-line projection from this blood type's recorded stock, used instead of a SARIMAX model.",
};

// null = no tag: the type is still being calculated, the forecast is not a real-history one, or the
// API did not say which order was used.
export function forecastSourceTag(view: TagInput, bloodType: string): ForecastSourceTag | null {
  if ((view.pending_types ?? []).includes(bloodType)) return null;
  if (view.forecast_source === "linear_trend") return LINEAR_TREND_TAG;
  if (view.forecast_source !== "sarimax_facility_history" || !view.sarimax_orders) return null;
  const label = view.sarimax_orders[bloodType];
  // sarimax_orders lists every type that has a SARIMAX forecast; a type missing from it is on the linear trend.
  if (label === undefined) return LINEAR_TREND_TAG;
  const mechanism = label.match(/\[([a-z-]+)\]\s*$/)?.[1];
  const text = mechanism === undefined ? undefined : ORDER_SOURCE_TEXT[mechanism];
  return text === undefined ? null : { text, title: label };
}
