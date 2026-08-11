import { useEffect, useId, useRef, useState } from 'react'
import { api, type Track } from '../../api/client'
import { describe } from '../../api/useRequest'
import { displayTitle, isFallbackTitle } from '../../api/titles'

const MAX_TITLE = 200
const MAX_NOTE = 4000

/**
 * Renaming a track, and keeping a note about it.
 *
 * The correction never touches the source: the file keeps saying what it said,
 * and clearing the field hands the display back to it. That is why "Reset" is a
 * real action here rather than a rename to something empty.
 *
 * No modal. The action is reversible, it affects one track, and a dialog for
 * every rename is a dialog people stop reading. The editor opens in place, the
 * focus moves into it, and Escape leaves without saving.
 */
export function TitleEditor({
  track,
  onSaved,
}: {
  track: Track
  onSaved: (updated: Track) => void
}) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(track.metadata.title ?? '')
  const [note, setNote] = useState(track.metadata.note ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const field = useRef<HTMLInputElement | null>(null)
  const titleId = useId()
  const noteId = useId()

  useEffect(() => {
    if (editing) field.current?.focus()
  }, [editing])

  const open = () => {
    setTitle(track.metadata.title ?? '')
    setNote(track.metadata.note ?? '')
    setError(null)
    setEditing(true)
  }

  const save = async (next: { title: string | null; note: string | null }) => {
    setSaving(true)
    setError(null)
    try {
      onSaved(await api.updateMetadata(track.id, next))
      setEditing(false)
    } catch (cause: unknown) {
      setError(describe(cause))
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <div className="title-row">
        <h1 data-testid="track-title">{displayTitle(track)}</h1>
        <button type="button" onClick={open} data-testid="edit-title">
          Edit title
        </button>
        {isFallbackTitle(track) && (
          <p className="muted" data-testid="fallback-title-note">
            This track has no title of its own; the archive is describing it.
          </p>
        )}
        {track.metadata.note !== null && (
          <p className="track-note" data-testid="track-note">
            {track.metadata.note}
          </p>
        )}
      </div>
    )
  }

  return (
    <form
      className="title-editor"
      data-testid="title-editor"
      onSubmit={(event) => {
        event.preventDefault()
        void save({ title: title.trim() === '' ? null : title, note: note.trim() === '' ? null : note })
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setEditing(false)
      }}
    >
      <div className="field">
        <label htmlFor={titleId}>Title</label>
        <input
          id={titleId}
          ref={field}
          value={title}
          maxLength={MAX_TITLE}
          placeholder={track.metadata.source_title ?? 'No title in the source file'}
          onChange={(event) => {
            setTitle(event.target.value)
          }}
          data-testid="title-input"
        />
        <span className="muted">
          Leave empty to use the title in the file
          {track.metadata.source_title === null && ' — this file has none'}.
        </span>
      </div>
      <div className="field">
        <label htmlFor={noteId}>Note</label>
        <textarea
          id={noteId}
          value={note}
          maxLength={MAX_NOTE}
          rows={3}
          onChange={(event) => {
            setNote(event.target.value)
          }}
          data-testid="note-input"
        />
      </div>
      {error !== null && (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      )}
      <div className="pager">
        <button type="submit" className="primary" disabled={saving} data-testid="save-title">
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          disabled={saving}
          onClick={() => {
            setEditing(false)
          }}
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={saving || !track.metadata.is_overridden}
          onClick={() => {
            void save({ title: null, note: note.trim() === '' ? null : note })
          }}
          data-testid="reset-title"
        >
          Reset to source title
        </button>
      </div>
    </form>
  )
}
