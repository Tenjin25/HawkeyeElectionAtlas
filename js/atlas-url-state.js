(function initializeAtlasURLState(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasURLState = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasURLState() {
  const VIEW_VALUES = new Set(['counties', 'districts', 'state_house', 'state_senate', 'vtds_2000']);
  const MODE_VALUES = new Set(['margins', 'winners', 'shift', 'flips', 'demographics', 'population_change']);

  function normalizeViewToken(raw) {
    const token = String(raw || '').trim().toLowerCase();
    return VIEW_VALUES.has(token) ? token : '';
  }

  function normalizeModeToken(raw) {
    const token = String(raw || '').trim().toLowerCase();
    return MODE_VALUES.has(token) ? token : '';
  }

  function normalizeDistrictLinesYear(raw) {
    // This static build only includes Iowa's 2022 Plan 2 geometry and slices.
    // Treat legacy/shared URLs for later line years as the available 2022 layer.
    return 2022;
  }

  function parse(search = '') {
    const params = new URLSearchParams(String(search || ''));
    const view = normalizeViewToken(params.get('view'));
    const contest = String(params.get('contest') || '').trim();
    const mode = normalizeModeToken(params.get('mode'));
    const focus = String(params.get('focus') || '').trim();
    const linesRaw = String(params.get('lines') || '').trim();
    const lines = linesRaw ? normalizeDistrictLinesYear(linesRaw) : null;
    const swingRaw = String(params.get('swing') || '').trim();
    const swing = swingRaw ? Number(swingRaw) : null;
    const sscopeRaw = String(params.get('sscope') || '').trim().toLowerCase();
    const sscope = sscopeRaw ? (sscopeRaw === 'wake' ? 'wake' : 'statewide') : null;
    const barometerRaw = String(params.get('barometer') || params.get('bar') || '').trim().toLowerCase();
    const barometerEnabled = barometerRaw
      ? ['1', 'true', 'on', 'yes'].includes(barometerRaw)
      : null;
    const demoContrastRaw = String(params.get('democontrast') || params.get('demo_contrast') || '').trim().toLowerCase();
    const demoContrastHigh = demoContrastRaw
      ? ['high', '1', 'true', 'on', 'yes'].includes(demoContrastRaw)
      : null;
    const popMetricRaw = String(params.get('popmetric') || '').trim().toLowerCase();
    const popMetric = popMetricRaw === 'abs' || popMetricRaw === 'pct' ? popMetricRaw : null;
    const mBlendRaw = String(params.get('mblend') || '').trim();
    const mTurnoutRaw = String(params.get('mturnout') || '').trim();
    const mBonusRaw = String(params.get('mbonus') || '').trim();
    const mBlend = mBlendRaw ? Number(mBlendRaw) : null;
    const mTurnout = mTurnoutRaw ? Number(mTurnoutRaw) : null;
    const mBonus = mBonusRaw ? Number(mBonusRaw) : null;
    const hasAny = !!(
      view ||
      contest ||
      mode ||
      focus ||
      lines !== null ||
      swingRaw ||
      sscopeRaw ||
      barometerEnabled !== null ||
      demoContrastHigh !== null ||
      popMetric !== null ||
      mBlendRaw ||
      mTurnoutRaw ||
      mBonusRaw
    );
    return {
      hasAny,
      view,
      contest,
      mode,
      focus,
      lines,
      swing,
      sscope,
      barometerEnabled,
      demoContrastHigh,
      popMetric,
      mBlend,
      mTurnout,
      mBonus
    };
  }

  return {
    VIEW_VALUES,
    MODE_VALUES,
    normalizeViewToken,
    normalizeModeToken,
    normalizeDistrictLinesYear,
    parse
  };
}));
