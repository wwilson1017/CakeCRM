// @vitest-environment jsdom
//
// Scope: `resyncPushSubscription` only — the push-subscription self-heal (#192).
//
// This function is load-bearing for a decision made in SQL: the #192 migration
// deliberately leaves legacy `push_subscriptions.user_id` NULL, because claiming those
// endpoints for the admin could push the admin's targeted notifications to a member's
// browser. The whole reason that is safe rather than merely degraded is that this
// re-POST converges every active browser onto whoever is actually signed in. So the
// three properties pinned here are the ones the migration comment relies on:
//
//   1. it re-POSTs the existing subscription when one exists and permission is granted;
//   2. it NEVER prompts and never creates a subscription — a user who has not opted into
//      push must be untouched, and a permission prompt on every page load would be a
//      visible regression;
//   3. it swallows failures — a self-heal that threw would take out the CRM shell's
//      mount effect with it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../api/client', () => ({ api }));

const { resyncPushSubscription } = await import('./pushSubscription');

const SUB_JSON = {
  endpoint: 'https://fcm.googleapis.com/fcm/send/abc',
  keys: { p256dh: 'p', auth: 'a' },
};

function stubPush({ permission = 'granted', registration = true, subscription = true } = {}) {
  const getSubscription = vi.fn().mockResolvedValue(
    subscription ? { toJSON: () => SUB_JSON } : null);
  vi.stubGlobal('Notification', { permission });
  vi.stubGlobal('PushManager', function PushManager() {});
  vi.stubGlobal('navigator', {
    userAgent: 'TestUA',
    serviceWorker: {
      getRegistration: vi.fn().mockResolvedValue(
        registration ? { pushManager: { getSubscription } } : undefined),
    },
  });
  return { getSubscription };
}

beforeEach(() => { api.mockReset(); api.mockResolvedValue({ ok: true }); });
afterEach(() => { vi.unstubAllGlobals(); });

describe('resyncPushSubscription', () => {
  it('re-POSTs the existing subscription so the server re-stamps its owner', async () => {
    stubPush();
    await resyncPushSubscription();
    expect(api).toHaveBeenCalledTimes(1);
    const [path, init] = api.mock.calls[0];
    expect(path).toBe('/api/notifications/push/subscribe');
    expect(init.method).toBe('POST');
    // Same body shape the first subscribe sends — they share postSubscription, and an
    // endpoint created with one shape then re-stamped with another would be a silent
    // half-migration.
    expect(JSON.parse(init.body)).toEqual({
      endpoint: SUB_JSON.endpoint,
      keys: SUB_JSON.keys,
      user_agent: 'TestUA',
    });
  });

  it('does nothing when permission was never granted', async () => {
    stubPush({ permission: 'default' });
    await resyncPushSubscription();
    expect(api).not.toHaveBeenCalled();
  });

  it('does nothing when permission is denied', async () => {
    stubPush({ permission: 'denied' });
    await resyncPushSubscription();
    expect(api).not.toHaveBeenCalled();
  });

  it('does nothing when no service worker is registered', async () => {
    stubPush({ registration: false });
    await resyncPushSubscription();
    expect(api).not.toHaveBeenCalled();
  });

  it('does not create a subscription when this browser has none', async () => {
    // Granted permission with no subscription is a real state (the user disabled push
    // from Settings). Re-creating one here would silently re-enable it.
    stubPush({ subscription: false });
    await resyncPushSubscription();
    expect(api).not.toHaveBeenCalled();
  });

  it('is a no-op where Push is unsupported rather than throwing', async () => {
    vi.stubGlobal('Notification', { permission: 'granted' });
    vi.stubGlobal('navigator', { userAgent: 'TestUA' });   // no serviceWorker
    await expect(resyncPushSubscription()).resolves.toBeUndefined();
    expect(api).not.toHaveBeenCalled();
  });

  it('swallows a failed POST instead of rejecting into the caller', async () => {
    stubPush();
    api.mockRejectedValue(new Error('network'));
    await expect(resyncPushSubscription()).resolves.toBeUndefined();
  });
});
