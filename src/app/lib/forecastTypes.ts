// What GET /forecast returns for a blood bank (view "forecast"), shared by the Dashboard and the blood bank's dashboard view.
// A hospital's /forecast answer is a different shape (ThresholdView, in HospitalDashboard).

import type { HistoryPoint, SeriesPoint } from "./trendChart.ts";

export type ForecastAlert = {
  type: string;
  severity: "critical" | "warn";
  reason: string;
  days_until_threshold: number;
};

// Plain-language restock recommendation: the translated, staff-facing form of whatever the forecast math already produced.
// See _compute_restock_recommendation in main.py: a pure post-processing step over the point forecast, not a second model.
export type RestockRecommendation = {
  blood_type: string;
  breach_date: string;
  days_until_breach: number;
  recommended_units: number;
  // The projection's lowest point in the next 30 days: an estimate, shown beside current stock and never blended with it.
  // Absent from an older API.
  projected_low_units?: number;
};

export type ForecastView = {
  view: "forecast";
  has_sufficient_history: boolean;
  days_of_history: number;
  min_days_required: number;
  is_dengue_season: boolean;
  forecast_source: "sarimax_facility_history" | "linear_trend" | "synthetic_model_stand_in" | "none";
  synthetic_model_label?: string;
  // Only present when forecast_source is "sarimax_facility_history".
  sarimax_model_label?: string;
  sarimax_fallback_types?: string[];
  // {blood_type: "SARIMAX(p,d,q)x(P,D,Q,s)+dengue [grid-searched|ai-recommended|default-order]"} for every type that has a
  // SARIMAX forecast. A type missing from it is on the linear trend.
  sarimax_orders?: Record<string, string>;
  sarimax_min_days_required?: number;
  interval_confidence: number | null;
  restock_recommendations: RestockRecommendation[];
  // "pending" = some blood types are still being calculated and none of the ready ones needs action yet, so "on_track"
  // would be a claim the server can't make. null only on the "none" source.
  restock_status: "on_track" | "action_needed" | "pending" | null;
  // Blood types the server hasn't fitted yet (it fits a couple per request); the dashboard asks again until none are.
  pending_types?: string[];
  types_total?: number;
  series: SeriesPoint[];
  history?: HistoryPoint[];
  alerts: ForecastAlert[];
};

// True once the forecast is derived from this facility's own real history (SARIMAX once there is enough of it, a linear
// trend before that), as opposed to the synthetic stand-in or no data at all.
export function isRealForecastSource(source: ForecastView["forecast_source"]): boolean {
  return source === "sarimax_facility_history" || source === "linear_trend";
}
