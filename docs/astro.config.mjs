import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://second-shift.app',
  integrations: [starlight({
    title: 'SecondShift',
    customCss: ['./src/styles/docs.css'],
    head: [
      { tag: 'meta', attrs: { property: 'og:image', content: 'https://second-shift.app/og.png' } },
      { tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
      { tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
      { tag: 'meta', attrs: { name: 'twitter:image', content: 'https://second-shift.app/og.png' } },
    ],
    social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/piotrekno1/second-shift' }],
    sidebar: [
      { label: 'Start', items: [{ label: 'Overview', slug: 'docs/overview' }, { label: 'Try locally', slug: 'docs/quickstart' }, { label: 'Add a task', slug: 'docs/engineering-tasks' }] },
      { label: 'Set up', items: [{ label: 'Repository config', slug: 'docs/repository-configuration' }, { label: 'GitHub App', slug: 'docs/github-app' }, { label: 'Deploy', slug: 'docs/production-deployment' }] },
      { label: 'Reference', collapsed: true, items: [{ label: 'Self-hosting', slug: 'docs/self-hosting' }, { label: 'Environment variables', slug: 'docs/environment-variables' }, { label: 'MCP server', slug: 'docs/mcp' }, { label: 'Security', slug: 'docs/security-model' }, 'docs/architecture', 'docs/comparisons'] },
    ],
  })],
});
