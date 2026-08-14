interface AreaChartProps {
  points: number[];
  labels: string[];
  color: string;
  formatValue?: (v: number) => string;
}

// Small hand-rolled SVG area chart — the data volumes on Analytics are
// daily-bucketed traces (tens to low hundreds of points at most), nowhere
// near where a charting library's virtualization would earn its bundle cost.
export function AreaChart({ points, labels, color, formatValue }: AreaChartProps) {
  const width = 600;
  const height = 160;
  const padding = 8;
  const max = Math.max(...points, 1);

  const stepX = points.length > 1 ? (width - padding * 2) / (points.length - 1) : 0;
  const coords = points.map((v, i) => {
    const x = padding + i * stepX;
    const y = height - padding - (v / max) * (height - padding * 2);
    return [x, y] as const;
  });

  const linePath = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ");
  const areaPath = `${linePath} L${coords[coords.length - 1]?.[0] ?? padding},${height - padding} L${padding},${height - padding} Z`;

  return (
    <svg
      className="area-chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
    >
      <defs>
        <linearGradient id={`area-fill-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#area-fill-${color.replace("#", "")})`} />
      <path d={linePath} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
      {coords.map(([x, y], i) => {
        const pointValue = points[i] ?? 0;
        return (
          <circle key={i} cx={x} cy={y} r="2.5" fill={color}>
            <title>
              {labels[i]}: {formatValue ? formatValue(pointValue) : pointValue}
            </title>
          </circle>
        );
      })}
    </svg>
  );
}
