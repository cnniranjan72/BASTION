export function TableSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton skeleton-row" style={{ animationDelay: `${i * 80}ms` }} />
      ))}
    </div>
  );
}
