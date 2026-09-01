function favoriteDashboard() {
  return {
    loading: true,
    error: '',
    items: [],
    summary: {},
    selected: null,
    selectedSymbol: '',
    rangePeriods: ['5m', '15m', '1h', '4h', '24h', '7d'],

    async init() {
      await this.refresh();
    },

    async fetchJson(url) {
      const response = await fetch(url, {credentials: 'same-origin'});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) throw new Error(payload.error || response.statusText || 'no data');
      return payload;
    },

    async refresh() {
      this.loading = true;
      this.error = '';
      try {
        const payload = await this.fetchJson('/api/favorites');
        this.items = payload.items || [];
        this.summary = payload.summary || {};
        const next = this.items.find(item => item.symbol === this.selectedSymbol) || this.items[0];
        if (next) await this.select(next.symbol);
        else this.selected = null;
      } catch (error) {
        this.items = [];
        this.summary = {};
        this.selected = null;
        this.error = error.message || 'no data';
      } finally {
        this.loading = false;
      }
    },

    async select(symbol) {
      if (!symbol) return;
      this.selectedSymbol = symbol;
      try {
        const payload = await this.fetchJson('/api/favorites/' + encodeURIComponent(symbol));
        this.selected = payload.item || null;
      } catch (error) {
        this.selected = this.items.find(item => item.symbol === symbol) || null;
        this.error = this.selected ? '' : (error.message || 'no data');
      }
    },

    selectSummary(item) {
      if (item?.symbol) this.select(item.symbol);
    },

    formatSymbol(symbol) {
      const raw = String(symbol || '').toUpperCase();
      return raw.endsWith('USDC') ? raw.slice(0, -4) + '/USDC' : raw || '—';
    },

    price(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return '—';
      if (number >= 1000) return number.toLocaleString('en-US', {maximumFractionDigits: 2});
      if (number >= 100) return number.toFixed(2);
      if (number >= 1) return number.toFixed(4);
      return number.toPrecision(6);
    },

    pct(value) {
      const number = Number(value);
      return Number.isFinite(number) ? (number * 100).toFixed(2) + '%' : '—';
    },

    usdc(value) {
      const number = Number(value);
      return Number.isFinite(number) ? `${number >= 0 ? '+' : ''}${number.toFixed(4)} USDC` : '—';
    },

    compact(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return '—';
      if (number >= 1e9) return (number / 1e9).toFixed(2) + 'B';
      if (number >= 1e6) return (number / 1e6).toFixed(2) + 'M';
      if (number >= 1e3) return (number / 1e3).toFixed(1) + 'K';
      return number.toFixed(0);
    },

    date(value) {
      if (!value) return '—';
      const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleString('fr-FR', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'});
    },

    changeClass(value) {
      const number = Number(value);
      if (!Number.isFinite(number) || Math.abs(number) < 0.000001) return 'neutral';
      return number > 0 ? 'profit' : 'loss';
    },

    verdictClass(verdict) {
      const value = String(verdict || '').toLowerCase();
      if (value === 'intéressant') return 'is-interesting';
      if (value === 'à surveiller') return 'is-watch';
      if (value === 'calme') return 'is-calm';
      if (value.includes('haut') || value.includes('volatile') || value.includes('spread') || value.includes('faible')) return 'is-risk';
      return 'is-calm';
    },

    scalpingClass(grade) {
      return 'scalping-' + String(grade || 'unknown');
    },

    historyClass(history) {
      const value = String(history?.assessment || '');
      if (value === 'historique positif') return 'history-positive';
      if (value === 'historique négatif') return 'history-negative';
      if (value === 'échantillon limité') return 'history-limited';
      return 'history-none';
    },

    summarySymbol(item) { return item?.symbol ? this.formatSymbol(item.symbol) : '—'; },
    summaryLabel(item) { return item?.verdict || 'no data'; },
    summaryChange(item) { return item?.changes?.change_5m_pct; },
    behaviorText(item) { return item?.behavior?.summary || 'no data'; },
    scalpingText(item) { return item?.scalping_quality?.label || 'no data'; },
    riskText(item) { return item?.market?.risk_label || item?.market?.risk_level || 'no data'; },

    freshnessText() {
      if (!this.summary.latest_data_at) return 'Données radar indisponibles';
      return 'Dernier radar : ' + this.date(this.summary.latest_data_at);
    },

    freshnessState() {
      if (!this.summary.latest_data_at) return 'no data';
      return this.summary.freshness?.status === 'fresh' ? 'données fraîches' : 'données anciennes';
    },

    freshnessItemText(item) {
      if (!item?.updated_at) return 'no data';
      return item.freshness?.status === 'fresh' ? 'données fraîches' : 'données anciennes';
    },

    rowSentence(item) {
      const behavior = this.behaviorText(item);
      const quality = this.scalpingText(item);
      const risk = this.riskText(item);
      return `${behavior} · scalping ${quality} · risque ${risk}`;
    },

    rangePositionText(range) {
      if (!range?.available) return 'donnée absente';
      const source = range.source === 'kraken_24h_ticker' ? 'ticker 24 h · ' : '';
      return `${source}${range.position_in_range_pct.toFixed(0)}% dans le range · min ${this.pct(range.distance_from_min_pct)} · max ${this.pct(range.distance_to_max_pct)}`;
    },

    exitText(exits) {
      if (!Array.isArray(exits) || !exits.length) return '—';
      return exits.map(item => `${item.reason} (${item.count})`).join(', ');
    },

    missingFields(item) {
      if (!item) return ['no data'];
      const missing = [];
      if (!Number.isFinite(Number(item.current_price))) missing.push('prix actuel');
      if (!Number.isFinite(Number(item.market?.spread_pct))) missing.push('spread');
      if (!Number.isFinite(Number(item.market?.quote_volume_24h))) missing.push('volume 24 h');
      const unavailable = this.rangePeriods.filter(period => !item.ranges?.[period]?.available);
      if (unavailable.length) missing.push('ranges ' + unavailable.join(', '));
      return missing;
    },

    sparklinePoints() {
      const values = (this.selected?.series || []).map(item => Number(item.price)).filter(Number.isFinite);
      if (values.length < 2) return '';
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = max - min || 1;
      return values.map((value, index) => {
        const x = (index / (values.length - 1)) * 240;
        const y = 58 - ((value - min) / span) * 52;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      }).join(' ');
    },
  };
}
