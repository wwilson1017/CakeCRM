import { createContext, useContext } from 'react';
export const VoiceLivePreviewContext = createContext(false);
export const VoiceLivePreviewSetterContext = createContext<(on: boolean) => void>(() => undefined);
export function useVoiceLivePreview(): boolean { return useContext(VoiceLivePreviewContext); }
export function useSetVoiceLivePreview() { return useContext(VoiceLivePreviewSetterContext); }
/** Shared setup snapshot; unknown readiness hides the mic. */
export const VoiceReadyContext = createContext<boolean | null>(null);
export function useVoiceReady(): boolean { return useContext(VoiceReadyContext) === true; }
