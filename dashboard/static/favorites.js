/* Personal favorites cockpit. This enhances the server-rendered view only. */
(function () {
  'use strict';

  const periods = ['5m', '15m', '1h', '4h', '24h', '7d'];

  function number(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function price(value) {
    const parsed = number(value);
    if (parsed === null) return '—';
    if (parsed >= 1000) return parsed.toLocaleString('fr-FR', { maximumFractionDigits: 2 });
    if (parsed >= 1) return parsed.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
    return parsed.toLocaleString('fr-FR', { maximumFractionDigits: 6 });
  }

  function pct(value) {
    const parsed = number(value);
    return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}${(parsed * 100).toFixed(2)} %`;
  }

  function compact(value) {
    const parsed = number(value);
    if (parsed === null) return '—';
    if (parsed >= 1000000) return `${(parsed / 1000000).toFixed(2)} M`;
    if (parsed >= 1000) return `${(parsed / 1000).toFixed(1)} K`;
    return parsed.toFixed(0);
  }

  function rangeReading(range) {
    if (!range || !range.available) return 'historique en cours de collecte';
    const position = number(range.position_in_range_pct);
    if (position !== null && position >= 80) return 'proche du haut récent';
    if (position !== null && position <= 20) return 'proche du bas récent';
    return 'au milieu du range';
  }

  function tradeUrl(symbol) {
    const compactSymbol = String(symbol || '').replace(/[^A-Za-z0-9]/g, '').toLowerCase();
    const quote = compactSymbol.endsWith('usdc') ? 'usdc' : compactSymbol.endsWith('usd') ? 'usd' : '';
    const base = quote ? compactSymbol.slice(0, -quote.length) : compactSymbol;
    return `https://pro.kraken.com/app/trade/${base}-${quote || 'usd'}`;
  }

  function setField(root, name, value) {
    const element = root.querySelector(`[data-field="${name}"]`);
    if (element) element.textContent = value == null || value === '' ? '—' : String(value);
  }

  function updateSelectedRows(symbol) {
    document.querySelectorAll('#favorite-list [data-symbol]').forEach((row) => {
      const selected = row.dataset.symbol === symbol;
      row.classList.toggle('is-selected', selected);
      row.setAttribute('aria-current', selected ? 'true' : 'false');
    });
  }

  function initialSeries() {
    const node = document.getElementById('favorite-initial-series');
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch { return null; }
  }

  window.startFavoritesCockpit = function startFavoritesCockpit(initialSymbol) {
    const root = document.getElementById('favorites-cockpit');
    const detail = document.getElementById('favorite-detail');
    const chartElement = document.getElementById('favorite-chart');
    const empty = document.getElementById('chart-empty');
    if (!root || !detail || !chartElement) return;

    let symbol = initialSymbol;
    let chart = null;
    let area = null;
    let currentPeriod = '24h';
    const quoteAsset = () => (detail.querySelector('[data-field="price"]')?.textContent || '').trim().split(/\s+/).pop() || 'USD';

    function chartInstance() {
      if (chart || !window.LightweightCharts) return chart;
      chart = window.LightweightCharts.createChart(chartElement, {
        autoSize: true,
        height: 330,
        layout: { background: { color: 'transparent' }, textColor: '#a7b1bf', fontFamily: 'Inter, system-ui, sans-serif' },
        grid: { vertLines: { color: '#263241' }, horzLines: { color: '#263241' } },
        rightPriceScale: { borderColor: '#344254' },
        timeScale: { borderColor: '#344254', timeVisible: true, secondsVisible: false },
        handleScroll: { vertTouchDrag: false },
      });
      area = chart.addAreaSeries({
        lineColor: '#F0B90B',
        topColor: 'rgba(54, 201, 145, 0.35)',
        bottomColor: 'rgba(54, 201, 145, 0.02)',
        lineWidth: 2,
        crosshairMarkerBackgroundColor: '#F0B90B',
      });
      return chart;
    }

    function renderSeries(series) {
      const bars = Array.isArray(series?.bars) ? series.bars : [];
      const instance = chartInstance();
      if (!instance || !area) return;
      const points = bars
        .map((bar) => ({ time: Number(bar.time), value: number(bar.close) }))
        .filter((point) => Number.isFinite(point.time) && point.value !== null);
      area.setData(points);
      if (points.length) instance.timeScale().fitContent();
      empty.hidden = points.length > 0;
      setField(detail, 'chart-caption', series?.source === 'kraken_ohlc' ? 'Kraken public OHLC' : series?.source === 'local_snapshots' ? 'Snapshots locaux' : 'historique indisponible');
      const period = series?.period || currentPeriod;
      const minimum = number(series?.min);
      const maximum = number(series?.max);
      const current = number(series?.current);
      const position = minimum !== null && maximum !== null && current !== null && maximum !== minimum
        ? Math.max(0, Math.min(100, ((current - minimum) / (maximum - minimum)) * 100))
        : 50;
      setField(detail, 'range-title', `Range ${period}`);
      setField(detail, 'range-min', price(minimum));
      setField(detail, 'range-current', price(current));
      setField(detail, 'range-max', price(maximum));
      setField(detail, 'range-position', points.length ? `${Math.round(position)} % du range` : '—');
      setField(detail, 'range-reading', points.length ? (position >= 80 ? 'proche du haut récent' : position <= 20 ? 'proche du bas récent' : 'au milieu du range') : 'historique en cours de collecte');
      const marker = detail.querySelector('[data-field="range-marker"]');
      if (marker) marker.style.left = `${position}%`;
      setField(detail, 'range-period', `Période active : ${period}`);
      document.querySelectorAll('.period-tab').forEach((button) => button.classList.toggle('is-active', button.dataset.period === (series?.period || currentPeriod)));
    }

    function updateDetail(item) {
      const day = item.ranges?.['24h'];
      const market = item.market || {};
      const observation = item.observation || {};
      const bot = item.bot_history || {};
      detail.dataset.symbol = item.symbol || '';
      setField(detail, 'symbol', item.display_symbol);
      setField(detail, 'reading', item.watchlist?.summary);
      setField(detail, 'price', `${price(item.current_price)} ${item.quote_asset || 'USD'}`);
      setField(detail, 'change-24h', pct(item.changes?.change_24h_pct));
      setField(detail, 'volume', `${compact(market.quote_volume_24h)} ${item.quote_asset || 'USD'}`);
      setField(detail, 'behavior', item.watchlist?.behavior);
      setField(detail, 'range-min', day?.available ? price(day.min) : '—');
      setField(detail, 'range-current', price(item.current_price));
      setField(detail, 'range-max', day?.available ? price(day.max) : '—');
      setField(detail, 'range-reading', item.watchlist?.range_reading || rangeReading(day));
      setField(detail, 'range-position', day?.available ? `${Math.round(number(day.position_in_range_pct) || 0)} % du range` : '—');
      const marker = detail.querySelector('[data-field="range-marker"]');
      if (marker) marker.style.left = `${day?.available ? Math.max(0, Math.min(100, number(day.position_in_range_pct) || 50)) : 50}%`;
      setField(detail, 'observation-verdict', observation.verdict);
      setField(detail, 'observed-days', observation.days_observed);
      setField(detail, 'samples', observation.samples);
      setField(detail, 'spread-stability', observation.spread_stability);
      setField(detail, 'usual-volume', `${compact(observation.usual_volume_24h)} ${item.quote_asset || 'USD'}`);
      setField(detail, 'bot-assessment', bot.available ? bot.assessment : 'aucun historique bot');
      setField(detail, 'bot-trades', bot.available ? bot.complete_trades : '—');
      setField(detail, 'bot-pnl', bot.available ? `${number(bot.net_pnl_usdc)?.toFixed(4) || '—'} USDC` : '—');
      setField(detail, 'bot-best', bot.available ? `${number(bot.best_trade_usdc)?.toFixed(4) || '—'} USDC` : '—');
      setField(detail, 'bot-worst', bot.available ? `${number(bot.worst_trade_usdc)?.toFixed(4) || '—'} USDC` : '—');
      const krakenLink = detail.querySelector('[data-field="kraken-link"]');
      if (krakenLink) krakenLink.href = tradeUrl(item.symbol);
    }

    async function loadSeries(period) {
      currentPeriod = periods.includes(period) ? period : '24h';
      try {
        const response = await fetch(`/api/favorites/${encodeURIComponent(symbol)}/series?period=${encodeURIComponent(currentPeriod)}`, { credentials: 'same-origin' });
        const payload = await response.json();
        renderSeries(payload.ok ? payload.series : null);
      } catch {
        renderSeries(null);
      }
    }

    async function select(symbolToSelect, updateUrl) {
      if (!symbolToSelect || symbolToSelect === symbol) return;
      try {
        const response = await fetch(`/api/favorites/${encodeURIComponent(symbolToSelect)}`, { credentials: 'same-origin' });
        const payload = await response.json();
        if (!payload.ok || !payload.item) return;
        symbol = payload.item.symbol;
        updateDetail(payload.item);
        updateSelectedRows(symbol);
        try { window.localStorage.setItem('favorites:last-symbol', symbol); } catch {}
        if (updateUrl) window.history.replaceState({}, '', `/favorites?symbol=${encodeURIComponent(symbol)}`);
        await loadSeries('24h');
      } catch {
        window.location.assign(`/favorites?symbol=${encodeURIComponent(symbolToSelect)}`);
      }
    }

    document.getElementById('favorite-list')?.addEventListener('click', (event) => {
      const row = event.target.closest('[data-symbol]');
      if (!row || row.classList.contains('is-unavailable')) return;
      event.preventDefault();
      select(row.dataset.symbol, true);
    });
    document.querySelector('.period-tabs')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-period]');
      if (button) loadSeries(button.dataset.period);
    });

    const series = initialSeries();
    renderSeries(series);
    try {
      const remembered = window.localStorage.getItem('favorites:last-symbol');
      if (!new URLSearchParams(window.location.search).get('symbol') && remembered && remembered !== symbol) select(remembered, true);
    } catch {}
  };
})();
