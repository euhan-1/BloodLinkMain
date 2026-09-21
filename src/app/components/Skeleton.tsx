// A calm, static-shaped placeholder for content that's still loading —
// deliberately using Tailwind's built-in animate-pulse (a slow, gentle
// opacity fade) rather than a shimmer/sweep effect, to match a healthcare
// tool's restrained motion language.
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-gray-200 ${className}`} />;
}
