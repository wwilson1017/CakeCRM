/**
 * Web Push subscription helpers (issue #6).
 *
 * Registers the service worker, requests permission, fetches the server VAPID
 * public key, and subscribes/unsubscribes — all native browser APIs (no npm
 * deps). Ported from Chatty's pushSubscription.ts, endpoints unchanged.
 *
 * `resyncPushSubscription` (issue #192) is the self-heal that makes push subscriptions
 * per-seat without the migration having to guess who owns a legacy endpoint. See its
 * own comment.
 */

import { api } from '../api/client';

export function isPushSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

export function getPushPermissionState(): NotificationPermission | 'unsupported' {
  if (!isPushSupported()) return 'unsupported';
  return Notification.permission;
}

export async function isSubscribed(): Promise<boolean> {
  if (!isPushSupported()) return false;
  try {
    const registration = await navigator.serviceWorker.getRegistration('/sw.js');
    if (!registration) return false;
    return (await registration.pushManager.getSubscription()) !== null;
  } catch {
    return false;
  }
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

/**
 * POST a subscription to the server, which upserts it by endpoint and stamps the
 * signed-in seat as its owner (#192). One definition on purpose: the first subscribe
 * and the self-heal re-POST must send the SAME body, or an endpoint could be created
 * with one shape and re-stamped with another.
 */
async function postSubscription(subscription: PushSubscription): Promise<void> {
  const sub = subscription.toJSON();
  await api('/api/notifications/push/subscribe', {
    method: 'POST',
    body: JSON.stringify({
      endpoint: sub.endpoint,
      keys: sub.keys,
      user_agent: navigator.userAgent,
    }),
  });
}

export async function subscribeToPush(): Promise<boolean> {
  if (!isPushSupported()) return false;
  try {
    const registration = await navigator.serviceWorker.register('/sw.js');
    await navigator.serviceWorker.ready;

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return false;

    const { public_key } = await api<{ public_key: string }>('/api/notifications/push/vapid-public-key');
    const applicationServerKey = urlBase64ToUint8Array(public_key);

    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey.buffer as ArrayBuffer,
    });

    await postSubscription(subscription);
    return true;
  } catch (err) {
    console.error('Push subscription failed:', err);
    return false;
  }
}

export async function unsubscribeFromPush(): Promise<boolean> {
  if (!isPushSupported()) return false;
  try {
    const registration = await navigator.serviceWorker.getRegistration('/sw.js');
    if (!registration) return true;

    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return true;

    const endpoint = subscription.endpoint;
    await subscription.unsubscribe();
    await api('/api/notifications/push/unsubscribe', {
      method: 'POST',
      body: JSON.stringify({ endpoint }),
    });
    return true;
  } catch (err) {
    console.error('Push unsubscribe failed:', err);
    return false;
  }
}

/**
 * Re-POST an already-granted subscription so the server re-stamps its owner (#192).
 *
 * `push_subscriptions.user_id` is nullable and the #192 migration deliberately does NOT
 * claim legacy rows: an endpoint records a browser, not a person, so claiming them for
 * the admin could push the admin's targeted notifications to a member's browser. An
 * unstamped row therefore receives broadcasts and nothing else — correct, but degraded.
 *
 * This restores it with no user action. The subscribe endpoint is an upsert that sets
 * `user_id = EXCLUDED.user_id`, so simply re-sending the subscription this browser
 * already holds binds it to whoever is signed in right now. CrmLayout calls this on
 * every authenticated load and whenever the signed-in account changes, which also
 * covers a shared browser being handed to a colleague.
 *
 * Deliberately silent and side-effect-free otherwise: it NEVER prompts for permission
 * and never creates a subscription (that stays the Settings toggle's job), so a user who
 * has not opted into push is untouched. Every failure is swallowed — a self-heal that
 * surfaced a toast on a flaky network would be worse than the stale stamp it fixes.
 */
export async function resyncPushSubscription(): Promise<void> {
  if (!isPushSupported() || Notification.permission !== 'granted') return;
  try {
    const registration = await navigator.serviceWorker.getRegistration('/sw.js');
    if (!registration) return;
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return;

    await postSubscription(subscription);
  } catch {
    /* best-effort: the endpoint keeps its previous owner until the next load */
  }
}
