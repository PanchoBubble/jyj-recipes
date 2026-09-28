import { zodResolver } from '@hookform/resolvers/zod'
import { Trash2 } from 'lucide-react'
import { useId, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import {
  useCreateIngredient,
  useDeleteIngredient,
  useUnits,
  useUpdateIngredient,
  type Ingredient,
  type IngredientInput,
  type MeasurableDimension,
} from '@/features/ingredients/api'
import { BASE_UNITS, normaliseAmount, trimAmount } from '@/features/stock/quantity'
import { ApiError } from '@/lib/api'

const DIMENSIONS: { value: MeasurableDimension; label: string }[] = [
  { value: 'mass', label: 'Weight (g, kg)' },
  { value: 'volume', label: 'Volume (ml, l, cups)' },
  { value: 'count', label: 'Pieces' },
]

const factor = z
  .string()
  .trim()
  .refine((v) => v === '' || /^\d{1,9}(?:[.,]\d{1,3})?$/.test(v), 'Use a number like 0.53 (up to 3 decimals)')
  .refine((v) => v === '' || /[1-9]/.test(v), 'Must be more than 0')

const schema = z.object({
  name: z.string().trim().min(1, 'Enter a name').max(100, 'At most 100 characters'),
  dimension: z.enum(['mass', 'volume', 'count']),
  default_unit: z.string().min(1, 'Pick a unit'),
  category: z.string().trim().max(50, 'At most 50 characters'),
  grams_per_ml: factor,
  grams_per_piece: factor,
})

type FormValues = z.infer<typeof schema>

function toValues(ingredient: Ingredient | null): FormValues {
  const dimension = (ingredient?.dimension ?? 'mass') as MeasurableDimension
  return {
    name: ingredient?.name ?? '',
    dimension,
    default_unit: ingredient?.default_unit ?? BASE_UNITS[dimension] ?? 'g',
    category: ingredient?.category ?? '',
    grams_per_ml: ingredient?.grams_per_ml ? trimAmount(ingredient.grams_per_ml) : '',
    grams_per_piece: ingredient?.grams_per_piece ? trimAmount(ingredient.grams_per_piece) : '',
  }
}

function toInput(values: FormValues): IngredientInput {
  const optional = (v: string) => (v.trim() === '' ? null : normaliseAmount(v))
  return {
    name: values.name.trim(),
    dimension: values.dimension,
    default_unit: values.default_unit,
    category: values.category.trim() || null,
    grams_per_ml: optional(values.grams_per_ml),
    grams_per_piece: optional(values.grams_per_piece),
  }
}

const REFERENCE_LABELS: Record<string, string> = {
  stock_movements: 'pantry history',
  recipe_ingredients: 'recipes',
}

function problemMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Something went wrong. Try again.'
  if (error.status === 0) return "Can't reach the server. Check your connection."
  const refs = error.extensions.references
  const detail = error.detail ?? error.title
  if (Array.isArray(refs) && refs.length > 0) {
    const names = refs.map((r) => REFERENCE_LABELS[String(r)] ?? String(r).replaceAll('_', ' '))
    return `${detail} (used by ${names.join(', ')})`
  }
  return detail
}

interface BlockingRecipe {
  id: number
  name: string
  units: string[]
}

function blockingRecipes(error: unknown): BlockingRecipe[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const recipes = error.extensions.recipes
  if (!Array.isArray(recipes) || recipes.length === 0) return null
  return recipes
    .filter((r): r is BlockingRecipe => typeof r?.id === 'number' && typeof r?.name === 'string')
    .map((r) => ({ id: r.id, name: r.name, units: Array.isArray(r.units) ? r.units.map(String) : [] }))
}

export interface IngredientFormTarget {
  ingredient: Ingredient | null
}

interface Props {
  target: IngredientFormTarget | null
  categories: string[]
  onOpenChange: (open: boolean) => void
}

