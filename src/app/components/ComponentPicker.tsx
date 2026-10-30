import { BLOOD_COMPONENTS, type BloodComponent } from "../lib/bloodComponents";

// Which component is being asked for. Nothing is pre-selected: release reserves units of exactly this component, so the
// requester chooses it (the same rule as the reason for a request). The nearby search waits for it too.
export function ComponentPicker({ value, onChange }: { value: BloodComponent | null; onChange: (c: BloodComponent) => void }) {
  return (
    <fieldset>
      <legend className="text-[14px] font-semibold text-foreground mb-1.5">Component needed</legend>
      <div role="radiogroup" aria-required="true" className="flex gap-1.5">
        {BLOOD_COMPONENTS.map((c) => (
          <button
            key={c}
            type="button"
            role="radio"
            aria-checked={value === c}
            onClick={() => onChange(c)}
            className={`h-10 px-4 rounded-md border text-[14px] font-semibold transition-colors ${
              value === c
                ? "bg-primary text-white border-primary"
                : "bg-card text-foreground border-border hover:bg-secondary"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
    </fieldset>
  );
}
