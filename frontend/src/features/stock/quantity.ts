import type { Dimension, StockLevel } from '@/features/ingredients/api'

// Amounts arrive as decimal strings on the backend's 3-place quantum. Arithmetic
// runs on BigInt thousandths so nothing is ever rounded through a float.
const SCALE = 1000n
const DECIMAL_RE = /^(-)?(\d+)(?:\.(\d+))?$/

export const AMOUNT_RE = /^\d+(?:[.,]\d{1,3})?$/

export function toMilli(value: string): bigint {
  const match = DECIMAL_RE.exec(value.trim())
  if (!match) throw new Error(`not a decimal: ${value}`)
  const [, sign, whole, frac = ''] = match
  const milli = BigInt(whole) * SCALE + BigInt(frac.slice(0, 3).padEnd(3, '0'))
  return sign ? -milli : milli
}

export function fromMilli(milli: bigint): string {
  const negative = milli < 0n
  const abs = negative ? -milli : milli
  const frac = (abs % SCALE).toString().padStart(3, '0')
  return `${negative ? '-' : ''}${abs / SCALE}.${frac}`
}

/** "1.500" -> "1.5", "200.000" -> "200". Formatting only, no rounding. */
export function trimAmount(value: string): string {
  if (!value.includes('.')) return value
  return value.replace(/\.?0+$/, '')
}

/** Normalises user input ("1,5") to the wire format ("1.5"). */
export function normaliseAmount(input: string): string {
  return input.trim().replace(',', '.')
}

export const BASE_UNITS: Record<Dimension, string | null> = {
  mass: 'g',
  volume: 'ml',
  count: 'piece',
  none: null,
}

const LARGE_UNITS: Partial<Record<Dimension, string>> = { mass: 'kg', volume: 'l' }

/** Mirrors the backend's display_quantity: kg/l only when the value stays exact. */
export function displayFromBase(baseMilli: bigint, dimension: Dimension) {
  const base = BASE_UNITS[dimension] ?? ''
  const large = LARGE_UNITS[dimension]
  const abs = baseMilli < 0n ? -baseMilli : baseMilli
  if (large && abs >= 1000n * SCALE && baseMilli % 1000n === 0n) {
    return { amount: fromMilli(baseMilli / 1000n), unit: large }
  }
  return { amount: fromMilli(baseMilli), unit: base }
}

export function stockLevelFromBase(baseMilli: bigint, dimension: Dimension): StockLevel {
  return {
    quantity_base: fromMilli(baseMilli),
    base_unit: BASE_UNITS[dimension] ?? '',
    display: displayFromBase(baseMilli, dimension),
  }
}

export function formatQuantity(amount: string, unit: string): string {
  const shown = trimAmount(amount)
  if (unit === 'piece') return `${shown} ${shown === '1' ? 'piece' : 'pieces'}`
  return `${shown} ${unit}`
}

export interface Step {
  amount: string
  unit: string
}

const STEPS: Record<Dimension, Step | null> = {
  mass: { amount: '100', unit: 'g' },
  volume: { amount: '100', unit: 'ml' },
  count: { amount: '1', unit: 'piece' },
  none: null,
}

export function stepFor(dimension: Dimension): Step | null {
  return STEPS[dimension]
}

/** New optimistic balance after a delta in base units; stock floors at zero like the ledger. */
export function applyDelta(level: StockLevel, deltaBaseMilli: bigint, dimension: Dimension) {
  const next = toMilli(level.quantity_base) + deltaBaseMilli
  return stockLevelFromBase(next < 0n ? 0n : next, dimension)
}