export function IngredientFormDrawer({ target, categories, onOpenChange }: Props) {
  // Keep rendering the last target while the drawer animates closed.
  const [shown, setShown] = useState(target)
  if (target && target !== shown) setShown(target)

  return (
    <Drawer open={target !== null} onOpenChange={onOpenChange}>
      <DrawerContent>
        {shown && (
          <IngredientForm
            key={shown.ingredient?.id ?? 'new'}
            ingredient={shown.ingredient}
            categories={categories}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DrawerContent>
    </Drawer>
  )
}

function IngredientForm({
  ingredient,
  categories,
  onDone,
}: {
  ingredient: Ingredient | null
  categories: string[]
  onDone: () => void
}) {
  const id = useId()
  const units = useUnits()
  const create = useCreateIngredient()
  const update = useUpdateIngredient(ingredient?.id ?? 0)
  const [formError, setFormError] = useState<string | null>(null)
  const [blocking, setBlocking] = useState<BlockingRecipe[] | null>(null)
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: toValues(ingredient),
  })
  const { errors, isSubmitting } = form.formState
  const dimension = useWatch({ control: form.control, name: 'dimension' })
  const unitOptions = (units.data ?? []).filter((u) => u.dimension === dimension)

  const onSubmit = form.handleSubmit(async (values) => {
    setFormError(null)
    setBlocking(null)
    try {
      const input = toInput(values)
      const saved = ingredient
        ? await update.mutateAsync(input)
        : await create.mutateAsync(input)
      toast.success(ingredient ? `Saved ${saved.name}` : `Added ${saved.name}`)
      onDone()
    } catch (error) {
      const recipes = blockingRecipes(error)
      if (recipes) setBlocking(recipes)
      else setFormError(problemMessage(error))
    }
  })

  const field = (name: keyof FormValues) => ({
    id: `${id}-${name}`,
    'aria-invalid': errors[name] ? true : undefined,
    'aria-describedby': `${id}-${name}-hint`,
  })

  return (
    <div className="flex min-h-0 flex-col overflow-y-auto pb-[env(safe-area-inset-bottom)]">
      <DrawerHeader className="text-left">
        <DrawerTitle className="text-lg">{ingredient ? 'Edit ingredient' : 'New ingredient'}</DrawerTitle>
        <DrawerDescription>
          {ingredient ? 'Changes apply to recipes and the pantry.' : 'Starts at zero in the pantry.'}
        </DrawerDescription>
      </DrawerHeader>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4 px-4 pb-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-name`}>Name</Label>
          <Input {...field('name')} autoComplete="off" className="h-11" {...form.register('name')} />
          <FieldHint id={`${id}-name-hint`} error={errors.name?.message} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-dimension`}>Measured by</Label>
            <NativeSelect
              {...field('dimension')}
              className="w-full [&_select]:h-11"
              {...form.register('dimension', {
                onChange: (e: React.ChangeEvent<HTMLSelectElement>) => {
                  const dim = e.target.value as MeasurableDimension
                  form.setValue('default_unit', BASE_UNITS[dim] ?? '')
                },
              })}
            >
              {DIMENSIONS.map((d) => (
                <NativeSelectOption key={d.value} value={d.value}>
                  {d.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <FieldHint id={`${id}-dimension-hint`} error={errors.dimension?.message} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-default_unit`}>Default unit</Label>
            <NativeSelect
              {...field('default_unit')}
              className="w-full [&_select]:h-11"
              {...form.register('default_unit')}
            >
              {unitOptions.length === 0 && (
                <NativeSelectOption value={form.getValues('default_unit')}>
                  {form.getValues('default_unit')}
                </NativeSelectOption>
              )}
              {unitOptions.map((u) => (
                <NativeSelectOption key={u.code} value={u.code}>
                  {u.code}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <FieldHint id={`${id}-default_unit-hint`} error={errors.default_unit?.message} />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-category`}>Category</Label>
          <Input
            {...field('category')}
            list={`${id}-categories`}
            placeholder="e.g. Dairy, Pantry"
            autoComplete="off"
            className="h-11"
            {...form.register('category')}
          />
          <datalist id={`${id}-categories`}>
            {categories.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
          <FieldHint
            id={`${id}-category-hint`}
            error={errors.category?.message}
            hint="Optional. Groups the pantry and shopping lists."
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-grams_per_ml`}>Grams per ml</Label>
            <Input
              {...field('grams_per_ml')}
              inputMode="decimal"
              placeholder="e.g. 0.53"
              autoComplete="off"
              className="h-11"
              {...form.register('grams_per_ml')}
            />
            <FieldHint
              id={`${id}-grams_per_ml-hint`}
              error={errors.grams_per_ml?.message}
              hint="Optional. Lets ml and cups convert to grams. Water is 1."
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-grams_per_piece`}>Grams per piece</Label>
            <Input
              {...field('grams_per_piece')}
              inputMode="decimal"
              placeholder="e.g. 50"
              autoComplete="off"
              className="h-11"
              {...form.register('grams_per_piece')}
            />
            <FieldHint
              id={`${id}-grams_per_piece-hint`}
              error={errors.grams_per_piece?.message}
              hint="Optional. Weight of one piece, e.g. an egg is about 50."
            />
          </div>
        </div>

        {formError && (
          <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {formError}
          </p>
        )}

        {blocking && (
          <div role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <p>Used by these recipes in units that need this conversion:</p>
            <ul className="mt-1 flex flex-col">
              {blocking.map((r) => (
                <li key={r.id}>
                  <Link
                    to={`/recipes/${r.id}/edit`}
                    onClick={onDone}
                    className="inline-flex min-h-11 items-center gap-1 font-medium underline underline-offset-4"
                  >
                    {r.name}
                  </Link>
                  {r.units.length > 0 && <span className="text-destructive/80"> ({r.units.join(', ')})</span>}
                </li>
              ))}
            </ul>
            <p className="mt-1">Change those lines first, then save again.</p>
          </div>
        )}

        <Button type="submit" className="h-11" disabled={isSubmitting}>
          {isSubmitting ? 'Saving…' : ingredient ? 'Save' : 'Add ingredient'}
        </Button>
      </form>

      {ingredient && <DeleteSection ingredient={ingredient} onDeleted={onDone} />}
    </div>
  )
}

function FieldHint({ id, error, hint }: { id: string; error?: string; hint?: string }) {
  if (error) {
    return (
      <p id={id} className="text-xs text-destructive">
        {error}
      </p>
    )
  }
  if (!hint) return null
  return (
    <p id={id} className="text-xs text-muted-foreground">
      {hint}
    </p>
  )
}

function DeleteSection({ ingredient, onDeleted }: { ingredient: Ingredient; onDeleted: () => void }) {
  const remove = useDeleteIngredient(ingredient.id)
  const [confirming, setConfirming] = useState(false)

  const onDelete = () =>
    remove.mutate(undefined, {
      onSuccess: () => {
        toast.success(`Deleted ${ingredient.name}`)
        onDeleted()
      },
    })

  return (
    <div className="flex flex-col gap-2 border-t px-4 pt-4 pb-4">
      {remove.isError && (
        <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {problemMessage(remove.error)}
        </p>
      )}
      {confirming ? (
        <>
          <p className="text-sm">
            Delete <span className="font-medium">{ingredient.name}</span>? This can't be undone.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <Button
              variant="outline"
              className="h-11"
              onClick={() => {
                setConfirming(false)
                remove.reset()
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              className="h-11"
              disabled={remove.isPending}
              onClick={onDelete}
            >
              {remove.isPending ? 'Deleting…' : 'Delete'}
            </Button>
          </div>
        </>
      ) : (
        <Button variant="destructive" className="h-11" onClick={() => setConfirming(true)}>
          <Trash2 aria-hidden /> Delete ingredient
        </Button>
      )}
    </div>
  )
}
