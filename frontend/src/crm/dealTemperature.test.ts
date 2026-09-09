import { describe, expect, it } from 'vitest';
import {
  DEAL_TEMPERATURES,
  nextTemperature,
  normalizeTemperature,
  temperatureLabel,
} from './dealTemperature';

describe('normalizeTemperature', () => {
  it('accepts the three tiers, case- and whitespace-insensitively', () => {
    expect(normalizeTemperature('hot')).toBe('hot');
    expect(normalizeTemperature(' WARM ')).toBe('warm');
    expect(normalizeTemperature('Cold')).toBe('cold');
  });

  it('reads every non-tier as null, never as a fourth state', () => {
    // The server's `_temperature_multiplier` fails an unknown value toward neutral too, so
    // the two ends agree about what an unrecognised column value means.
    for (const v of [null, undefined, '', '   ', 'cool', 'lukewarm', 'HOTT', '1'] as const) {
      expect(normalizeTemperature(v)).toBeNull();
    }
  });
});

describe('nextTemperature', () => {
  it('takes an untriaged deal straight to hot', () => {
    // One click from rest flags the deal you care about — the whole point of the control.
    expect(nextTemperature(null)).toBe('hot');
    expect(nextTemperature(undefined)).toBe('hot');
    expect(nextTemperature('')).toBe('hot');
  });

  it('cycles hot → warm → cold → not set, and back to hot', () => {
    expect(nextTemperature('hot')).toBe('warm');
    expect(nextTemperature('warm')).toBe('cold');
    expect(nextTemperature('cold')).toBeNull();
    expect(nextTemperature(nextTemperature('cold'))).toBe('hot');
  });

  it('returns to unset within one lap, so a mis-click is always recoverable here', () => {
    // The load-bearing difference from the blueprint, whose cycle never reaches unset and
    // needs a second surface to clear a value. This board has no such surface, so a cycle
    // that could not reach null would make an accidental Hot permanent from the card.
    const seen: (string | null)[] = [];
    let current: string | null = null;
    for (let i = 0; i < DEAL_TEMPERATURES.length + 1; i += 1) {
      current = nextTemperature(current);
      seen.push(current);
    }
    expect(seen).toEqual(['hot', 'warm', 'cold', null]);
  });

  it('normalises before stepping, so a stray stored value re-enters at hot', () => {
    expect(nextTemperature('cool')).toBe('hot');
  });
});

describe('temperatureLabel', () => {
  it('names the tier, and says "not set" rather than "cold" for an untriaged deal', () => {
    // The distinction the whole feature rests on: nobody has judged this deal is not the
    // same claim as somebody judged it cold.
    expect(temperatureLabel('hot')).toBe('Hot');
    expect(temperatureLabel('warm')).toBe('Warm');
    expect(temperatureLabel('cold')).toBe('Cold');
    expect(temperatureLabel(null)).toBe('not set');
    expect(temperatureLabel('cool')).toBe('not set');
  });
});
