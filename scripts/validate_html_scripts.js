const fs = require('node:fs');
const path = require('node:path');

const projectRoot = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(projectRoot, 'index.html'), 'utf8');
const helperScripts = [
  'atlas-classification.js',
  'atlas-core.js',
  'atlas-data.js',
  'atlas-display.js',
  'atlas-election.js',
  'atlas-manifest.js',
  'atlas-regions.js',
  'atlas-trends.js',
  'atlas-turnout.js',
  'atlas-url-state.js'
];

const failures = [];
for (const filename of helperScripts) {
  const escapedFilename = filename.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const scriptTag = new RegExp(`<script\\s+src=["']\\./js/${escapedFilename}(?:\\?[^"']*)?["']\\s*><\\/script>`, 'i');
  if (!scriptTag.test(html)) failures.push(`missing valid script tag for js/${filename}`);
}

if (/^\s*\$1[^\r\n]*<\/script>/m.test(html)) {
  failures.push('found a literal $1 replacement artifact before </script>');
}

if (failures.length) {
  console.error(`HTML helper-script validation failed:\n- ${failures.join('\n- ')}`);
  process.exitCode = 1;
} else {
  console.log(`Validated ${helperScripts.length} helper script tags in index.html.`);
}
