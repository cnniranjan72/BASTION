interface DonutGaugeProps {
  fraction: number;
  color: string;
  trackColor: string;
  label: string;
  centerValue: string;
}

export function DonutGauge({ fraction, color, trackColor, label, centerValue }: DonutGaugeProps) {
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(1, fraction));
  const offset = circumference * (1 - clamped);

  return (
    <div className="donut-gauge">
      <svg viewBox="0 0 130 130" width="130" height="130">
        <circle cx="65" cy="65" r={radius} fill="none" stroke={trackColor} strokeWidth="14" />
        <circle
          cx="65"
          cy="65"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="14"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 65 65)"
          className="donut-gauge__arc"
        />
        <text x="65" y="61" textAnchor="middle" className="donut-gauge__value">
          {centerValue}
        </text>
        <text x="65" y="80" textAnchor="middle" className="donut-gauge__label">
          {label}
        </text>
      </svg>
    </div>
  );
}
