import DOMPurify from 'dompurify'
import { marked } from 'marked'

// Run files hold repository and model text, so rendered HTML is always
// sanitized. Images and embeds are dropped so a file cannot load remote content.
const FORBIDDEN_TAGS = ['img', 'svg', 'math', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'style']

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer nofollow')
  }
})

/** Render sanitized HTML. `link` may rewrite each link target first. */
export function renderMarkdown(text: string, link?: (href: string) => string): string {
  const html = marked.parse(text, {
    async: false,
    gfm: true,
    walkTokens: link
      ? (token) => {
          if (token.type === 'link') token.href = link(token.href)
        }
      : null,
  })
  return DOMPurify.sanitize(html, { FORBID_TAGS: FORBIDDEN_TAGS, FORBID_ATTR: ['style'] })
}
