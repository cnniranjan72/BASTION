import type { ComponentType, ReactNode, SVGProps } from "react";
import { EmptyBoxIcon } from "./icons";

interface EmptyStateProps {
  icon?: ComponentType<SVGProps<SVGSVGElement>>;
  title: string;
  children?: ReactNode;
}

// Standard "nothing here yet" block — an icon plus a short heading reads as
// an intentional state, not a page that failed to load. Used instead of a
// single line of plain text floating in an otherwise-empty page.
export function EmptyState({ icon: Icon = EmptyBoxIcon, title, children }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <Icon width={28} height={28} className="empty-state__icon" />
      <p className="empty-state__title">{title}</p>
      {children && <p className="empty-state__body">{children}</p>}
    </div>
  );
}
