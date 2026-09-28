import { ArrowLeft, Minus, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  SERVINGS_MAX,
  recipeErrorMessage,
  useDeleteRecipe,
  useRecipe,
  useScaledRecipe,
  type Recipe,
  type ScaledIngredient,
} from '@/features/recipes/api'
import { unitLabel } from '@/features/recipes/form'
import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { useDebouncedValue } from '@/features/recipes/useDebouncedValue'
import { formatQuantity, trimAmount } from '@/features/stock/quantity'

export function RecipeDetailPage() {
  const id = Number(useParams().id)
  const recipe = useRecipe(id)

  if (recipe.isPending) {
    return (
      <div role="status" aria-label="Loading recipe" className="flex flex-col gap-3">
        <Skeleton className="aspect-video w-full rounded-xl" />
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (recipe.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <BackLink />
        <p role="alert" className="text-sm text-destructive">
          {recipeErrorMessage(recipe.error)}
        </p>
        <Button variant="outline" className="h-11" onClick={() => recipe.refetch()}>
          Try again
        </Button>
      </div>
    )
  }
  return <RecipeDetail key={recipe.data.id} recipe={recipe.data} />
}

function BackLink() {
  return (
    <Link
      to="/recipes"
      className="-ml-2 inline-flex h-11 w-fit items-center gap-1 rounded-lg px-2 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" aria-hidden /> Recipes
    </Link>
  )
}

function RecipeDetail({ recipe }: { recipe: Recipe }) {
  const [servings, setServings] = useState(recipe.default_servings)
  const debounced = useDebouncedValue(servings, 300)
  const scaled = useScaledRecipe(recipe.id, debounced, recipe.ingredients.length > 0)
  const byPosition = new Map(scaled.data?.ingredients.map((line) => [line.position, line]))
  const stale = scaled.isFetching || debounced !== servings || scaled.data?.servings !== servings

  return (
    <article className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <BackLink />
        <Button asChild variant="ghost" className="h-11">
          <Link to={`/recipes/${recipe.id}/edit`}>
            <Pencil aria-hidden /> Edit
          </Link>
        </Button>
      </div>

      <RecipePhoto
        src={recipe.photo_url}
        alt={recipe.name}
        className="aspect-video w-full rounded-xl"
      />

      <header className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold">{recipe.name}</h2>
        {recipe.archived_at && (
          <Badge variant="secondary" className="w-fit">
            Archived
          </Badge>
        )}
        {recipe.description && (
          <p className="text-sm whitespace-pre-line text-muted-foreground">{recipe.description}</p>
        )}
      </header>

      <div className="flex items-center justify-between gap-3 rounded-xl border p-2 pl-3">
        <span className="text-sm font-medium" id="servings-label">
          Servings
        </span>
        <div className="flex items-center gap-2" role="group" aria-labelledby="servings-label">
          <Button
            variant="outline"
            size="icon"
            className="size-11"
            aria-label="Fewer servings"
            disabled={servings <= 1}
            onClick={() => setServings((s) => Math.max(1, s - 1))}
          >
            <Minus className="size-5" aria-hidden />
          </Button>
          <output
            aria-live="polite"
            className="w-8 text-center text-lg font-semibold tabular-nums"
          >
            {servings}
          </output>
          <Button
            variant="outline"
            size="icon"
            className="size-11"
            aria-label="More servings"
            disabled={servings >= SERVINGS_MAX}
            onClick={() => setServings((s) => Math.min(SERVINGS_MAX, s + 1))}
          >
            <Plus className="size-5" aria-hidden />
          </Button>
        </div>
      </div>

      <section aria-labelledby="ingredients-heading" className="flex flex-col gap-2">
        <h3 id="ingredients-heading" className="font-medium">
          Ingredients
        </h3>
        {recipe.ingredients.length === 0 ? (
          <p className="text-sm text-muted-foreground">No ingredients yet.</p>
        ) : (
          <ul
            aria-label="Ingredients"
            aria-busy={stale || undefined}
            className="divide-y rounded-xl border"
          >
            {[...recipe.ingredients]
              .sort((a, b) => a.position - b.position)
              .map((line) => (
                <li key={line.id} className="flex items-baseline gap-3 px-3 py-2.5">
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="font-medium">{line.ingredient_name}</span>
                    {line.note && (
                      <span className="text-sm text-muted-foreground">{line.note}</span>
                    )}
                  </span>
                  <span
                    data-testid="scaled-amount"
                    className={
                      stale
                        ? 'text-right text-sm tabular-nums text-muted-foreground'
                        : 'text-right text-sm font-medium tabular-nums'
                    }
                  >
                    {scaledLabel(byPosition.get(line.position), line.unit)}
                  </span>
                </li>
              ))}
          </ul>
        )}
        {scaled.isError && (
          <p role="alert" className="text-sm text-destructive">
            Couldn't scale amounts: {recipeErrorMessage(scaled.error)}
          </p>
        )}
      </section>

      <DeleteRecipe recipe={recipe} />
    </article>
  )
}

function scaledLabel(line: ScaledIngredient | undefined, unit: string): string {
  if (!line) return '…'
  if (line.display) return formatQuantity(line.display.amount, line.display.unit)
  if (line.amount) return formatQuantity(line.amount, unitLabel(line.unit))
  if (line.amount_per_person && Number(line.amount_per_person) > 0) {
    return `${trimAmount(line.amount_per_person)} ${unitLabel(unit)}`
  }
  return unitLabel(unit)
}

function DeleteRecipe({ recipe }: { recipe: Recipe }) {
  const navigate = useNavigate()
  const remove = useDeleteRecipe(recipe.id)
  const [open, setOpen] = useState(false)

  const onDelete = () =>
    remove.mutate(undefined, {
      onSuccess: (result) => {
        setOpen(false)
        if (result.archived) {
          toast.info(`Archived ${recipe.name}`, {
            description: "It's used in the meal plan, so it was archived instead of deleted.",
          })
        } else {
          toast.success(`Deleted ${recipe.name}`)
        }
        navigate('/recipes', { replace: true })
      },
    })

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) remove.reset()
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="destructive" className="h-11">
          <Trash2 aria-hidden /> Delete recipe
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {recipe.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            If it's in the meal plan it will be archived instead, so past meals keep their recipe.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {remove.isError && (
          <p role="alert" className="text-sm text-destructive">
            {recipeErrorMessage(remove.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel className="h-11">Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            className="h-11"
            disabled={remove.isPending}
            onClick={onDelete}
          >
            {remove.isPending ? 'Deleting…' : 'Delete'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
