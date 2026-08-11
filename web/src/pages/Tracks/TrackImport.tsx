import { useCallback, useId, useState } from 'react'
import { api, type ImportOutcome } from '../../api/client'
import { explainImportError } from '../../api/explanations'
import { describe } from '../../api/useRequest'

/**
 * Offering files to the archive from the browser.
 *
 * One request per file, and one verdict per file. Four things can happen to an
 * offered file and only one of them is "done": the archive already held it, the
 * archive had lost its copy and restored it from these bytes, or the archive
 * could not read it at all. A control that answered "uploaded" to all four
 * would be wrong in exactly the cases somebody has to act on.
 *
 * Nothing here decides anything. The bytes go to the canonical import use case
 * and this renders the outcome the archive returned, in the archive's own
 * vocabulary.
 */
export function TrackImport({
  enabled,
  onImported,
}: {
  /** Whether this deployment accepts files at all. */
  enabled: boolean
  /** Called once something new actually arrived, so a listing can catch up. */
  onImported: () => void
}) {
  const inputId = useId()
  const [results, setResults] = useState<readonly Result[]>([])
  const [busy, setBusy] = useState(false)

  const offer = useCallback(
    async (files: readonly File[]) => {
      setBusy(true)
      setResults(files.map((file) => ({ name: file.name, state: 'sending' })))
      let arrived = false
      for (const [index, file] of files.entries()) {
        const result = await offerOne(file)
        arrived = arrived || result.state === 'outcome'
          ? arrived || (result.state === 'outcome' && brought(result.outcome))
          : arrived
        setResults((current) => current.map((entry, at) => (at === index ? result : entry)))
      }
      setBusy(false)
      // Only when the archive actually gained something. A duplicate changed
      // nothing, and refetching the list to show the same rows is noise.
      if (arrived) onImported()
    },
    [onImported],
  )

  if (!enabled) {
    return (
      <p className="muted" data-testid="import-disabled">
        This deployment does not accept uploads. Tracks are imported on the machine that holds the
        data, with <code>gpx-view import</code> or <code>gpx-view scan</code>.
      </p>
    )
  }

  return (
    <div className="panel track-import">
      <label htmlFor={inputId}>Choose files to import</label>
      <input
        id={inputId}
        type="file"
        multiple
        accept=".gpx,application/gpx+xml"
        disabled={busy}
        onChange={(event) => {
          const chosen = [...(event.target.files ?? [])]
          // Cleared so that offering the same file twice in a row is two
          // attempts rather than one silent no-op.
          event.target.value = ''
          if (chosen.length > 0) void offer(chosen)
        }}
      />
      {results.length > 0 && (
        <ul className="track-import__results" data-testid="import-result">
          {results.map((result) => (
            <li key={result.name}>
              <span className="track-import__name">{result.name}</span>
              <span className="muted"> — {sentence(result)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type Result =
  | { name: string; state: 'sending' }
  | { name: string; state: 'outcome'; outcome: ImportOutcome }
  | { name: string; state: 'refused'; message: string }

/** Whether an outcome means the archive now holds something it did not before. */
function brought(outcome: ImportOutcome): boolean {
  return outcome.status === 'imported' || outcome.status === 'repaired'
}

async function offerOne(file: File): Promise<Result> {
  try {
    return { name: file.name, state: 'outcome', outcome: await api.importFile(file) }
  } catch (cause: unknown) {
    // A refused *request* -- too large, disabled, unreachable. Different from a
    // file the archive read and could not use, and it reads differently too.
    return { name: file.name, state: 'refused', message: describe(cause) }
  }
}

/**
 * One line saying what became of one file.
 *
 * The archive's four outcomes in words somebody can act on. "Duplicate" is the
 * one worth spelling out: it is not a failure and it is not an import, and
 * calling it either sends the reader looking for something that is not there.
 */
function sentence(result: Result): string {
  if (result.state === 'sending') return 'sending…'
  if (result.state === 'refused') return result.message
  const outcome = result.outcome
  if (outcome.status === 'imported') return 'imported'
  if (outcome.status === 'duplicate') return 'already in the archive; nothing to do'
  if (outcome.status === 'repaired') return 'already known; the archive restored its own copy'
  return `not imported: ${explainImportError(outcome.error_code)}`
}
