/** Where the user guide lives. Pages are Markdown files in the repository. */
export const DOCS_BASE_URL = 'https://github.com/piotrekno1/second-shift/blob/main/docs'

/** The guide's starting point, which links to every page. */
export const DOCS_HOME_URL = 'https://github.com/piotrekno1/second-shift#readme'

export const DocsPage = {
  DataHandling: 'data-handling.md',
  RepositoryConfiguration: 'repository-configuration.md',
  SecurityModel: 'security-model.md',
} as const
export type DocsPage = (typeof DocsPage)[keyof typeof DocsPage]

/** A docs page, with an optional section anchor. */
export function docsUrl(page: DocsPage, section?: string): string {
  return `${DOCS_BASE_URL}/${page}${section ? `#${section}` : ''}`
}

/** The run report a webhook receives, field by field. */
export const WEBHOOK_PAYLOAD_DOCS_URL = docsUrl(DocsPage.RepositoryConfiguration, 'webhook-payload')
