(() => {
  'use strict';

  const SNAPSHOT_URL = '/api/public/snapshot';
  const MAX_ACTIVITY = 10;
  const MAX_OPERATORS = 24;
  const MAX_HOTSPOTS = 36;
  const model = { snapshot: null, activity: [], socket: null, reconnectTimer: null, retryDelay: 2000, connected: false, connectionInterrupted: false };
  const el = (id) => document.getElementById(id);

  function safeText(value, maxLength = 80) {
    if (typeof value !== 'string' && typeof value !== 'number') return '';
    return String(value).trim().slice(0, maxLength);
  }

  function isOnline(value) {
    if (value === true) return true;
    if (value === false || value == null) return false;
    return ['online', 'connected', 'operational', 'up', 'running', 'ok', 'active'].includes(String(value).toLowerCase());
  }

  function isOffline(value) {
    if (value === false) return true;
    if (value == null) return false;
    return ['offline', 'disconnected', 'down', 'unavailable', 'error', 'stopped'].includes(String(value).toLowerCase());
  }

  function setNetworkStatus(state, label, announcement = '') {
    const pill = el('networkPill');
    const pillText = el('networkPillText');
    const live = el('liveText');
    const footer = el('footerStatus');
    const stateCard = el('radioMetricCard');
    const metric = el('metricNetworkState');
    const liveLabel = document.querySelector('.live-label');

    pill.dataset.state = state;
    pillText.textContent = label;
    live.textContent = model.connected ? 'Atualização ao vivo' : state === 'error' ? 'Dados indisponíveis' : 'Reconectando';
    if (liveLabel) liveLabel.dataset.state = model.connected ? 'online' : state === 'error' ? 'error' : 'offline';
    footer.textContent = label;
    stateCard.dataset.network = state;
    metric.textContent = label === 'Online' ? 'Operacional' : label === 'Offline' ? 'Indisponível' : label === 'Verificando' ? 'Verificando' : label;
    if (announcement) el('connectionAnnouncement').textContent = announcement;
  }

  function numberText(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return Math.max(0, Math.floor(value)).toLocaleString('pt-BR');
    if (typeof value === 'string' && /^\d+$/.test(value.trim())) return Number(value).toLocaleString('pt-BR');
    return null;
  }

  function setMetric(id, value, fallback = '—') {
    el(id).textContent = numberText(value) ?? fallback;
  }

  function timestampValue(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value > 1e12 ? value : value * 1000;
    if (typeof value === 'string') {
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
    return null;
  }

  function relativeTime(value) {
    const timestamp = timestampValue(value);
    if (!timestamp) return 'agora';
    const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (seconds < 8) return 'agora';
    if (seconds < 60) return `há ${seconds} s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `há ${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `há ${hours} h`;
    return `há ${Math.floor(hours / 24)} d`;
  }

  function setEmpty(container, message, className = 'empty-state') {
    const node = document.createElement('p');
    node.className = className;
    node.textContent = message;
    container.replaceChildren(node);
  }

  function formatDuration(value) {
    const seconds = Math.max(0, Math.floor(Number(value) || 0));
    const minutes = Math.floor(seconds / 60);
    return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  }

  function speakerProfile(speaker) {
    const name = safeText(speaker && speaker.name, 80);
    const city = safeText(speaker && speaker.city, 60);
    const region = safeText(speaker && speaker.region, 40);
    const location = [city, region].filter(Boolean).join(' / ');
    return [name, location].filter(Boolean).join(' · ');
  }

  function speakerLabel(speaker) {
    const callsign = safeText(speaker && speaker.callsign, 24);
    const radioId = numberText(speaker && speaker.dmr_id);
    return callsign || (radioId ? `DMR ${radioId}` : 'Emissor não identificado');
  }

  function ingressLabel(ingress) {
    const callsign = safeText(ingress && ingress.callsign, 24);
    const repeaterId = numberText(ingress && ingress.repeater_id);
    const essid = safeText(ingress && ingress.essid, 2);
    return [callsign || 'Acesso DMR', repeaterId ? `ID ${repeaterId}` : '', essid ? `ESSID ${essid}` : '']
      .filter(Boolean).join(' · ');
  }

  function renderMetrics(snapshot) {
    const metrics = snapshot.metrics || {};
    const hotspots = Array.isArray(snapshot.hotspots) ? snapshot.hotspots : [];
    const connected = hotspots.filter((item) => item && item.connected === true);
    const connectedMetric = numberText(metrics.hotspots_connected);
    setMetric('metricHotspots', connectedMetric ?? connected.length);
    el('metricHotspotsNote').textContent = Number(connectedMetric ?? connected.length) === 1 ? 'Conectado agora' : 'Conectados agora';

    const tg100Count = numberText(metrics.tg100_active_hotspots)
      ?? numberText(metrics.tg100_active)
      ?? connected.filter((item) => item.tg100_active === true).length.toLocaleString('pt-BR');
    const tg100Metric = el('metricTg100');
    const count = Number(tg100Count.replace(/\D/g, ''));
    tg100Metric.textContent = `${tg100Count} ${count === 1 ? 'ativo' : 'ativos'}`;
    el('metricTg100').classList.remove('metric-state');
    el('metricTg100').nextElementSibling.textContent = 'Hotspots conectados à TG100';
    const transmitting = metrics.tg100_transmitting === true;
    const tg100Card = el('tg100MetricCard');
    tg100Card.dataset.transmitting = String(transmitting);
    el('tg100TransmitBadge').hidden = !transmitting;
    setMetric('metricOperators', metrics.operators_authorized);
  }

  function renderHotspots(items) {
    const container = el('hotspotList');
    const list = Array.isArray(items) ? items.filter((item) => item && typeof item === 'object').slice(0, MAX_HOTSPOTS) : [];
    container.setAttribute('aria-busy', 'false');
    el('hotspotBadge').textContent = String(list.filter((item) => item.connected === true).length);
    if (!list.length) {
      setEmpty(container, model.connected ? 'Nenhum hotspot conectado no momento.' : 'Não foi possível carregar os hotspots.', model.connected ? 'empty-state' : 'error-state');
      return;
    }
    const cards = list.map((item) => {
      const card = document.createElement('article');
      card.className = 'hotspot-card';
      const symbol = document.createElement('span');
      symbol.className = 'hotspot-symbol';
      symbol.setAttribute('aria-hidden', 'true');
      symbol.textContent = '⌁';
      const main = document.createElement('div');
      main.className = 'hotspot-main';
      const callsign = document.createElement('span');
      callsign.className = 'hotspot-callsign';
      callsign.textContent = safeText(item.callsign, 24) || 'Hotspot';
      const status = document.createElement('span');
      status.className = 'hotspot-state';
      const active = item.tg100_active === true;
      const transmitting = item.connected === true && item.transmitting === true;
      status.dataset.active = String(active || transmitting);
      status.dataset.transmitting = String(transmitting);
      status.textContent = item.connected !== true ? 'Desconectado' : transmitting ? 'Transmitindo · TG100' : active ? 'Conectado · TG100 ativo' : 'Conectado';
      main.append(callsign, status);
      card.append(symbol, main);
      return card;
    });
    container.replaceChildren(...cards);
  }

  function renderActiveTransmissions(items) {
    const container = el('activeTransmissionList');
    const list = Array.isArray(items) ? items.filter((item) => item && typeof item === 'object').slice(0, 36) : [];
    container.setAttribute('aria-busy', 'false');
    if (!list.length) {
      setEmpty(container, model.connected ? 'Nenhuma transmissão em andamento.' : 'As transmissões aparecerão quando a conexão voltar.', model.connected ? 'empty-state' : 'error-state');
      return;
    }
    const cards = list.map((item) => {
      const card = document.createElement('article');
      card.className = 'transmission-card';
      const heading = document.createElement('div');
      heading.className = 'transmission-heading';
      const speaker = document.createElement('strong');
      speaker.className = 'transmission-speaker';
      speaker.textContent = speakerLabel(item.speaker);
      const live = document.createElement('span');
      live.className = 'transmission-live';
      live.textContent = 'AO VIVO';
      heading.append(speaker, live);
      const sourceId = numberText(item.speaker && item.speaker.dmr_id);
      const speakerDetail = document.createElement('p');
      speakerDetail.className = 'transmission-detail';
      speakerDetail.textContent = sourceId ? `Emissor · DMR ID ${sourceId}` : 'Emissor';
      const profile = speakerProfile(item.speaker);
      const profileDetail = document.createElement('p');
      profileDetail.className = 'transmission-profile';
      profileDetail.textContent = profile;
      const ingress = document.createElement('p');
      ingress.className = 'transmission-ingress';
      ingress.textContent = `Entrada · ${ingressLabel(item.ingress)}`;
      const meta = document.createElement('div');
      meta.className = 'transmission-meta';
      const channel = document.createElement('span');
      channel.textContent = `TG${numberText(item.talkgroup) || '—'} · TS${numberText(item.timeslot) || '—'}`;
      const duration = document.createElement('time');
      duration.className = 'transmission-duration';
      duration.setAttribute('aria-live', 'off');
      duration.dataset.startedAt = safeText(item.started_at, 40);
      duration.textContent = formatDuration((Date.now() - (timestampValue(item.started_at) || Date.now())) / 1000);
      meta.append(channel, duration);
      card.append(heading, speakerDetail);
      if (profile) card.append(profileDetail);
      card.append(ingress, meta);
      return card;
    });
    container.replaceChildren(...cards);
  }

  function renderActivity(items) {
    const container = el('activityList');
    const list = Array.isArray(items) ? items.filter((item) => item && typeof item === 'object').slice(0, MAX_ACTIVITY) : [];
    container.setAttribute('aria-busy', 'false');
    if (!list.length) {
      setEmpty(container, model.connected ? 'Nenhuma atividade recente.' : 'A atividade aparecerá quando a conexão voltar.', model.connected ? 'empty-state' : 'error-state');
      return;
    }
    const entries = list.map((item) => {
      const row = document.createElement('li');
      row.className = 'activity-item';
      row.dataset.kind = 'voice';
      const marker = document.createElement('span');
      marker.className = 'activity-marker';
      marker.setAttribute('aria-hidden', 'true');
      marker.textContent = '◖';
      const copy = document.createElement('span');
      copy.className = 'activity-copy';
      const title = document.createElement('span');
      title.className = 'activity-title';
      title.textContent = speakerLabel(item.speaker);
      const subtitle = document.createElement('span');
      subtitle.className = 'activity-subtitle';
      subtitle.textContent = `Emissor · DMR ID ${numberText(item.speaker && item.speaker.dmr_id) || '—'}`;
      const profile = speakerProfile(item.speaker);
      const profileDetail = document.createElement('span');
      profileDetail.className = 'activity-profile';
      profileDetail.textContent = profile;
      const ingress = document.createElement('span');
      ingress.className = 'activity-ingress';
      ingress.textContent = `Entrada · ${ingressLabel(item.ingress)}`;
      const channel = document.createElement('span');
      channel.className = 'activity-channel';
      channel.textContent = `TG${numberText(item.talkgroup) || '—'} · TS${numberText(item.timeslot) || '—'} · ${formatDuration(item.duration_seconds)}`;
      copy.append(title, subtitle);
      if (profile) copy.append(profileDetail);
      copy.append(ingress, channel);
      const time = document.createElement('time');
      time.className = 'activity-time';
      time.setAttribute('aria-live', 'off');
      const timestamp = timestampValue(item.ended_at);
      if (timestamp) time.dateTime = new Date(timestamp).toISOString();
      time.textContent = relativeTime(item.ended_at);
      row.append(marker, copy, time);
      return row;
    });
    container.replaceChildren(...entries);
  }

  function renderOperators(items) {
    const container = el('operatorList');
    const list = Array.isArray(items) ? items.filter((item) => item && typeof item === 'object').slice(0, MAX_OPERATORS) : [];
    container.setAttribute('aria-busy', 'false');
    if (!list.length) {
      setEmpty(container, model.connected ? 'Nenhum operador disponível.' : 'Não foi possível carregar os operadores.', model.connected ? 'empty-state' : 'error-state');
      return;
    }
    const cards = list.map((item) => {
      const callsign = safeText(item.callsign, 24) || 'Operador';
      const card = document.createElement('article');
      card.className = 'operator-card';
      const avatar = document.createElement('span');
      avatar.className = 'operator-avatar';
      avatar.setAttribute('aria-hidden', 'true');
      avatar.textContent = callsign.slice(0, 2).toUpperCase();
      const copy = document.createElement('div');
      copy.className = 'operator-copy';
      const name = document.createElement('div');
      name.className = 'operator-name';
      name.textContent = callsign;
      const label = document.createElement('span');
      label.className = 'operator-label';
      label.textContent = 'Autorizado';
      copy.append(name, label);
      card.append(avatar, copy);
      return card;
    });
    container.replaceChildren(...cards);
  }

  function renderSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') throw new Error('Formato de dados inválido');
    model.snapshot = snapshot;
    const state = snapshot.state || {};
    const dashboardOnline = isOnline(state.dashboard);
    const radioOnline = isOnline(state.radio);
    let label = 'Verificando';
    let visualState = 'loading';
    if (isOffline(state.dashboard) || isOffline(state.radio)) {
      label = 'Offline';
      visualState = 'offline';
    } else if (dashboardOnline && radioOnline) {
      label = 'Online';
      visualState = 'online';
    }
    setNetworkStatus(visualState, label);
    renderMetrics(snapshot);
    renderHotspots(snapshot.hotspots);
    renderActiveTransmissions(snapshot.active_transmissions);
    renderActivity(snapshot.activity);
    renderOperators(snapshot.operators);
  }

  async function loadSnapshot() {
    const response = await fetch(SNAPSHOT_URL, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderSnapshot(await response.json());
  }

  function socketUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/public`;
  }

  function scheduleReconnect() {
    if (model.reconnectTimer || document.hidden) return;
    const delay = model.retryDelay;
    model.retryDelay = Math.min(30000, Math.round(model.retryDelay * 1.7));
    model.reconnectTimer = window.setTimeout(() => {
      model.reconnectTimer = null;
      connectSocket();
    }, delay);
  }

  function connectSocket() {
    if (document.hidden || model.socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(model.socket.readyState)) return;
    let socket;
    try { socket = new WebSocket(socketUrl()); }
    catch { model.connected = false; setNetworkStatus(model.snapshot ? 'offline' : 'error', model.snapshot ? 'Reconectando' : 'Indisponível'); scheduleReconnect(); return; }
    model.socket = socket;
    socket.addEventListener('open', () => {
      const announcement = model.connectionInterrupted ? 'Conexão ao vivo restaurada.' : '';
      model.connectionInterrupted = false;
      model.connected = true;
      model.retryDelay = 2000;
      setNetworkStatus(model.snapshot ? (isOnline(model.snapshot.state?.dashboard) && isOnline(model.snapshot.state?.radio) ? 'online' : 'offline') : 'loading', model.snapshot ? (isOnline(model.snapshot.state?.dashboard) && isOnline(model.snapshot.state?.radio) ? 'Online' : 'Offline') : 'Conectando', announcement);
    });
    socket.addEventListener('message', (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message && ['snapshot', 'voice_started', 'voice_ended'].includes(message.type)) {
        try { renderSnapshot(message.data); } catch { /* Ignore malformed public frames. */ }
      }
    });
    socket.addEventListener('close', () => {
      if (model.socket === socket) model.socket = null;
      model.connected = false;
      model.connectionInterrupted = true;
      setNetworkStatus(model.snapshot ? 'offline' : 'error', model.snapshot ? 'Reconectando' : 'Indisponível', 'Conexão ao vivo perdida. Tentando reconectar.');
      renderActiveTransmissions([]);
      scheduleReconnect();
    });
    socket.addEventListener('error', () => socket.close());
  }

  function refreshRelativeTimes() {
    if (!model.snapshot || document.hidden) return;
    document.querySelectorAll('#activityList time').forEach((node, index) => {
      const item = model.snapshot.activity && model.snapshot.activity[index];
      if (item) node.textContent = relativeTime(item.ended_at);
    });
    document.querySelectorAll('.transmission-duration[data-started-at]').forEach((node) => {
      const started = timestampValue(node.dataset.startedAt);
      if (started) node.textContent = formatDuration((Date.now() - started) / 1000);
    });
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (model.reconnectTimer) { clearTimeout(model.reconnectTimer); model.reconnectTimer = null; }
      if (model.socket) model.socket.close();
      return;
    }
    loadSnapshot().catch(() => {});
    connectSocket();
  });

  window.setInterval(refreshRelativeTimes, 1000);
  loadSnapshot().catch(() => {
    model.connected = false;
    setNetworkStatus('error', 'Indisponível', 'Não foi possível carregar os dados públicos.');
    renderHotspots([]);
    renderActiveTransmissions([]);
    renderActivity([]);
    renderOperators([]);
  });
  connectSocket();
})();
