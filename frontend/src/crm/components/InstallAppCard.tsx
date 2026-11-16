/**
 * InstallAppCard — "Install on your phone" (#375, port of todo-gtd 809a1f8's InstallGuide).
 *
 * Step-by-step home-screen install for iPhone Safari, Android Chrome and desktop Chrome/Edge,
 * the list for this device open. Member-visible: it calls no route. The native prompt
 * (`beforeinstallprompt`) usually fires before this lazy page loads, so index.html holds it
 * on `window.__installPrompt`; the card also listens while mounted. `InstallSteps` is shared
 * with TodoDevicesCard, which shows only the phone lists.
 */
import { useEffect, useState } from 'react';

import { FONT_SANS, INK, INK_MUTE } from '../../shared/styles';
import { btnPrimary } from '../styles';
import { SettingsCard } from './SettingsCard';

type Device = 'iphone' | 'android' | 'computer';
interface BeforeInstallPromptEvent extends Event { prompt(): Promise<void> }
type InstallWindow = Window & { __installPrompt?: BeforeInstallPromptEvent | null };

const STEPS: Record<Device, { label: string; steps: string[] }> = {
  iphone: {
    label: 'iPhone or iPad (Safari)',
    steps: [
      "Tap the Share button: the square with an arrow pointing up. If you don't see it, tap ••• first.",
      'Scroll down and tap “Add to Home Screen”.',
      'Leave “Open as Web App” on, if you see it, and tap Add.',
    ],
  },
  android: {
    label: 'Android (Chrome)',
    steps: [
      'Tap the ⋮ menu at the right of the address bar.',
      'Tap “Install and create shortcut” (older Chrome: “Add to Home screen” or “Install app”).',
      'Tap Install.',
    ],
  },
  computer: {
    label: 'Computer (Chrome or Edge)',
    steps: [
      'Click the install icon at the right end of the address bar.',
      'Click Install.',
    ],
  },
};

function thisDevice(): Device {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'iphone';
  return /Android/.test(ua) ? 'android' : 'computer';
}

function installed(): boolean {
  return (typeof window.matchMedia === 'function' && window.matchMedia('(display-mode: standalone)').matches)
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

const text = { fontFamily: FONT_SANS, fontSize: 13, color: INK, lineHeight: 1.6 };

export function InstallSteps({ devices = ['iphone', 'android', 'computer'], open = thisDevice() }: {
  devices?: Device[];
  open?: Device;
}) {
  return (
    <div style={{ ...text, display: 'grid', gap: 8, marginTop: 12 }}>
      {devices.map(device => (
        <details key={device} open={device === open} style={{
          border: '1px solid var(--color-ck-line)', borderRadius: 8,
          background: 'var(--color-ck-bg)', padding: '10px 12px',
        }}>
          <summary style={{ cursor: 'pointer', fontWeight: 600 }}>{STEPS[device].label}</summary>
          <ol style={{ margin: '8px 0 0', paddingLeft: 20 }}>
            {STEPS[device].steps.map(step => <li key={step}>{step}</li>)}
          </ol>
        </details>
      ))}
    </div>
  );
}

export function InstallAppCard({ isMobile }: { isMobile: boolean }) {
  const [prompt, setPrompt] = useState(() => (window as InstallWindow).__installPrompt ?? null);
  const [isInstalled, setInstalled] = useState(installed);
  const device = thisDevice();

  useEffect(() => {
    function available(event: Event) {
      event.preventDefault();
      const held = event as BeforeInstallPromptEvent;
      (window as InstallWindow).__installPrompt = held;
      setPrompt(held);
    }
    function complete() {
      (window as InstallWindow).__installPrompt = null;
      setPrompt(null);
      setInstalled(true);
    }
    window.addEventListener('beforeinstallprompt', available);
    window.addEventListener('appinstalled', complete);
    return () => {
      window.removeEventListener('beforeinstallprompt', available);
      window.removeEventListener('appinstalled', complete);
    };
  }, []);

  async function install() {
    if (!prompt) return;
    // Consume before awaiting: a double click must never prompt the same event twice.
    (window as InstallWindow).__installPrompt = null;
    setPrompt(null);
    try {
      await prompt.prompt();
    } catch {
      // A refused or unavailable native prompt leaves the manual steps available.
    }
  }

  return (
    <SettingsCard
      id="install_app"
      title="Install on your phone"
      description="CakeCRM works best as an app on your phone's home screen. It opens full screen like any other app, and there is nothing to download from an app store."
      isMobile={isMobile}
    >
      <div style={text}>
        {isInstalled && <p>✓ You're using the installed app on this device.</p>}
        {prompt && !isInstalled && (
          <button type="button" style={btnPrimary} onClick={() => void install()}>Install CakeCRM</button>
        )}
        {device === 'computer' ? (
          <ol style={{ paddingLeft: 20 }}>
            <li>Open CakeCRM on your phone: go to <strong style={{ overflowWrap: 'anywhere' }}>{location.origin}</strong> in its browser and sign in.</li>
            <li>On your phone, add it to your home screen:</li>
          </ol>
        ) : <p>On this phone, add it to your home screen:</p>}
        <InstallSteps open={device} />
        <p style={{ color: INK_MUTE, marginBottom: 0 }}>From then on, open it from its icon on your home screen, not from the browser.</p>
      </div>
    </SettingsCard>
  );
}
