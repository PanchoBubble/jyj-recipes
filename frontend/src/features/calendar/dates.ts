/** Calendar dates are plain `YYYY-MM-DD` strings in the household's local time. */
export type IsoDate = string

const DAY_MS = 86_400_000
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/

function pad(n: number) {
  return String(n).padStart(2, '0')
}

export function toIsoDate(date: Date): IsoDate {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function todayIso(): IsoDate {
  return toIsoDate(new Date())
}

export function isIsoDate(value: string | null | undefined): value is IsoDate {
  const m = value ? ISO_RE.exec(value) : null
  if (!m) return false
  const utc = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]))
  return utc.getUTCMonth() === +m[2] - 1 && utc.getUTCDate() === +m[3]
}

// UTC arithmetic so DST changes never shift a day.
function toUtc(iso: IsoDate) {
  const [y, m, d] = iso.split('-').map(Number)
  return Date.UTC(y, m - 1, d)
}

function fromUtc(ms: number): IsoDate {
  const date = new Date(ms)
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`
}

export function addDays(iso: IsoDate, days: number): IsoDate {
  return fromUtc(toUtc(iso) + days * DAY_MS)
}

/** Monday of the week containing `iso`. */
export function startOfWeek(iso: IsoDate): IsoDate {
  const weekday = new Date(toUtc(iso)).getUTCDay()
  return addDays(iso, -((weekday + 6) % 7))
}

export function weekDays(monday: IsoDate): IsoDate[] {
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i))
}

function asUtcDate(iso: IsoDate) {
  return new Date(toUtc(iso))
}

const dayFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  timeZone: 'UTC',
})
const longDayFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  timeZone: 'UTC',
})
const shortFormat = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  timeZone: 'UTC',
})

export function formatDay(iso: IsoDate) {
  return dayFormat.format(asUtcDate(iso))
}

export function formatLongDay(iso: IsoDate) {
  return longDayFormat.format(asUtcDate(iso))
}

export function formatWeekRange(monday: IsoDate) {
  return `${shortFormat.format(asUtcDate(monday))} – ${shortFormat.format(asUtcDate(addDays(monday, 6)))}`
}
