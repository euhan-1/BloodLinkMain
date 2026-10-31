// What the notification bell shows, as plain functions (the component only draws it): the badge, how long ago, and the list's
// bookkeeping. Kept apart from the component so scripts/check_notifications.ts can assert it.

import type { NotificationItem } from "./api.ts";
import { formatDateTime } from "./dateFormat.ts";

// The badge counts every unread notification the server holds, not the newest page. The exact number is shown up to this
// many; above it the badge says so ("99+") and the list's header still gives the exact count.
export const BADGE_MAX = 99;

export function badgeLabel(unread: number): string | null {
  if (unread <= 0) return null;
  return unread > BADGE_MAX ? `${BADGE_MAX}+` : String(unread);
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

// "Just now", "5 minutes ago", "3 hours ago", then the date written out ("30 Oct 2026, 20:27"), never "2d" and never the
// browser's locale.
export function agoText(iso: string, now: number = Date.now()): string {
  const minutes = Math.floor((now - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} ${plural(minutes, "minute", "minutes")} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${plural(hours, "hour", "hours")} ago`;
  return formatDateTime(iso);
}

// "12 unread" / "Nothing unread": the exact count, in the list's header.
export const unreadText = (unread: number): string => (unread <= 0 ? "Nothing unread" : `${unread} unread`);

// The bell polls only while the browser tab is visible, like the request lists and the chat, and asks again at once when the
// tab comes back.
export const shouldPoll = (visibility: string): boolean => visibility === "visible";

export const markOneRead = (items: NotificationItem[], id: number, at: string): NotificationItem[] =>
  items.map((n) => (n.id === id && n.read_at === null ? { ...n, read_at: at } : n));

export const markEveryRead = (items: NotificationItem[], at: string): NotificationItem[] =>
  items.map((n) => (n.read_at === null ? { ...n, read_at: at } : n));

// The id to ask for older rows with: the oldest one held. null when nothing is held.
export const oldestId = (items: NotificationItem[]): number | null => (items.length === 0 ? null : Math.min(...items.map((n) => n.id)));

// A page the server filled to the limit may have more behind it; a short page is the end.
export const mayHaveMore = (pageLength: number, limit: number): boolean => pageLength >= limit;

// Older rows appended below what is held, each id once.
export function appendOlder(items: NotificationItem[], older: NotificationItem[]): NotificationItem[] {
  const held = new Set(items.map((n) => n.id));
  return [...items, ...older.filter((n) => !held.has(n.id))];
}

// A new arrival is a newest id the bell has not seen; a count that merely changed (something was read elsewhere) is not one.
export const isArrival = (knownLatestId: number | null, latestId: number): boolean => knownLatestId !== null && latestId > knownLatestId;
