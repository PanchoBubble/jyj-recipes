import { zodResolver } from '@hookform/resolvers/zod'
import { ArrowDown, ArrowLeft, ArrowUp, Minus, Plus, Trash2 } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import {
  useFieldArray,
  useForm,
  useWatch,
  type Control,
  type FieldErrors,
  type UseFormReturn,
} from 'react-hook-form'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  convertibleUnits,
  useIngredients,
  useUnits,
  type Ingredient,
  type MeasurableDimension,
  type Unit,
} from '@/features/ingredients/api'
import {
  LINES_MAX,
  SERVINGS_MAX,
  recipeErrorMessage,
  useCreateRecipe,
  useRecipe,
  useUpdateRecipe,
  type Recipe,
} from '@/features/recipes/api'
import {
  emptyLine,
  lineUnits,
  mapRecipeProblem,
  recipeSchema,
  toFormValues,
  toRecipeInput,
  unitLabel,
  unitsForDimension,
  type LineValues,
  type RecipeFormValues,
} from '@/features/recipes/form'
import { IngredientCombobox } from '@/features/recipes/IngredientCombobox'
import { photoErrorMessage, useUploadRecipePhoto } from '@/features/recipes/photo'
import { PhotoField } from '@/features/recipes/PhotoField'
import { BASE_UNITS } from '@/features/stock/quantity'
import { ApiError } from '@/lib/api'

const DIMENSIONS: { value: MeasurableDimension; label: string }[] = [
  { value: 'mass', label: 'Weight' },
  { value: 'volume', label: 'Volume' },
  { value: 'count', label: 'Pieces' },
]

const selectClass = 'w-full [&_select]:h-11'

export function RecipeEditorPage() {
  const { id } = useParams()
  const recipeId = id ? Number(id) : null
  if (recipeId === null) return <RecipeEditor recipe={null} />
  return <EditExisting id={recipeId} />
}

