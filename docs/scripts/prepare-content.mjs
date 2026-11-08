import { readFile, readdir, mkdir, writeFile, copyFile, cp } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const output = new URL('../src/content/docs/docs/', import.meta.url);
const publicScreenshots = new URL('../public/screenshots/', import.meta.url);
const publicImages = new URL('../public/images/', import.meta.url);
const publicMark = new URL('../public/secondshift-mark.svg', import.meta.url);
const publicFavicon = new URL('../public/favicon.svg', import.meta.url);
const publicShareImage = new URL('../public/og.png', import.meta.url);
await mkdir(output, { recursive: true });
await mkdir(publicScreenshots, { recursive: true });
await copyFile(new URL('../../dashboard/public/secondshift-mark.svg', import.meta.url), publicMark);
await copyFile(new URL('../../dashboard/public/favicon.svg', import.meta.url), publicFavicon);
await copyFile(new URL('../src/assets/og.png', import.meta.url), publicShareImage);
await copyFile(new URL('../src/assets/apple-touch-icon.png', import.meta.url), new URL('../public/apple-touch-icon.png', import.meta.url));
const files = (await readdir(root)).filter((name) => name.endsWith('.md'));
for (const name of files) {
  let body = await readFile(new URL(name, root), 'utf8');
  const heading = body.match(/^# (.+)\n/);
  const title = heading?.[1] ?? name.replace(/\.md$/, '').replaceAll('-', ' ');
  if (heading) body = body.slice(heading[0].length).trimStart();
  body = body.replace(/^\[README\][^\n]*\n\n/, '');
  body = body.replace(/\]\((?:\.\/)?([\w-]+)\.md(#[^)]*)?\)/g, (_, slug, hash = '') => `](/docs/${slug}/${hash})`);
  body = body.replace(/\]\(\.\.\/examples\/README\.md\)/g, '](/docs/skill-catalog/)');
  body = body.replace(/\]\(\.\.\/README\.md(#[^)]*)?\)/g, (_, hash = '') => `](https://github.com/piotrekno1/second-shift/blob/main/README.md${hash})`);
  body = body.replace(/\]\(\.\.\/([^)]*)\)/g, (_, path) => `](https://github.com/piotrekno1/second-shift/blob/main/${path})`);
  body = body.replace(/\]\(screenshots\//g, '](/screenshots/');
  body = body.replace(/\]\(images\//g, '](/images/');
  await writeFile(new URL(name, output), `---\ntitle: ${JSON.stringify(title)}\n---\n\n${body}`);
}
// The skill catalog page comes from examples/README.md. Example links point at
// their folders on GitHub, and docs links stay on the site.
const catalog = await readFile(new URL('../examples/README.md', root), 'utf8');
const catalogHeading = catalog.match(/^# (.+)\n/);
let catalogBody = catalogHeading ? catalog.slice(catalogHeading[0].length).trimStart() : catalog;
catalogBody = catalogBody.replace(/\]\(\.\.\/docs\/([\w-]+)\.md(#[^)]*)?\)/g, (_, slug, hash = '') => `](/docs/${slug}/${hash})`);
catalogBody = catalogBody.replace(/\]\(([\w-]+)\/README\.md\)/g, (_, name) => `](https://github.com/piotrekno1/second-shift/tree/main/examples/${name})`);
await writeFile(new URL('skill-catalog.md', output), `---\ntitle: ${JSON.stringify(catalogHeading?.[1] ?? 'Skill catalog')}\n---\n\n${catalogBody}`);
await cp(new URL('../screenshots/', import.meta.url), publicScreenshots, { recursive: true });
await cp(new URL('../images/', import.meta.url), publicImages, { recursive: true });
