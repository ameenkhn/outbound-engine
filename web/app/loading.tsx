// Shown instantly on every navigation while the server component fetches data.
// Turns a "frozen" click into immediate visual feedback (skeleton placeholders).
export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="h-7 w-44 animate-pulse rounded-md bg-card" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="card h-20 animate-pulse" />
        ))}
      </div>
      <div className="card h-56 animate-pulse" />
      <div className="card h-72 animate-pulse" />
    </div>
  );
}
