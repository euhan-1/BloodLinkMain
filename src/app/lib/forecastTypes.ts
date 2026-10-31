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
  // Present when the type has fewer recorded days than SARIMAX needs: the recommendation stands, marked as resting on that.
  limited_history?: LimitedHistory;
};

// A type whose projection holds at or above its minimum but whose 95% interval's lower bound drops below it (see
// _compute_downside_risk in main.py). Display only: it is never a recommendation and never an alert.
export type DownsideRisk = {
  blood_type: string;
  risk_date: string; // the earliest checkpoint whose lower bound is below the minimum
  days_until_risk: number;
  lower_units: number;
};

// Too few recorded days for the forecast to say anything about this type (see _limited_history in main.py).
export type LimitedHistory = { observed_days: number; min_days_required: number };
export type InsufficientHistory = LimitedHistory & { blood_type: string };

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
  // Absent from an older API and from the synthetic stand-in and "none" answers, which have no interval: absent reads as none.
  downside_risks?: DownsideRisk[];
  // Types with no recommendation and too few recorded days: never "on track", never "at risk". Absent from an older API.
  insufficient_history?: InsufficientHistory[];
  // {blood_type: points}: the same checkpoints and interval as `series`, per type instead of summed (lower/upper null: no interval).
  type_bounds?: Record<string, SeriesPoint[]>;
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
