export interface Frontmatter {
  fields: { key: string, value: string }[]
  body: string
}

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/

/**
 * Splits the `---` header block off a skill file. Only top-level `key: value`
 * lines are read. Indented lines after `key: >` or `key: |` join the value.
 */
export function splitFrontmatter(text: string): Frontmatter {
  const match = FRONTMATTER.exec(text)
  if (!match) return { fields: [], body: text }
  const fields: Frontmatter['fields'] = []
  for (const line of match[1].split(/\r?\n/)) {
    const field = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line)
    if (field) {
      const value = ['>', '|', '>-', '|-'].includes(field[2]) ? '' : field[2]
      fields.push({ key: field[1], value: unquote(value) })
    } else if (/^\s+\S/.test(line) && fields.length) {
      const last = fields[fields.length - 1]
      last.value = `${last.value} ${line.trim()}`.trim()
    }
  }
  return { fields, body: text.slice(match[0].length) }
}

function unquote(value: string): string {
  const quoted = /^(['"])(.*)\1$/.exec(value)
  return quoted ? quoted[2] : value
}
