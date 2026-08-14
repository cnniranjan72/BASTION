interface BarListItem {
  label: string;
  value: number;
}

export function BarList({ items, color }: { items: BarListItem[]; color: string }) {
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="bar-list">
      {items.map((item) => (
        <div key={item.label} className="bar-list__row">
          <span className="bar-list__label">{item.label}</span>
          <div className="bar-list__track">
            <div
              className="bar-list__fill"
              style={{ width: `${(item.value / max) * 100}%`, background: color }}
            />
          </div>
          <span className="bar-list__value">{item.value}</span>
        </div>
      ))}
    </div>
  );
}
