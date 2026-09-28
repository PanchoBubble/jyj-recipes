import { convertibleUnits, type Ingredient, type Unit } from '@/features/ingredients/api'
import {
  applyDelta,
  displayFromBase,
  formatQuantity,
  fromMilli,
  toMilli,
  trimAmount,
} from '@/features/stock/quantity'

describe('decimal helpers', () => {
  it('round-trips decimal strings exactly', () => {
    expect(toMilli('0.1') + toMilli('0.2')).toBe(toMilli('0.3'))
    expect(fromMilli(toMilli('-12.5'))).toBe('-12.500')
    expect(fromMilli(toMilli('-0.25'))).toBe('-0.250')
    expect(trimAmount('1.500')).toBe('1.5')
    expect(trimAmount('200.000')).toBe('200')
    expect(trimAmount('0.000')).toBe('0')
    expect(() => toMilli('1e3')).toThrow()
  })

  it('picks kg/l only when the value stays exact, like the backend', () => {
    expect(displayFromBase(toMilli('1500'), 'mass')).toEqual({ amount: '1.500', unit: 'kg' })
    expect(displayFromBase(toMilli('1234.5'), 'mass')).toEqual({ amount: '1234.500', unit: 'g' })
    expect(displayFromBase(toMilli('999'), 'volume')).toEqual({ amount: '999.000', unit: 'ml' })
    expect(displayFromBase(toMilli('-2000'), 'volume')).toEqual({ amount: '-2.000', unit: 'l' })
    expect(displayFromBase(toMilli('3000'), 'count')).toEqual({ amount: '3000.000', unit: 'piece' })
  })

  it('floors optimistic balances at zero', () => {
    const level = { quantity_base: '50.000', base_unit: 'g', display: { amount: '50.000', unit: 'g' } }
    expect(applyDelta(level, toMilli('-100'), 'mass').quantity_base).toBe('0.000')
  })

  it('pluralises pieces', () => {
    expect(formatQuantity('1.000', 'piece')).toBe('1 piece')
    expect(formatQuantity('2.000', 'piece')).toBe('2 pieces')
  })
})

describe('convertibleUnits', () => {
  const units: Unit[] = [
    { code: 'g', dimension: 'mass', to_base: '1' },
    { code: 'ml', dimension: 'volume', to_base: '1' },
    { code: 'piece', dimension: 'count', to_base: '1' },
    { code: 'pinch', dimension: 'none', to_base: null },
  ]
  const make = (dimension: Ingredient['dimension'], perMl: boolean, perPiece: boolean) =>
    ({
      dimension,
      grams_per_ml: perMl ? '1.000' : null,
      grams_per_piece: perPiece ? '50.000' : null,
    }) as Ingredient
  const codes = (i: Ingredient) => convertibleUnits(units, i).map((u) => u.code)

  it('follows the ingredient conversion factors', () => {
    expect(codes(make('mass', false, false))).toEqual(['g'])
    expect(codes(make('mass', true, false))).toEqual(['g', 'ml'])
    expect(codes(make('count', false, true))).toEqual(['g', 'piece'])
    expect(codes(make('count', true, false))).toEqual(['piece'])
    expect(codes(make('volume', true, true))).toEqual(['g', 'ml', 'piece'])
  })
})
