/** Where the user guide lives. */
export const DOCS_BASE_URL = 'https://second-shift.app/docs'

/** The guide's starting point, which links to every page. */
export const DOCS_HOME_URL = `${DOCS_BASE_URL}/overview/`

export const DocsPage = {
  RepositoryConfiguration: 'repository-configuration',
  SecurityModel: 'security-model',
} as const
export type DocsPage = (typeof DocsPage)[keyof typeof DocsPage]

/** A docs page, with an optional section anchor. */
export function docsUrl(page: DocsPage, section?: string): string {
  return `${DOCS_BASE_URL}/${page}/${section ? `#${section}` : ''}`
}
