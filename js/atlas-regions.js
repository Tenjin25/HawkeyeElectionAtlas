(function initializeAtlasRegions(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasRegions = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasRegions() {
  const IOWA_COUNTY_ALIASES = Object.freeze({
    OBRIEN: 'OBRIEN',
    OBREIN: 'OBRIEN',
    VAN: 'VAN BUREN',
    VANBUREN: 'VAN BUREN'
  });

  function normalizeCountyName(name) {
    const normalized = (name || '')
      .toString()
      .replace(/\s+COUNTY$/i, '')
      .replace(/[^a-z0-9 .\-]/gi, '')
      .replace(/\s+/g, ' ')
      .trim()
      .toUpperCase();
    const compact = normalized.replace(/[^A-Z0-9]/g, '');
    return IOWA_COUNTY_ALIASES[compact] || normalized;
  }

  function countyFeatureNameExpression() {
    const rawName = [
      'upcase',
      ['coalesce',
        ['get', 'county_norm'],
        ['get', 'NAME20'],
        ['get', 'CountyName'],
        ['get', 'COUNTYNAME'],
        ['get', 'county_nam'],
        ['get', 'NAME'],
        ['get', 'County'],
        ['get', 'name'],
        ''
      ]
    ];

    // Mapbox expressions cannot call normalizeCountyName(). Canonicalize the
    // source spellings here so paint/filter joins use the election-data keys.
    return [
      'match',
      rawName,
      ["O'BRIEN", 'O\u2019BRIEN', 'OBREIN'],
      'OBRIEN',
      ['VAN', 'VANBUREN'],
      'VAN BUREN',
      rawName
    ];
  }

  function getCountySet(counties) {
    return new Set(
      (Array.isArray(counties) ? counties : [])
        .map(normalizeCountyName)
        .filter(Boolean)
    );
  }

  function aggregateContestRows(rows, contestType, counties) {
    const countySet = getCountySet(counties);
    const type = String(contestType || '').trim();
    let dem = 0;
    let rep = 0;
    let other = 0;
    let total = 0;
    let demCandidate = '';
    let repCandidate = '';
    const matchedCounties = new Set();

    (Array.isArray(rows) ? rows : []).forEach(row => {
      const countyRaw = ((row?.county || '').toString().split(' - ')[0] || '').trim();
      const countyNorm = normalizeCountyName(countyRaw);
      if (!countyNorm || !countySet.has(countyNorm)) return;

      matchedCounties.add(countyNorm);
      dem += Number(row?.[`${type}_dem`] || 0);
      rep += Number(row?.[`${type}_rep`] || 0);
      other += Number(row?.[`${type}_other`] || 0);
      total += Number(row?.[`${type}_total`] || 0);
      if (!demCandidate) {
        demCandidate = (row?.[`${type}_dem_candidate`] || '').toString().trim();
      }
      if (!repCandidate) {
        repCandidate = (row?.[`${type}_rep_candidate`] || '').toString().trim();
      }
    });

    return {
      dem,
      rep,
      other,
      total,
      demCandidate,
      repCandidate,
      matchedCounties: matchedCounties.size,
      totalCounties: countySet.size
    };
  }

  return {
    normalizeCountyName,
    countyFeatureNameExpression,
    getCountySet,
    aggregateContestRows
  };
}));