function EditExisting({ id }: { id: number }) {
  const recipe = useRecipe(id)
  if (recipe.isPending) {
    return (
      <div role="status" aria-label="Loading recipe" className="flex flex-col gap-3">
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (recipe.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm text-destructive">
          {recipeErrorMessage(recipe.error)}
        </p>
        <Button variant="outline" className="h-11" onClick={() => recipe.refetch()}>
          Try again
        </Button>
      </div>
    )
  }
  return <RecipeEditor key={recipe.data.id} recipe={recipe.data} />
}

function RecipeEditor({ recipe }: { recipe: Recipe | null }) {
  const navigate = useNavigate()
  const create = useCreateRecipe()
  const update = useUpdateRecipe(recipe?.id ?? 0)
  const units = useUnits()
  const ingredients = useIngredients()
  const uploadPhoto = useUploadRecipePhoto()
  const [formError, setFormError] = useState<string | null>(null)
  const [photo, setPhoto] = useState<File | null>(null)
  const [photoProgress, setPhotoProgress] = useState<number | null>(null)
  const form = useForm<RecipeFormValues>({
    resolver: zodResolver(recipeSchema),
    defaultValues: toFormValues(recipe),
    shouldFocusError: true,
  })
  const lines = useFieldArray({ control: form.control, name: 'ingredients' })
  const { errors, isSubmitting } = form.formState
  const backTo = recipe ? `/recipes/${recipe.id}` : '/recipes'

  const onSubmit = form.handleSubmit(async (values) => {
    setFormError(null)
    const input = toRecipeInput(values)
    let saved: Recipe
    try {
      saved = recipe ? await update.mutateAsync(input) : await create.mutateAsync(input)
    } catch (error) {
      if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
        setFormError(recipeErrorMessage(error))
        return
      }
      const unplaced: string[] = []
      let first: string | null = null
      for (const { path, message } of mapRecipeProblem(error)) {
        if (path) {
          form.setError(path as keyof RecipeFormValues, { type: 'server', message })
          first ??= path
        } else {
          unplaced.push(message)
        }
      }
      if (unplaced.length > 0) setFormError(unplaced.join(' '))
      else if (first) setFormError('Fix the highlighted fields and save again.')
      if (first) form.setFocus(first as keyof RecipeFormValues)
      return
    }

    if (!recipe && photo) {
      // The recipe exists now, so a failed upload must not keep the user here where a
      // second submit would create a duplicate; the detail page can retry the photo.
      setPhotoProgress(0)
      try {
        await uploadPhoto.mutateAsync({ id: saved.id, file: photo, onProgress: setPhotoProgress })
        toast.success(`Added ${saved.name}`)
      } catch (error) {
        toast.error(`Added ${saved.name}, but the photo didn't upload`, {
          description: `${photoErrorMessage(error)} You can try again from the recipe page.`,
        })
      } finally {
        setPhotoProgress(null)
      }
    } else {
      toast.success(recipe ? `Saved ${saved.name}` : `Added ${saved.name}`)
    }
    navigate(`/recipes/${saved.id}`, { replace: true })
  })

  const servingsField = form.register('default_servings', { valueAsNumber: true })
  const stepServings = (by: number) => {
    const current = form.getValues('default_servings')
    const next = Math.min(SERVINGS_MAX, Math.max(1, (Number.isFinite(current) ? current : 1) + by))
    form.setValue('default_servings', next, { shouldDirty: true, shouldValidate: true })
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      <Link
        to={backTo}
        className="-ml-2 inline-flex h-11 w-fit items-center gap-1 rounded-lg px-2 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden /> {recipe ? 'Back to recipe' : 'Recipes'}
      </Link>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="recipe-name">Name</Label>
        <Input
          id="recipe-name"
          autoComplete="off"
          className="h-11"
          aria-invalid={errors.name ? true : undefined}
          aria-describedby="recipe-name-hint"
          {...form.register('name')}
        />
        <FieldError id="recipe-name-hint" message={errors.name?.message} />
      </div>

      {recipe ? (
        <PhotoField recipe={recipe} label="Photo" hint="Photo changes save right away." />
      ) : (
        <PhotoField
          recipe={null}
          pending={photo}
          onPendingChange={setPhoto}
          progress={photoProgress}
          label="Photo"
          hint={photo ? 'Uploads after the recipe is created.' : 'Optional.'}
        />
      )}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="recipe-description">Description</Label>
        <Textarea
          id="recipe-description"
          rows={3}
          placeholder="Optional. Method, tips, where it's from."
          aria-invalid={errors.description ? true : undefined}
          aria-describedby="recipe-description-hint"
          {...form.register('description')}
        />
        <FieldError id="recipe-description-hint" message={errors.description?.message} />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="recipe-servings">Default servings</Label>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="size-11"
            aria-label="Fewer servings"
            onClick={() => stepServings(-1)}
          >
            <Minus className="size-5" aria-hidden />
          </Button>
          <Input
            id="recipe-servings"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="off"
            className="h-11 w-20 text-center"
            aria-invalid={errors.default_servings ? true : undefined}
            aria-describedby="recipe-servings-hint"
            {...servingsField}
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="size-11"
            aria-label="More servings"
            onClick={() => stepServings(1)}
          >
            <Plus className="size-5" aria-hidden />
          </Button>
        </div>
        <FieldError id="recipe-servings-hint" message={errors.default_servings?.message} />
      </div>

      <section aria-labelledby="recipe-ingredients-heading" className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 id="recipe-ingredients-heading" className="font-medium">
            Ingredients
          </h2>
          <span className="text-xs text-muted-foreground">Amounts are per person</span>
        </div>
        {errors.ingredients?.root?.message || errors.ingredients?.message ? (
          <FieldError message={errors.ingredients?.root?.message ?? errors.ingredients?.message} />
        ) : null}
        <ol className="flex flex-col gap-3">
          {lines.fields.map((field, index) => (
            <IngredientLine
              key={field.id}
              form={form}
              index={index}
              count={lines.fields.length}
              units={units.data ?? []}
              ingredients={ingredients.data ?? []}
              errors={errors.ingredients?.[index]}
              onMove={(to) => lines.move(index, to)}
              onRemove={() => lines.remove(index)}
            />
          ))}
        </ol>
        {lines.fields.length === 0 && (
          <p className="text-sm text-muted-foreground">No ingredients yet.</p>
        )}
        <Button
          type="button"
          variant="outline"
          className="h-11"
          disabled={lines.fields.length >= LINES_MAX}
          onClick={() => lines.append(emptyLine(), { shouldFocus: true })}
        >
          <Plus aria-hidden /> Add ingredient
        </Button>
      </section>

      {formError && (
        <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {formError}
        </p>
      )}

      <div className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] -mx-4 border-t bg-background/95 px-4 py-3 backdrop-blur">
        <Button type="submit" className="h-11 w-full" disabled={isSubmitting}>
          {photoProgress !== null
            ? `Uploading photo ${Math.round(photoProgress * 100)}%…`
            : isSubmitting
              ? 'Saving…'
              : recipe
                ? 'Save recipe'
                : 'Create recipe'}
        </Button>
      </div>
    </form>
  )
}

