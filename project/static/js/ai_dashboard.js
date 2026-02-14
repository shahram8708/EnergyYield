(() => {
  const deviceSelector = document.getElementById('deviceSelector');
  let currentDeviceId = (window.appConfig && window.appConfig.selectedDeviceId) || (deviceSelector ? deviceSelector.value : null);
  let resetChart;

  function formatNumber(val, digits = 1) {
    if (val === null || val === undefined || Number.isNaN(val)) return '--';
    return Number(val).toFixed(digits);
  }

  function setBar(barId, labelId, value, max = 1) {
    const bar = document.getElementById(barId);
    const label = document.getElementById(labelId);
    const pct = Math.min(Math.max((value / max) * 100, 0), 100);
    if (bar) bar.style.width = `${pct}%`;
    if (label) label.textContent = `${formatNumber(value * (labelId.includes('Value') ? 1 : 1), 2)}`;
  }

  function renderAlerts(alerts) {
    const panel = document.getElementById('alertsPanel');
    if (!panel) return;
    panel.innerHTML = '';
    if (!alerts || alerts.length === 0) {
      panel.innerHTML = '<div class="text-muted">No active alerts.</div>';
      return;
    }
    alerts.forEach((a) => {
      const card = document.createElement('div');
      const badgeClass = a.severity === 'critical' ? 'danger' : (a.severity === 'warn' ? 'warning' : 'info');
      card.className = `alert alert-${badgeClass} d-flex justify-content-between align-items-start`;
      card.innerHTML = `<div><div class="fw-semibold">${a.title}</div><div class="small">${a.detail || ''}</div><div class="small text-muted">${new Date(a.created_at).toLocaleString()}</div></div>`;
      const btn = document.createElement('button');
      btn.className = 'btn btn-sm btn-outline-secondary ms-2';
      btn.textContent = 'Clear';
      btn.onclick = async () => {
        await fetch(`/api/alerts/clear/${a.id}`, { method: 'POST' });
        await loadAlerts();
      };
      card.appendChild(btn);
      panel.appendChild(card);
    });
  }

  function renderRecommendations(recs) {
    const container = document.getElementById('recommendationsList');
    if (!container) return;
    container.innerHTML = '';
    if (!recs || recs.length === 0) {
      container.innerHTML = '<div class="text-muted">No recommendations yet.</div>';
      return;
    }
    recs.forEach((r) => {
      const div = document.createElement('div');
      div.className = 'recommendation-item';
      div.innerHTML = `<i class="fa-solid fa-circle-check text-success me-2"></i>${r}`;
      container.appendChild(div);
    });
  }

  function renderFaults(faults) {
    const list = document.getElementById('faultTimeline');
    if (!list) return;
    list.innerHTML = '';
    if (!faults || faults.length === 0) {
      list.innerHTML = '<li class="list-group-item text-muted">No fault events recorded.</li>';
      return;
    }
    faults.forEach((f) => {
      const li = document.createElement('li');
      li.className = 'list-group-item d-flex justify-content-between align-items-start';
      li.innerHTML = `<div><div class="fw-semibold text-${f.severity === 'critical' ? 'danger' : f.severity === 'warn' ? 'warning' : 'secondary'}">${f.fault_type}</div><div class="small">${JSON.stringify(f.details)}</div><div class="small text-muted">${new Date(f.ts).toLocaleString()}</div></div>`;
      list.appendChild(li);
    });
  }

  function renderResetChart(resets) {
    const ctx = document.getElementById('resetChart');
    if (!ctx) return;
    const data = {
      labels: ['Last 24h'],
      datasets: [{
        label: 'Resets',
        data: [resets],
        backgroundColor: '#dc3545',
        borderColor: '#dc3545',
      }],
    };
    if (resetChart) {
      resetChart.data = data;
      resetChart.update();
      return;
    }
    resetChart = new Chart(ctx, {
      type: 'bar',
      data,
      options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } },
    });
  }

  function updateProgress(barId, labelId, value) {
    const bar = document.getElementById(barId);
    const label = document.getElementById(labelId);
    const numeric = Number.isFinite(value) ? value : 0;
    const pct = Math.min(Math.max(numeric, 0), 100);
    if (bar) bar.style.width = `${pct}%`;
    if (label) label.textContent = `${pct.toFixed(0)}%`;
  }

  function updateSummary(data) {
    if (!data || !data.diagnostics) return;
    const diag = data.diagnostics;
    const dust = diag.dust || {};
    const sensor = diag.sensor_health || {};
    const forecast = data.forecast || diag.forecast || {};
    const efficiency = diag.efficiency || {};

    if (dust) {
      const dustPct = (dust.dust_probability || 0) * 100;
      const shadingPct = (dust.shading_probability || 0) * 100;
      updateProgress('dustBar', 'dustValue', dustPct);
      updateProgress('shadingBar', 'shadingValue', shadingPct);
    }

    if (sensor) {
      updateProgress('sensorHealthBar', 'sensorHealthValue', sensor.score || 0);
    }

    if (forecast) {
      document.getElementById('forecastValue').textContent = `${formatNumber(forecast.predicted_wh, 2)} Wh`;
      document.getElementById('forecastConfidence').textContent = formatNumber(forecast.confidence, 1);
    }

    if (efficiency) {
      updateProgress('efficiencyBar', 'efficiencyScore', efficiency.efficiency_score || 0);
      const bestPower = (efficiency.best_slot_power || efficiency.best_slot_power === 0) ? efficiency.best_slot_power : null;
      document.getElementById('bestSlotPower').textContent = `${formatNumber(bestPower, 2)} W`;
      document.getElementById('wastedMoves').textContent = `${formatNumber(efficiency.wasted_moves_percent, 1)} %`;
    }

    if (data.today) {
      document.getElementById('todayEnergy').textContent = `${formatNumber(data.today.energy_wh, 2)} Wh`;
      document.getElementById('todayMoves').textContent = data.today.move_count ?? '--';
    }

    if (data.generated_at) {
      document.getElementById('aiGeneratedAt').textContent = new Date(data.generated_at).toLocaleString();
    }

    const prisk = (diag.power_rail && diag.power_rail.power_rail_risk) ? diag.power_rail.power_rail_risk * 100 : (data.power_rail_risk || 0) * 100;
    updateProgress('powerRailRiskBar', 'powerRailRiskLabel', prisk);
    const rtcScore = data.rtc_reliability_score || (diag.rtc ? diag.rtc.rtc_reliability_score : 0) || 0;
    updateProgress('rtcScoreBar', 'rtcScoreLabel', rtcScore);
    renderResetChart(data.reset_frequency || (diag.power_rail ? diag.power_rail.reset_frequency : 0) || 0);

    if (data.explanation) {
      document.getElementById('explainerText').textContent = data.explanation;
    }

    renderRecommendations(data.recommendations || []);
    renderFaults(data.faults || []);
  }

  async function loadSummary() {
    if (!currentDeviceId) return;
    try {
      const res = await fetch(`/api/ai/latest/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const payload = await res.json();
      updateSummary(payload);
    } catch (err) {
      console.error('AI summary fetch failed', err);
    }
  }

  async function loadAlerts() {
    if (!currentDeviceId) return;
    try {
      const res = await fetch(`/api/alerts/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const payload = await res.json();
      renderAlerts(payload);
    } catch (err) {
      console.error('Alerts fetch failed', err);
    }
  }

  function setDevice(deviceId) {
    currentDeviceId = deviceId;
    window.appConfig = window.appConfig || {};
    window.appConfig.selectedDeviceId = deviceId;
    const label = document.getElementById('aiDeviceLabel');
    if (label) label.textContent = deviceId || 'No device';
  }

  async function refreshAi() {
    await Promise.all([loadSummary(), loadAlerts()]);
  }

  if (deviceSelector) {
    deviceSelector.addEventListener('change', async (e) => {
      setDevice(e.target.value);
      await fetch('/api/select_device', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ device_id: e.target.value }) });
      await refreshAi();
    });
  }

  if (currentDeviceId) {
    setDevice(currentDeviceId);
    refreshAi();
    setInterval(refreshAi, 60000);
  }
})();
