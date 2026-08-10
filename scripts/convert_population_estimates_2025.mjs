import fs from 'node:fs/promises';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const inputPath = 'data/co-est2025-pop-19.xlsx';
const outputPath = 'data/county_population_estimates_2025.csv';

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItemAt(0);
const values = sheet.getUsedRange().values;

const headers = [
  'county_name', 'county_norm', 'population_2020', 'population_2021',
  'population_2022', 'population_2023', 'population_2024', 'population_2025',
  'change_2020_2025', 'change_2020_2025_pct', 'change_2024_2025', 'change_2024_2025_pct'
];

function cleanName(raw) {
  const text = String(raw ?? '').trim().replace(/^\.+/, '').trim();
  if (!text) return '';
  if (text === 'Iowa') return 'Iowa';
  return text.replace(/,\s*Iowa$/i, '').replace(/\s+County$/i, '').trim();
}

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const rows = [];
for (const sourceRow of values.slice(4)) {
  const countyName = cleanName(sourceRow?.[0]);
  if (!countyName || !(/^(Iowa|\.?[A-Za-z' -]+ County, Iowa)$/i.test(String(sourceRow?.[0] ?? '').trim()))) continue;
  const population2020 = number(sourceRow[2]);
  const population2021 = number(sourceRow[3]);
  const population2022 = number(sourceRow[4]);
  const population2023 = number(sourceRow[5]);
  const population2024 = number(sourceRow[6]);
  const population2025 = number(sourceRow[7]);
  if (![population2020, population2021, population2022, population2023, population2024, population2025].every(v => v !== null)) continue;
  const change2020To2025 = population2025 - population2020;
  const change2024To2025 = population2025 - population2024;
  const countyNorm = countyName === 'Iowa' && String(sourceRow?.[0] ?? '').trim() === 'Iowa'
    ? 'IOWA STATE'
    : countyName.toUpperCase();
  rows.push([
    countyName,
    countyNorm,
    population2020,
    population2021,
    population2022,
    population2023,
    population2024,
    population2025,
    change2020To2025,
    (change2020To2025 / population2020) * 100,
    change2024To2025,
    (change2024To2025 / population2024) * 100,
  ]);
}

if (rows.length !== 100 || rows[0]?.[0] !== 'Iowa') {
  throw new Error(`Unexpected cleaned row count/order: ${rows.length}, first=${rows[0]?.[0]}`);
}

function csvCell(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

const csv = [headers, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
await fs.writeFile(outputPath, csv, 'utf8');
console.log(`Wrote ${outputPath} with ${rows.length} rows and ${headers.length} columns.`);
console.log(rows.slice(0, 3));
