import { createContext, useContext } from 'react';
/** Shared setup snapshot; unknown readiness hides the mic. */
export const VoiceReadyContext = createContext<boolean | null>(null);
export function useVoiceReady(): boolean { return useContext(VoiceReadyContext) === true; }
