(() => {
  const deviceSelector = document.getElementById('deviceSelector');
  let currentDeviceId = (window.appConfig && window.appConfig.selectedDeviceId) || (deviceSelector ? deviceSelector.value : null);

  let powerChart;
  let energyProgressChart;
  let angleChart;
  let historyPowerChart;
  let historyAngleChart;
  let historyEnergyChart;

  const chartsPresent = {
    dashboard: !!document.getElementById('powerChart'),
    history: !!document.getElementById('historyPowerChart'),
  };

  function setDevice(deviceId) {
    currentDeviceId = deviceId;
    window.appConfig = window.appConfig || {};
    window.appConfig.selectedDeviceId = deviceId;
    const labels = ['dashboardDeviceLabel', 'historyDeviceLabel'];
    labels.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = deviceId || 'No device';
    });
  }

  async function rememberDevice(deviceId) {
    try {
      await fetch('/api/select_device', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId }),
      });
    } catch (err) {
      console.error('Failed to save device selection', err);
    }
  }

  if (deviceSelector) {
    deviceSelector.addEventListener('change', async (e) => {
      const deviceId = e.target.value;
      setDevice(deviceId);
      await rememberDevice(deviceId);
      await refreshAll();
    });
  }

  function formatNumber(val, digits = 2) {
    if (val === null || val === undefined || Number.isNaN(val)) return '--';
    return Number(val).toFixed(digits);
  }

  function updateStatusBadge(status, lastSeen) {
    const el = document.getElementById('cardStatus');
    const lastSeenEl = document.getElementById('cardLastSeen');
    if (!el) return;
    const online = status === 'online';
    el.innerHTML = `<span class="badge ${online ? 'bg-success' : 'bg-secondary'}"><i class="fa-solid fa-circle me-1"></i>${online ? 'Online' : 'Offline'}</span>`;
    if (lastSeenEl) {
      lastSeenEl.textContent = lastSeen ? `Last seen ${lastSeen}` : '--';
    }
  }

  function updateCards(latest) {
    if (!latest) return;
    const map = [
      ['cardVoltage', `${formatNumber(latest.v_panel)} V`],
      ['cardCurrent', `${formatNumber(latest.i_panel)} A`],
      ['cardPower', `${formatNumber(latest.p_w)} W`],
      ['cardEnergy', `${formatNumber(latest.e_wh_today)} Wh`],
      ['cardAngle', `${formatNumber(latest.angle_deg, 0)}°`],
      ['cardMode', latest.mode || '--'],
    ];
    map.forEach(([id, val]) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    });
    const tsEl = document.getElementById('latestTimestamp');
    if (tsEl) tsEl.textContent = latest.ts ? new Date(latest.ts).toLocaleString() : '--';
    updateStatusBadge(latest.status || 'offline', latest.last_seen ? new Date(latest.last_seen).toLocaleString() : null);
  }

  function ensureChart(chartRef, ctx, config) {
    if (!ctx) return null;
    if (chartRef) {
      chartRef.data = config.data;
      chartRef.options = config.options || {};
      chartRef.update();
      return chartRef;
    }
    return new Chart(ctx, config);
  }

  async function loadLatest() {
    if (!currentDeviceId || !chartsPresent.dashboard) return;
    try {
      const res = await fetch(`/api/latest/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      // attach status if available
      if (!data.status) {
        try {
          const statusRes = await fetch(`/api/device/status/${encodeURIComponent(currentDeviceId)}`);
          if (statusRes.ok) {
            const statusData = await statusRes.json();
            data.status = statusData.status;
            data.last_seen = statusData.last_seen;
          }
        } catch (err) {
          console.warn('status fetch failed', err);
        }
      }
      updateCards(data);
      return data;
    } catch (err) {
      console.error('Latest telemetry fetch failed', err);
    }
  }

  async function loadPowerChart() {
    if (!currentDeviceId || !chartsPresent.dashboard) return;
    const ctx = document.getElementById('powerChart');
    if (!ctx) return;
    try {
      const res = await fetch(`/api/chart/power/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const arr = await res.json();
      const labels = arr.map((p) => new Date(p.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      const dataset = arr.map((p) => p.power);
      powerChart = ensureChart(powerChart, ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Power (W)',
            data: dataset,
            borderColor: '#0d6efd',
            backgroundColor: 'rgba(13,110,253,0.1)',
            tension: 0.25,
            fill: true,
            pointRadius: 0,
          }],
        },
        options: {
          scales: { x: { title: { display: true, text: 'Time' } }, y: { beginAtZero: true } },
          plugins: { legend: { display: false } },
        },
      });
    } catch (err) {
      console.error('Power chart load failed', err);
    }
  }

  async function loadEnergyProgress(latestEnergy) {
    if (!currentDeviceId || !chartsPresent.dashboard) return;
    const ctx = document.getElementById('energyProgressChart');
    if (!ctx) return;
    try {
      const res = await fetch(`/api/chart/energy/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const arr = await res.json();
      const todayEntry = arr[arr.length - 1];
      const energyToday = latestEnergy ?? (todayEntry ? todayEntry.energy_wh : 0);
      const target = 5000; // Wh target placeholder
      const remaining = Math.max(target - energyToday, 0);
      const percent = target > 0 ? Math.min((energyToday / target) * 100, 100) : 0;
      const targetLabel = document.getElementById('energyTargetLabel');
      if (targetLabel) {
        targetLabel.textContent = `Today: ${formatNumber(energyToday)} Wh / Target ${target} Wh (${percent.toFixed(0)}%)`;
      }
      energyProgressChart = ensureChart(energyProgressChart, ctx, {
        type: 'doughnut',
        data: {
          labels: ['Produced', 'Remaining'],
          datasets: [{ data: [energyToday, remaining], backgroundColor: ['#20c997', '#e9ecef'], borderWidth: 0 }],
        },
        options: { plugins: { legend: { position: 'bottom' } }, cutout: '60%' },
      });
    } catch (err) {
      console.error('Energy chart load failed', err);
    }
  }

  async function loadAngleChart() {
    if (!currentDeviceId || !chartsPresent.dashboard) return;
    const ctx = document.getElementById('angleChart');
    if (!ctx) return;
    try {
      const res = await fetch(`/api/chart/angle/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const arr = await res.json();
      const labels = arr.map((p) => new Date(p.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      const dataset = arr.map((p) => p.angle);
      angleChart = ensureChart(angleChart, ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Angle (deg)',
            data: dataset,
            borderColor: '#6f42c1',
            backgroundColor: 'rgba(111,66,193,0.1)',
            tension: 0.2,
            fill: true,
            pointRadius: 0,
          }],
        },
        options: {
          scales: { x: { title: { display: true, text: 'Time' } } },
          plugins: { legend: { display: false } },
        },
      });
    } catch (err) {
      console.error('Angle chart load failed', err);
    }
  }

  async function refreshDashboard() {
    const latest = await loadLatest();
    await Promise.all([
      loadPowerChart(),
      loadAngleChart(),
      loadEnergyProgress(latest ? latest.e_wh_today : 0),
    ]);
  }

  async function loadHistory() {
    if (!currentDeviceId || !chartsPresent.history) return;
    const dateInput = document.getElementById('historyDate');
    if (!dateInput) return;
    const dateVal = dateInput.value;
    try {
      const res = await fetch(`/api/history/${encodeURIComponent(currentDeviceId)}?date=${encodeURIComponent(dateVal)}`);
      if (!res.ok) return;
      const payload = await res.json();
      const telemetry = payload.telemetry || [];
      const labels = telemetry.map((t) => new Date(t.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      const powerData = telemetry.map((t) => t.p_w);
      const angleData = telemetry.map((t) => t.angle_deg);
      const energyData = telemetry.map((t) => t.e_wh_today);

      historyPowerChart = ensureChart(historyPowerChart, document.getElementById('historyPowerChart'), {
        type: 'line',
        data: { labels, datasets: [{ label: 'Power (W)', data: powerData, borderColor: '#0d6efd', fill: false, tension: 0.1 }] },
        options: { scales: { y: { beginAtZero: true } } },
      });

      historyAngleChart = ensureChart(historyAngleChart, document.getElementById('historyAngleChart'), {
        type: 'line',
        data: { labels, datasets: [{ label: 'Angle (deg)', data: angleData, borderColor: '#fd7e14', fill: false, tension: 0.1 }] },
        options: {},
      });

      historyEnergyChart = ensureChart(historyEnergyChart, document.getElementById('historyEnergyChart'), {
        type: 'line',
        data: { labels, datasets: [{ label: 'Energy (Wh)', data: energyData, borderColor: '#20c997', fill: true, backgroundColor: 'rgba(32,201,151,0.15)', tension: 0.1 }] },
        options: { scales: { y: { beginAtZero: true } } },
      });
    } catch (err) {
      console.error('History load failed', err);
    }
  }

  async function refreshAll() {
    if (chartsPresent.dashboard) {
      await refreshDashboard();
    }
    if (chartsPresent.history) {
      await loadHistory();
    }
  }

  if (chartsPresent.history) {
    const dateInput = document.getElementById('historyDate');
    if (dateInput) {
      dateInput.addEventListener('change', () => loadHistory());
    }
  }

  // initial load
  if (currentDeviceId) {
    setDevice(currentDeviceId);
    refreshAll();
    if (chartsPresent.dashboard) {
      setInterval(refreshDashboard, 5000);
    }
  }
})();
