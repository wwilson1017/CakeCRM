import type { FieldProvenance } from '../../core/types';
import { AiBadge } from './AiBadge';

// Thin CRM wrapper so detail views don't hand-map a provenance row (issue #16). Renders
// nothing when there is no live-badge row for the field.
interface ProvenanceBadgeProps {
  prov: FieldProvenance | undefined;
  onConfirm: () => void;
  confirming: boolean;
}

export function ProvenanceBadge({ prov, onConfirm, confirming }: ProvenanceBadgeProps) {
  if (!prov) return null;
  return (
    <AiBadge
      source={prov.source}
      sourceDetail={prov.source_detail}
      confidence={prov.confidence}
      onConfirm={onConfirm}
      confirming={confirming}
    />
  );
}
