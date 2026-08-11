/**
 * The archive's stable codes, in words a reader has not read the schema for.
 *
 * `gps_accuracy_present` and `large_unobserved_gaps` are exactly right as
 * stored data: they are stable identifiers, they never change meaning, and
 * renaming one would invalidate every stored explanation. They are also not
 * English, and a detail page that prints them is a page that explains a verdict
 * in the vocabulary of the thing being explained.
 *
 * So the codes stay the contract and this is the translation. Two rules:
 *
 * - **No XML, no namespaces, no schema names.** A reader wants to know that a
 *   receiver reported how well it was measuring, not which element carried it.
 * - **An unknown code is shown as itself.** A build that meets a code it has no
 *   words for prints the code rather than hiding the evidence, because the
 *   evidence is why the archive decided what it decided.
 */

const EVIDENCE: Record<string, string> = {
  track_element_present: 'The document described this as a track',
  route_element_present: 'The document described this as a planned route',
  route_instructions_present: 'It carries turn-by-turn navigation instructions',
  timestamps_present: 'Its positions carry times',
  timestamps_absent: 'Its positions carry no times',
  gps_accuracy_present: 'A receiver reported how well it was measuring',
  course_measurements_present: 'A device reported the direction it was heading',
  measurement_metadata_absent: 'Nothing measured anything about these positions',
  external_link_present: 'The document links to a related web page',
  activity_metadata_present: 'The document states which activity this was',
  source_link_present: 'The document links to a related web page',
}

/** Explain one evidence code, or hand it back if this build has no words for it. */
export function explainEvidence(code: string): string {
  return EVIDENCE[code] ?? code
}

const QUALITY: Record<string, string> = {
  missing_timestamps: 'Some positions carry no time, so part of the track has no timing',
  non_monotonic_timestamps: 'Time runs backwards somewhere; those stretches were left out',
  large_unobserved_gaps: 'The recording stopped for longer than its own sampling explains',
  speed_outliers_excluded:
    'Some intervals were too fast to believe and were left out of the speed figures',
  insufficient_temporal_data: 'There was not enough timing to derive a duration or a speed',
  insufficient_elevation_data: 'There was not enough altitude data to derive a profile',
}

/** Explain one analysis quality flag in a sentence. */
export function explainQuality(flag: string): string {
  return QUALITY[flag] ?? flag
}

/**
 * Which side of the classification an observation supported.
 *
 * Shown beside the explanation so a verdict reads as a weighing rather than as
 * a pronouncement. The archive's own rules decide; this only says which way
 * each observation pointed, and `neither` is a real answer -- an external link
 * is provenance and was deliberately taken off both sides.
 */
export function evidenceLeaning(code: string): 'recorded' | 'planned' | 'neither' {
  if (code === 'gps_accuracy_present' || code === 'course_measurements_present') return 'recorded'
  if (
    code === 'route_element_present' ||
    code === 'route_instructions_present' ||
    code === 'measurement_metadata_absent' ||
    code === 'timestamps_absent'
  ) {
    return 'planned'
  }
  return 'neither'
}
