import type { ShoppingRange } from '@/features/shopping/api'

const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/

/** Local calendar date as YYYY-MM-DD; toISOString would shift it across UTC midnight. */
export function toIsoDate(date: Date): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function parseIsoDate(value: string): Date | null {
  const match = ISO_DATE_RE.exec(value)
  if (!match) return null
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return toIsoDate(date) === value ? date : null
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

function mondayOf(date: Date): Date {
  return addDays(date, -((date.getDay() + 6) % 7))
}

export type PresetId = 'this-week' | 'next-7-days' | 'next-week'

export const PRESETS: { id: PresetId; label: string; range: (today: Date) => ShoppingRange }[] = [
  {
    id: 'this-week',
    label: 'This week',
    range: (today) => {
      const monday = mondayOf(today)
      return { from: toIsoDate(monday), to: toIsoDate(addDays(monday, 6)) }
    },
  },
  {
    id: 'next-7-days',
    label: 'Next 7 days',
    range: (today) => ({ from: toIsoDate(today), to: toIsoDate(addDays(today, 6)) }),
  },
  {
    id: 'next-week',
    label: 'Next week',
    range: (today) => {
      const monday = addDays(mondayOf(today), 7)
      return { from: toIsoDate(monday), to: toIsoDate(addDays(monday, 6)) }
    },
  },
]

export const DEFAULT_PRESET: PresetId = 'next-7-days'

export function presetFor(range: ShoppingRange, today: Date): PresetId | null {
  return PRESETS.find((p) => {
    const r = p.range(today)
    return r.from === range.from && r.to === range.to
  })?.id ?? null
}

/** Mirrors the backend's RANGE_MAX_DAYS so an oversized range fails before the request. */
export const RANGE_MAX_DAYS = 62

export function rangeError(range: ShoppingRange): string | null {
  const from = parseIsoDate(range.from)
  const to = parseIsoDate(range.to)
  if (!from || !to) return 'Pick a start and an end date'
  if (to < from) return 'The end date is before the start date'
  const days = Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1
  if (days > RANGE_MAX_DAYS) return `Pick at most ${RANGE_MAX_DAYS} days`
  return null
}

const dayFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})

export function formatDay(value: string): string {
  const date = parseIsoDate(value)
  return date ? dayFormat.format(date) : value
}

export function formatRange(from: string, to: string): string {
  return from === to ? formatDay(from) : `${formatDay(from)} – ${formatDay(to)}`
}
