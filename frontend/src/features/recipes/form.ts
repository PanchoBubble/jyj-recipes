import { z } from 'zod'

import type { MeasurableDimension, Unit } from '@/features/ingredients/api'
import {
  DESCRIPTION_MAX,
  INGREDIENT_NAME_MAX,
  LINES_MAX,
  NAME_MAX,
  NOTE_MAX,
  SERVINGS_MAX,
  type Recipe,
  type RecipeInput,
  type RecipeLineInput,
} from '@/features/recipes/api'
import { normaliseAmount, toMilli, trimAmount } from '@/features/stock/quantity'
import { ApiError } from '@/lib/api'

const AMOUNT_RE = /^\d{1,9}(?:[.,]\d{1,3})?$/

export const lineSchema = z
  .object({
    ingredient_id: z.number().nullable(),
    ingredient_name: z.string(),
    new_ingredient: z
      .object({
        name: z.string().trim().min(1).max(INGREDIENT_NAME_MAX, `At most ${INGREDIENT_NAME_MAX} characters`),
        dimension: z.enum(['mass', 'volume', 'count']),
        default_unit: z.string().min(1, 'Pick a unit'),
      })
      .nullable(),
    amount: z.string(),
    unit: z.string(),
    unit_measurable: z.boolean(),
    note: z.string().trim().max(NOTE_MAX, `At most ${NOTE_MAX} characters`),
  })
  .superRefine((line, ctx) => {
    if (line.ingredient_id === null && line.new_ingredient === null) {
      ctx.addIssue({ code: 'custom', path: ['ingredient_name'], message: 'Pick an ingredient' })
    }
    if (!line.unit) {
      ctx.addIssue({ code: 'custom', path: ['unit'], message: 'Pick a unit' })
    }
    const amount = line.amount.trim()
    if (amount === '') {
      if (line.unit_measurable) {
        ctx.addIssue({ code: 'custom', path: ['amount'], message: 'Enter an amount' })
      }
      return
    }
    if (!AMOUNT_RE.test(amount)) {
      ctx.addIssue({ code: 'custom', path: ['amount'], message: 'Use a number like 250 or 1.5' })
    } else if (line.unit_measurable && toMilli(normaliseAmount(amount)) === 0n) {
      ctx.addIssue({ code: 'custom', path: ['amount'], message: 'Must be more than 0' })
    }
  })

export const recipeSchema = z.object({
  name: z.string().trim().min(1, 'Enter a name').max(NAME_MAX, `At most ${NAME_MAX} characters`),
  description: z.string().trim().max(DESCRIPTION_MAX, `At most ${DESCRIPTION_MAX} characters`),
  default_servings: z
    .number({ message: 'Enter a number' })
    .int('Whole servings only')
    .min(1, 'At least 1')
    .max(SERVINGS_MAX, `At most ${SERVINGS_MAX}`),
  ingredients: z.array(lineSchema).max(LINES_MAX, `At most ${LINES_MAX} ingredients`),
})

export type LineValues = z.infer<typeof lineSchema>
export type RecipeFormValues = z.infer<typeof recipeSchema>

export function emptyLine(): LineValues {
  return {
    ingredient_id: null,
    ingredient_name: '',
    new_ingredient: null,
    amount: '',
    unit: '',
    unit_measurable: true,
    note: '',
  }
}

export function toFormValues(recipe: Recipe | null): RecipeFormValues {
  if (!recipe) {
    return { name: '', description: '', default_servings: 2, ingredients: [emptyLine()] }
  }
  return {
    name: recipe.name,
    description: recipe.description ?? '',
    default_servings: recipe.default_servings,
    ingredients: [...recipe.ingredients]
      .sort((a, b) => a.position - b.position)
      .map((line) => ({
            ingredient_id: line.ingredient_id,
        ingredient_name: line.ingredient_name,
        new_ingredient: null,
        amount: line.amount_per_person ? trimAmount(line.amount_per_person) : '',
        unit: line.unit,
        unit_measurable: line.unit_dimension !== 'none',
        note: line.note ?? '',
      })),
  }
}

export function toRecipeInput(values: RecipeFormValues): RecipeInput {
  return {
    name: values.name.trim(),
    description: values.description.trim() || null,
    default_servings: values.default_servings,
    ingredients: values.ingredients.map((line): RecipeLineInput => {
      const rest = {
        amount_per_person: line.amount.trim() ? normaliseAmount(line.amount) : null,
        unit: line.unit,
        note: line.note.trim() || null,
      }
      return line.new_ingredient
        ? {
            new_ingredient: { ...line.new_ingredient, name: line.new_ingredient.name.trim() },
            ...rest,
          }
        : { ingredient_id: line.ingredient_id!, ...rest }
    }),
  }
}

/** Units a line can use: the measurable ones the ingredient converts to, plus "to taste" ones. */
export function lineUnits(units: Unit[], measurable: Unit[]): Unit[] {
  return [...measurable, ...units.filter((u) => u.dimension === 'none')]
}

export function unitsForDimension(units: Unit[], dimension: MeasurableDimension): Unit[] {
  return units.filter((u) => u.dimension === dimension)
}

export function unitLabel(code: string): string {
  return code.replaceAll('_', ' ')
}

type LineField = 'ingredient_name' | 'amount' | 'unit' | 'note'

const LOC_FIELDS: Record<string, LineField> = {
  ingredient_id: 'ingredient_name',
  new_ingredient: 'ingredient_name',
  amount_per_person: 'amount',
  unit: 'unit',
  note: 'note',
}

const TOP_FIELDS = new Set(['name', 'description', 'default_servings'])

export interface MappedProblem {
  /** Field path in the form, e.g. `ingredients.2.amount` or `name`; null when it fits nowhere. */
  path: string | null
  message: string
}

/**
 * Maps an API problem onto form fields. Service errors carry `row` and a detail prefixed with
 * `ingredients[i]: `; request validation errors carry pydantic `loc`s under `errors`.
 */
export function mapRecipeProblem(error: ApiError): MappedProblem[] {
  const row = error.extensions.row
  if (typeof row === 'number') {
    const message = (error.detail ?? error.title).replace(/^ingredients\[\d+\]:\s*/, '')
    return [{ path: `ingredients.${row}.${fieldFromDetail(message)}`, message }]
  }

  const errors = error.extensions.errors
  if (!Array.isArray(errors) || errors.length === 0) {
    return [{ path: null, message: error.detail ?? error.title }]
  }
  return errors.map((raw): MappedProblem => {
    const e = raw as { loc?: unknown[]; msg?: string }
    const loc = (e.loc ?? []).filter((part) => part !== 'body')
    const message = e.msg ?? 'Invalid value'
    if (loc[0] === 'ingredients' && typeof loc[1] === 'number') {
      const field = LOC_FIELDS[String(loc[2])] ?? 'ingredient_name'
      return { path: `ingredients.${loc[1]}.${field}`, message }
    }
    if (typeof loc[0] === 'string' && TOP_FIELDS.has(loc[0])) return { path: loc[0], message }
    return { path: null, message }
  })
}

function fieldFromDetail(detail: string): LineField {
  if (detail.startsWith('amount_per_person')) return 'amount'
  if (detail.startsWith('note')) return 'note'
  if (detail.startsWith('unknown unit') || detail.startsWith('cannot use')) return 'unit'
  return 'ingredient_name'
}
