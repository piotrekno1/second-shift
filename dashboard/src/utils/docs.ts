/** Where the user guide lives. Pages are Markdown files in the repository. */
export const DOCS_BASE_URL = 'https://github.com/piotrekno1/second-shift/blob/main/docs'

/** A docs page, with an optional section anchor. */
export function docsUrl(page: string, section?: string): string {
  return `${DOCS_BASE_URL}/${page}${section ? `#${section}` : ''}`
}
