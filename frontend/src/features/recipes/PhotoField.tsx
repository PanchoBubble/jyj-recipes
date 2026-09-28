import { Camera, ImagePlus, Search, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
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
import { Button } from '@/components/ui/button'
import type { Recipe } from '@/features/recipes/api'
import { photoErrorMessage, useRemoveRecipePhoto, useUploadRecipePhoto } from '@/features/recipes/photo'
import { PhotoCredit, PhotoSearchSheet } from '@/features/recipes/PhotoSearchSheet'
import { RecipePhoto } from '@/features/recipes/RecipePhoto'

type Props =
  | {
      /** Saved recipe: a picked photo is previewed, then uploaded on "Upload photo". */
      recipe: Recipe
      label?: string
      hint?: string
    }
  | {
      /** New recipe: the editor holds the picked file and uploads it after create. */
      recipe: null
      pending: File | null
      onPendingChange: (file: File | null) => void
      progress: number | null
      label?: string
      hint?: string
    }

export function PhotoField(props: Props) {
  const id = useId()
  const libraryInput = useRef<HTMLInputElement>(null)
  const cameraInput = useRef<HTMLInputElement>(null)
  const upload = useUploadRecipePhoto()
  const [ownPending, setOwnPending] = useState<File | null>(null)
  const [ownProgress, setOwnProgress] = useState<number | null>(null)
  const [searching, setSearching] = useState(false)

  const recipe = props.recipe
  const pending = recipe ? ownPending : props.pending
  const setPending = recipe ? setOwnPending : props.onPendingChange
  const progress = recipe ? ownProgress : props.progress
  const busy = progress !== null
  const name = recipe?.name ?? 'New recipe'
  const hasPhoto = Boolean(recipe?.photo_url)

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    // Cleared so picking the same file again still fires change.
    event.target.value = ''
    if (file) setPending(file)
  }

  const onUpload = () => {
    if (!recipe || !pending) return
    setOwnProgress(0)
    upload.mutate(
      { id: recipe.id, file: pending, onProgress: setOwnProgress },
      {
        onSuccess: () => {
          setOwnPending(null)
          toast.success('Photo saved')
        },
        onError: (error) =>
          toast.error("Couldn't upload the photo", { description: photoErrorMessage(error) }),
        onSettled: () => setOwnProgress(null),
      },
    )
  }

  return (
    <section
      aria-labelledby={props.label ? `${id}-label` : undefined}
      aria-label={props.label ? undefined : 'Photo'}
      className="flex flex-col gap-2"
    >
      {props.label && (
        <h2 id={`${id}-label`} className="text-sm font-medium">
          {props.label}
        </h2>
      )}
      <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-muted">
        {pending ? (
          <PendingPreview file={pending} />
        ) : (
          <RecipePhoto
            key={recipe?.photo_url ?? 'none'}
            src={recipe?.photo_url}
            alt={name}
            width={1600}
            height={900}
            className="size-full"
          />
        )}
        {busy && <UploadProgress value={progress} />}
      </div>
      {!pending && recipe?.photo_url && recipe.photo_credit && (
        <PhotoCredit credit={recipe.photo_credit} />
      )}

      <div className="flex flex-wrap gap-2">
        {pending ? (
          recipe ? (
            <>
              <Button type="button" className="h-11 flex-1" disabled={busy} onClick={onUpload}>
                <Upload aria-hidden /> {busy ? 'Uploading…' : 'Upload photo'}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-11"
                disabled={busy}
                onClick={() => setPending(null)}
              >
                <X aria-hidden /> Cancel
              </Button>
            </>
          ) : (
            <Button
              type="button"
              variant="outline"
              className="h-11"
              disabled={busy}
              onClick={() => setPending(null)}
            >
              <X aria-hidden /> Don't use this photo
            </Button>
          )
        ) : (
          <>
            <Button
              type="button"
              variant="outline"
              className="h-11 flex-1"
              onClick={() => libraryInput.current?.click()}
            >
              <ImagePlus aria-hidden /> {hasPhoto ? 'Replace photo' : 'Choose photo'}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="h-11 flex-1"
              onClick={() => cameraInput.current?.click()}
            >
              <Camera aria-hidden /> Take photo
            </Button>
            {recipe && (
              <Button
                type="button"
                variant="outline"
                className="h-11 flex-1"
                onClick={() => setSearching(true)}
              >
                <Search aria-hidden /> Find a photo
              </Button>
            )}
            {recipe && hasPhoto && <RemovePhoto recipe={recipe} />}
          </>
        )}
      </div>
      {props.hint && <p className="text-xs text-muted-foreground">{props.hint}</p>}
      {recipe && <PhotoSearchSheet recipe={recipe} open={searching} onOpenChange={setSearching} />}

      <input
        ref={libraryInput}
        type="file"
        accept="image/*"
        aria-label="Photo file from library"
        tabIndex={-1}
        className="sr-only"
        onChange={onPick}
      />
      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="environment"
        aria-label="Photo file from camera"
        tabIndex={-1}
        className="sr-only"
        onChange={onPick}
      />
    </section>
  )
}

function PendingPreview({ file }: { file: File }) {
  const img = useRef<HTMLImageElement>(null)
  const [brokenFile, setBrokenFile] = useState<File | null>(null)

  // Set imperatively so the object URL is created and revoked by the same effect run,
  // which keeps StrictMode's double mount from showing a revoked URL.
  useEffect(() => {
    const url = URL.createObjectURL(file)
    if (img.current) img.current.src = url
    return () => URL.revokeObjectURL(url)
  }, [file])

  const broken = brokenFile === file
  return (
    <>
      <img
        ref={img}
        alt="Selected photo preview"
        hidden={broken}
        onError={() => setBrokenFile(file)}
        className="size-full object-cover"
      />
      {broken && (
        <div className="flex size-full flex-col items-center justify-center gap-1 p-4 text-center text-sm text-muted-foreground">
          <span className="font-medium break-all text-foreground">{file.name}</span>
          <span>No preview for this format, but it can still be uploaded.</span>
        </div>
      )}
    </>
  )
}

function UploadProgress({ value }: { value: number | null }) {
  const percent = Math.round((value ?? 0) * 100)
  return (
    <div className="absolute inset-x-0 bottom-0 flex flex-col gap-1 bg-background/85 px-3 py-2 backdrop-blur">
      <div className="flex justify-between text-xs font-medium">
        <span>Uploading photo…</span>
        <span className="tabular-nums">{percent}%</span>
      </div>
      <div
        role="progressbar"
        aria-label="Photo upload"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-1.5 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
      </div>
    </div>
  )
}

function RemovePhoto({ recipe }: { recipe: Recipe }) {
  const remove = useRemoveRecipePhoto(recipe.id)
  const [open, setOpen] = useState(false)

  const onRemove = () =>
    remove.mutate(undefined, {
      onSuccess: () => {
        setOpen(false)
        toast.success('Photo removed')
      },
      onError: (error) =>
        toast.error("Couldn't remove the photo", { description: photoErrorMessage(error) }),
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
        <Button type="button" variant="outline" className="h-11 text-destructive">
          <Trash2 aria-hidden /> Remove photo
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove the photo?</AlertDialogTitle>
          <AlertDialogDescription>
            {recipe.name} will show a placeholder until you add a new one.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel className="h-11">Cancel</AlertDialogCancel>
          <Button
            type="button"
            variant="destructive"
            className="h-11"
            disabled={remove.isPending}
            onClick={onRemove}
          >
            {remove.isPending ? 'Removing…' : 'Remove'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
