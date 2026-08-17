const LAST_NOTIFIED_KEY = 'alchemical-tarot-last-notified';

export async function showDailyNudgeIfDue(notifyHour: number | null) {
  if (notifyHour === null) return;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;

  const now = new Date();
  const todayKey = now.toISOString().slice(0, 10);
  const lastNotified = localStorage.getItem(LAST_NOTIFIED_KEY);
  if (lastNotified === todayKey) return;
  if (now.getHours() < notifyHour) return;

  const title = 'A card is waiting';
  const options = {
    body: 'Take a breath, clear the space, and see what wants to be seen today.',
    icon: '/pwa-192x192.png',
    tag: 'daily-tarot-nudge',
  };

  if ('serviceWorker' in navigator) {
    const registration = await navigator.serviceWorker.ready.catch(() => null);
    if (registration) {
      await registration.showNotification(title, options);
      localStorage.setItem(LAST_NOTIFIED_KEY, todayKey);
      return;
    }
  }
  new Notification(title, options);
  localStorage.setItem(LAST_NOTIFIED_KEY, todayKey);
}

export function startNudgeWatcher(getHour: () => number | null) {
  const check = () => showDailyNudgeIfDue(getHour());
  check();
  const interval = setInterval(check, 5 * 60 * 1000);
  return () => clearInterval(interval);
}
