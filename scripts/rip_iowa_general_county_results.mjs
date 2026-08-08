import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");

const SEARCH_URL = "https://sos.iowa.gov/sitesearch?keys=precinct%20results";
const OUTPUT_ROOT = path.join(repoRoot, "data", "iowa_general_county_precinct_results");
const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

function slugifyCountyName(name) {
  return name
    .toLowerCase()
    .replace(/'/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

async function ensureEdgeExists() {
  try {
    await fs.access(EDGE_PATH);
  } catch {
    throw new Error(`Microsoft Edge not found at ${EDGE_PATH}`);
  }
}

function buildExpectedFilePattern(year) {
  return new RegExp(
    `^https://sos\\.iowa\\.gov/elections/pdf/precinctresults/${year}general/.+\\.xls$`,
    "i"
  );
}

async function collectGeneralPages(page) {
  const discovered = new Map();

  for (let pageIndex = 0; pageIndex < 6; pageIndex += 1) {
    const url = pageIndex === 0 ? SEARCH_URL : `${SEARCH_URL}&page=${pageIndex}`;
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});

    const pageLinks = await page.locator("a").evaluateAll((anchors) =>
      anchors
        .map((anchor) => ({
          text: (anchor.textContent || "").replace(/\u00a0/g, " ").trim(),
          href: anchor.href,
        }))
        .filter(
          (link) =>
            /^Precinct Results By County - \d{4} General$/i.test(link.text) ||
            /^Read more about Precinct Results By County - \d{4} General$/i.test(link.text) ||
            /^\d{4} General Election Precinct Results by County/i.test(link.text)
        )
    );

    for (const link of pageLinks) {
      const yearMatch = link.text.match(/(20\d{2}|2008|2010|2012|2014|2016|2018)/);
      if (!yearMatch) {
        continue;
      }

      const year = yearMatch[1];
      if (!discovered.has(year)) {
        discovered.set(year, {
          year,
          landingUrl: link.href,
          title: link.text.replace(/^Read more about /i, ""),
        });
      }
    }

    const bodyText = await page.locator("body").innerText();
    if (!bodyText.includes("Next page")) {
      break;
    }
  }

  return [...discovered.values()].sort((a, b) => Number(a.year) - Number(b.year));
}

async function extractCountyLinks(page, year) {
  const filePattern = buildExpectedFilePattern(year);
  const links = await page.locator("a").evaluateAll(
    (anchors, patternSource) =>
      anchors
        .map((anchor) => ({
          county: (anchor.textContent || "").replace(/\u00a0/g, " ").trim(),
          href: anchor.href,
        }))
        .filter((link) => {
          if (!link.county || !link.href) {
            return false;
          }

          const pattern = new RegExp(patternSource, "i");
          return pattern.test(link.href);
        }),
    filePattern.source
  );

  const deduped = new Map();
  for (const link of links) {
    deduped.set(link.county, link);
  }

  return [...deduped.values()].sort((a, b) => a.county.localeCompare(b.county));
}

async function writeYearManifest(yearDir, metadata) {
  await fs.writeFile(
    path.join(yearDir, "manifest.json"),
    JSON.stringify(metadata, null, 2)
  );
}

async function downloadCountyFiles(context, yearDir, countyLinks) {
  for (const link of countyLinks) {
    const response = await context.request.get(link.href, {
      failOnStatusCode: false,
    });

    if (!response.ok()) {
      throw new Error(
        `Failed to download ${link.county}: ${response.status()} ${response.statusText()}`
      );
    }

    const filename = `${slugifyCountyName(link.county)}.xls`;
    await fs.writeFile(path.join(yearDir, filename), await response.body());
    process.stdout.write(`Saved ${filename}\n`);
  }
}

async function ripYear(context, page, yearInfo) {
  await page.goto(yearInfo.landingUrl, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});

  const finalUrl = page.url();
  const countyLinks = await extractCountyLinks(page, yearInfo.year);

  if (countyLinks.length === 0) {
    throw new Error(`No county .xls links found for ${yearInfo.year} at ${finalUrl}`);
  }

  const yearDir = path.join(OUTPUT_ROOT, yearInfo.year);
  await fs.mkdir(yearDir, { recursive: true });

  await writeYearManifest(yearDir, {
    year: yearInfo.year,
    title: yearInfo.title,
    landingUrl: yearInfo.landingUrl,
    finalUrl,
    countyCount: countyLinks.length,
    counties: countyLinks,
  });

  await downloadCountyFiles(context, yearDir, countyLinks);

  return {
    year: yearInfo.year,
    finalUrl,
    countyCount: countyLinks.length,
  };
}

async function main() {
  await ensureEdgeExists();
  await fs.mkdir(OUTPUT_ROOT, { recursive: true });

  const browser = await chromium.launch({
    executablePath: EDGE_PATH,
    headless: true,
  });

  try {
    const context = await browser.newContext();
    const page = await context.newPage();

    const generalPages = await collectGeneralPages(page);
    if (generalPages.length === 0) {
      throw new Error("No Iowa SOS general-election precinct result pages were discovered.");
    }

    const summary = [];
    for (const yearInfo of generalPages) {
      process.stdout.write(`\nRipping ${yearInfo.year} from ${yearInfo.landingUrl}\n`);
      summary.push(await ripYear(context, page, yearInfo));
    }

    await fs.writeFile(
      path.join(OUTPUT_ROOT, "summary.json"),
      JSON.stringify(summary, null, 2)
    );

    process.stdout.write(`\nFinished ${summary.length} general-election years.\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
