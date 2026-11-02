import { createContext, useContext } from 'react';

/** Shared setup snapshot; unknown readiness hides the camera and image types. */
export const VisionReadyContext = createContext<boolean | null>(null);
export function useVisionReady(): boolean { return useContext(VisionReadyContext) === true; }
