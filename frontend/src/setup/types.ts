/**
 * CakeCRM — AI provider setup types.
 * Shapes mirror the normalized /api/providers backend contract. `profiles` is
 * keyed by BARE provider name ("anthropic", not "anthropic:default").
 */

export interface ProviderProfile {
  type: string; // 'api_key' | 'ollama_local'
  configured: boolean;
  key_preview?: string;
  base_url?: string;
}

export interface ProviderStatus {
  active_provider: string;
  active_model: string;
  is_railway?: boolean;
  profiles: Record<string, ProviderProfile>;
}

export interface ModelsResponse {
  provider: string;
  models: string[];
}

export interface TiersResponse {
  active_provider: string;
  tier_models: Record<string, Record<string, string>>;
  tier_labels: Record<string, Record<string, string>>;
  auto_triage_providers: string[];
}

export interface OllamaStatusResponse {
  reachable: boolean;
  models: string[];
}
