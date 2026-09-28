import { BookOpen, Minus, Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer'
import { SERVINGS_MAX } from '@/features/recipes/api'
import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { useDebouncedValue } from '@/features/recipes/useDebouncedValue'

import { useDeletePlannedMeal, useMealSlots, useUpdatePlannedMeal } from './api'
import { formatLongDay } from './dates'
import { MealStatusActions } from './MealStatusActions'
import type { PlannedMeal } from './plan'

interface MealSheetProps {
  meal: PlannedMeal | null
  onOpenChange: (open: boolean) => void
}

export function MealSheet({ meal, onOpenChange }: MealSheetProps) {
  // Keep rendering the last meal while the drawer animates closed.
  const [shown, setShown] = useState(meal)
  if (meal && meal !== shown) setShown(meal)

  return (
    <Drawer open={meal !== null} onOpenChange={onOpenChange}>
      <DrawerContent>
        {shown && <SheetBody key={shown.id} meal={shown} onClose={() => onOpenChange(false)} />}
      </DrawerContent>
    </Drawer>
  )
}

function SheetBody({ meal, onClose }: { meal: PlannedMeal; onClose: () => void }) {
  const remove = useDeletePlannedMeal()
  const cooked = meal.status === 'cooked'

  return (
    <div className="flex min-h-0 flex-col overflow-y-auto pb-[env(safe-area-inset-bottom)]">
      <DrawerHeader className="flex-row items-center gap-3 text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
        <RecipePhoto
          src={meal.recipe.photo_thumb_url}
          alt={meal.recipe.name}
          className="size-14 shrink-0 rounded-md"
        />
        <div className="min-w-0">
          <DrawerTitle className="truncate text-lg">{meal.recipe.name}</DrawerTitle>
          <DrawerDescription>
            {formatLongDay(meal.date)}
            {meal.slot && ` · ${meal.slot.name}`}
          </DrawerDescription>
        </div>
      </DrawerHeader>

      <div className="flex flex-col gap-5 px-4 pb-4">
        <ServingsStepper meal={meal} />
        <LabelSelect meal={meal} />
        <MealStatusActions meal={meal} />

        <div className="flex flex-col gap-2">
          <Button asChild variant="outline" className="h-11">
            <Link to={`/recipes/${meal.recipe_id}`}>
              <BookOpen aria-hidden /> Open recipe
            </Link>
          </Button>
          <Button
            variant="destructive"
            className="h-11"
            disabled={cooked}
            aria-describedby={cooked ? 'meal-remove-hint' : undefined}
            onClick={() => {
              remove.mutate(meal)
              onClose()
            }}
          >
            <Trash2 aria-hidden /> Remove from plan
          </Button>
          {cooked && (
            <p id="meal-remove-hint" className="text-center text-sm text-muted-foreground">
              Uncook first to remove this meal.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function ServingsStepper({ meal }: { meal: PlannedMeal }) {
  const update = useUpdatePlannedMeal()
  const { mutate } = update
  const [servings, setServings] = useState(meal.servings)
  const debounced = useDebouncedValue(servings, 400)
  const sent = useRef(meal.servings)
  const cooked = meal.status === 'cooked'

  useEffect(() => {
    if (debounced === sent.current) return
    sent.current = debounced
    mutate(
      { meal, changes: { servings: debounced } },
      {
        onError: () => {
          sent.current = meal.servings
          setServings(meal.servings)
        },
      },
    )
  }, [debounced, meal, mutate])

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium" id="meal-servings-label">
          Servings
        </span>
        <div className="flex items-center gap-2" role="group" aria-labelledby="meal-servings-label">
          <Button
            variant="outline"
            size="icon"
            className="size-11"
            aria-label="Fewer servings"
            disabled={cooked || servings <= 1}
            onClick={() => setServings((n) => Math.max(1, n - 1))}
          >
            <Minus className="size-5" aria-hidden />
          </Button>
          <span className="w-8 text-center text-lg font-semibold tabular-nums" aria-live="polite">
            {servings}
          </span>
          <Button
            variant="outline"
            size="icon"
            className="size-11"
            aria-label="More servings"
            disabled={cooked || servings >= SERVINGS_MAX}
            onClick={() => setServings((n) => Math.min(SERVINGS_MAX, n + 1))}
          >
            <Plus className="size-5" aria-hidden />
          </Button>
        </div>
      </div>
      {cooked && (
        <p className="text-sm text-muted-foreground">Uncook first to change servings.</p>
      )}
    </div>
  )
}

/** Optional lunch/dinner/... tag; the meal keeps its place in the day either way. */
function LabelSelect({ meal }: { meal: PlannedMeal }) {
  const slots = useMealSlots()
  const { mutate } = useUpdatePlannedMeal()
  // A retired label stays selectable on the meal that still carries it.
  const options = (slots.data ?? []).filter((s) => s.active || s.id === meal.slot_id)
  if (meal.slot && !options.some((s) => s.id === meal.slot_id)) options.push(meal.slot)

  return (
    <div className="flex items-center justify-between gap-2">
      <label htmlFor="meal-label" className="text-sm font-medium">
        Label
      </label>
      <NativeSelect
        id="meal-label"
        className="w-40"
        value={meal.slot_id === null ? '' : String(meal.slot_id)}
        onChange={(e) => {
          const slot = options.find((s) => String(s.id) === e.target.value) ?? null
          mutate({ meal, changes: { slot_id: slot?.id ?? null }, slot })
        }}
      >
        <NativeSelectOption value="">None</NativeSelectOption>
        {options.map((s) => (
          <NativeSelectOption key={s.id} value={String(s.id)}>
            {s.name}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
  )
}