interface LineProps {
  form: UseFormReturn<RecipeFormValues>
  index: number
  count: number
  units: Unit[]
  ingredients: Ingredient[]
  errors?: FieldErrors<LineValues>
  onMove: (to: number) => void
  onRemove: () => void
}

function IngredientLine({
  form,
  index,
  count,
  units,
  ingredients,
  errors,
  onMove,
  onRemove,
}: LineProps) {
  const id = useId()
  const line = useWatch({ control: form.control as Control<RecipeFormValues>, name: `ingredients.${index}` })
  const path = `ingredients.${index}` as const
  const position = index + 1
  const title = line.ingredient_name.trim() || `ingredient ${position}`

  // Registered only so validation and server errors can focus these controlled fields.
  const { ref: ingredientRef } = form.register(`${path}.ingredient_name`)
  const { ref: unitRef } = form.register(`${path}.unit`)

  const known = ingredients.find((i) => i.id === line.ingredient_id)
  const options = useMemo(() => {
    if (line.new_ingredient) return lineUnits(units, unitsForDimension(units, line.new_ingredient.dimension))
    if (known) return lineUnits(units, convertibleUnits(units, known))
    return []
  }, [units, known, line.new_ingredient])

  const set = <K extends keyof LineValues>(key: K, value: LineValues[K]) =>
    form.setValue(`${path}.${key}` as `ingredients.${number}.${K}`, value as never, {
      shouldDirty: true,
      shouldValidate: form.formState.isSubmitted,
    })

  const setUnit = (code: string) => {
    set('unit', code)
    set('unit_measurable', units.find((u) => u.code === code)?.dimension !== 'none')
  }

  const pick = (ingredient: Ingredient) => {
    form.clearErrors(path)
    set('ingredient_id', ingredient.id)
    set('ingredient_name', ingredient.name)
    set('new_ingredient', null)
    setUnit(ingredient.default_unit)
  }

  const createNew = (name: string) => {
    form.clearErrors(path)
    set('ingredient_id', null)
    set('ingredient_name', name)
    set('new_ingredient', { name, dimension: 'mass', default_unit: 'g' })
    setUnit('g')
  }

  const setNewDimension = (dimension: MeasurableDimension) => {
    const base = BASE_UNITS[dimension] ?? ''
    set('new_ingredient', { name: line.new_ingredient!.name, dimension, default_unit: base })
    setUnit(base)
  }

  const nameError =
    errors?.ingredient_name?.message ??
    errors?.new_ingredient?.name?.message ??
    errors?.new_ingredient?.message ??
    errors?.root?.message
  const unitError = errors?.unit?.message ?? errors?.new_ingredient?.default_unit?.message

  return (
    <li
      aria-label={`Ingredient ${position}`}
      className="flex flex-col gap-2 rounded-xl border p-3"
    >
      <div className="flex items-center gap-1">
        <span className="flex-1 text-xs font-medium text-muted-foreground">#{position}</span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-11"
          aria-label={`Move ${title} up`}
          disabled={index === 0}
          onClick={() => onMove(index - 1)}
        >
          <ArrowUp className="size-5" aria-hidden />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-11"
          aria-label={`Move ${title} down`}
          disabled={index === count - 1}
          onClick={() => onMove(index + 1)}
        >
          <ArrowDown className="size-5" aria-hidden />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-11 text-destructive"
          aria-label={`Remove ${title}`}
          onClick={onRemove}
        >
          <Trash2 className="size-5" aria-hidden />
        </Button>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-ingredient`} className="sr-only">
          Ingredient {position}
        </Label>
        <IngredientCombobox
          id={`${id}-ingredient`}
          inputRef={ingredientRef}
          value={line.ingredient_name}
          ingredients={ingredients}
          invalid={Boolean(nameError)}
          describedBy={`${id}-ingredient-hint`}
          onType={(text) => {
            set('ingredient_name', text)
            set('ingredient_id', null)
            set('new_ingredient', null)
          }}
          onPick={pick}
          onCreate={createNew}
        />
        <FieldError id={`${id}-ingredient-hint`} message={nameError} />
      </div>

      {line.new_ingredient && (
        <div className="flex flex-col gap-2 rounded-lg bg-muted/50 p-2">
          <Badge variant="secondary" className="w-fit">
            New ingredient
          </Badge>
          <div className="grid grid-cols-2 gap-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor={`${id}-dimension`} className="text-xs">
                Measured by
              </Label>
              <NativeSelect
                id={`${id}-dimension`}
                className={selectClass}
                value={line.new_ingredient.dimension}
                onChange={(e) => setNewDimension(e.target.value as MeasurableDimension)}
              >
                {DIMENSIONS.map((d) => (
                  <NativeSelectOption key={d.value} value={d.value}>
                    {d.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor={`${id}-default-unit`} className="text-xs">
                Default unit
              </Label>
              <NativeSelect
                id={`${id}-default-unit`}
                className={selectClass}
                value={line.new_ingredient.default_unit}
                onChange={(e) =>
                  set('new_ingredient', { ...line.new_ingredient!, default_unit: e.target.value })
                }
              >
                {unitsForDimension(units, line.new_ingredient.dimension).map((u) => (
                  <NativeSelectOption key={u.code} value={u.code}>
                    {u.code}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-[1fr_7.5rem] gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-amount`} className="text-xs">
            Per person
          </Label>
          <Input
            id={`${id}-amount`}
            inputMode="decimal"
            autoComplete="off"
            placeholder={line.unit_measurable ? 'e.g. 100' : 'optional'}
            className="h-11"
            aria-invalid={errors?.amount ? true : undefined}
            aria-describedby={`${id}-amount-hint`}
            {...form.register(`${path}.amount`)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-unit`} className="text-xs">
            Unit
          </Label>
          <NativeSelect
            id={`${id}-unit`}
            className={selectClass}
            value={line.unit}
            aria-invalid={unitError ? true : undefined}
            aria-describedby={`${id}-unit-hint`}
            disabled={options.length === 0}
            onChange={(e) => setUnit(e.target.value)}
            ref={unitRef}
          >
            {options.length === 0 && (
              <NativeSelectOption value={line.unit}>{unitLabel(line.unit) || '—'}</NativeSelectOption>
            )}
            {options.map((u) => (
              <NativeSelectOption key={u.code} value={u.code}>
                {unitLabel(u.code)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
      </div>
      <FieldError id={`${id}-amount-hint`} message={errors?.amount?.message} />
      <FieldError id={`${id}-unit-hint`} message={unitError} />

      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-note`} className="text-xs">
          Note
        </Label>
        <Input
          id={`${id}-note`}
          autoComplete="off"
          placeholder="Optional, e.g. finely chopped"
          className="h-11"
          aria-invalid={errors?.note ? true : undefined}
          aria-describedby={`${id}-note-hint`}
          {...form.register(`${path}.note`)}
        />
        <FieldError id={`${id}-note-hint`} message={errors?.note?.message} />
      </div>
    </li>
  )
}

function FieldError({ id, message }: { id?: string; message?: string }) {
  if (!message) return null
  return (
    <p id={id} className="text-xs text-destructive">
      {message}
    </p>
  )
}
