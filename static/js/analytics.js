(() => {
  const deviceSelector = document.getElementById('deviceSelector');
  let currentDeviceId = (window.appConfig && window.appConfig.selectedDeviceId) || (deviceSelector ? deviceSelector.value : null);

  let heatmapChart;
  let dailyChart;

  const labelIds = ['analyticsDeviceLabel'];

  function ensureMatrixController() {
    const already = Chart.registry?.getController?.('matrix');
    if (already) return true;
    const matrixPlugin = window['chartjs-chart-matrix'];
    if (matrixPlugin?.MatrixController && matrixPlugin?.MatrixElement) {
      Chart.register(matrixPlugin.MatrixController, matrixPlugin.MatrixElement);
      return true;
    }
    console.error('Chart.js matrix controller not available');
    return false;
  }

  function setDevice(deviceId) {
    currentDeviceId = deviceId;
    window.appConfig = window.appConfig || {};
    window.appConfig.selectedDeviceId = deviceId;
    labelIds.forEach((id) => {
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
      console.warn('Failed to save device selection', err);
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

  function colorFromValue(value, maxValue) {
    const ratio = maxValue > 0 ? value / maxValue : 0;
    const hue = Math.max(0, 120 - ratio * 120); // green to red
    return `hsl(${hue}, 70%, ${40 + ratio * 20}%)`;
  }

  function format(val, digits = 2) {
    if (val === null || val === undefined || Number.isNaN(val)) return '--';
    return Number(val).toFixed(digits);
  }

  async function loadHeatmap() {
    const canvas = document.getElementById('heatmapChart');
    if (!canvas || !currentDeviceId) return;
    if (!ensureMatrixController()) return;
    try {
      const res = await fetch(`/api/analytics/heatmap/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.length) {
        if (heatmapChart) {
            heatmapChart.destroy();
            heatmapChart = null;
        }
        return;
      }
      const slots = [...new Set(data.map((d) => d.slot))].sort((a, b) => a - b);
      const angles = [...new Set(data.map((d) => d.angle))].sort((a, b) => a - b);
      const maxPower = Math.max(0, ...data.map((d) => d.avg_power || 0));
      const dataset = data.map((d) => ({ x: d.slot, y: d.angle, v: d.avg_power }));

      if (heatmapChart) {
        heatmapChart.destroy();
        heatmapChart = null;
      }

      heatmapChart = new Chart(canvas, {
        type: 'matrix',
        data: {
          datasets: [{
            label: 'Power Heatmap',
            data: dataset,
            width: ({ chart }) => (chart.chartArea || {}).width / Math.max(slots.length, 1) - 2,
            height: ({ chart }) => (chart.chartArea || {}).height / Math.max(angles.length, 1) - 2,
            backgroundColor: (ctx) => {
              const value = ctx.raw.v || 0;
              return colorFromValue(value, maxPower || 1);
            },
            borderWidth: 1,
            borderColor: '#fff',
          }],
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => `Slot ${ctx.raw.x} @ ${ctx.raw.y}°: ${format(ctx.raw.v)} W` } } },
          scales: {
            x: {
              type: 'category',
              labels: slots,
              title: { display: true, text: 'Slot' },
            },
            y: {
              type: 'category',
              labels: angles,
              title: { display: true, text: 'Angle (deg)' },
              reverse: true,
            },
          },
        },
      });
    } catch (err) {
      console.error('Heatmap load failed', err);
    }
  }

  async function loadBestAngles() {
    const table = document.querySelector('#bestAnglesTable tbody');
    if (!table || !currentDeviceId) return;
    table.innerHTML = '<tr><td colspan="4" class="text-muted text-center">Loading...</td></tr>';
    try {
      const res = await fetch(`/api/analytics/best_angles/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const rows = await res.json();
      if (!rows.length) {
        table.innerHTML = '<tr><td colspan="4" class="text-muted text-center">No data</td></tr>';
        return;
      }
      table.innerHTML = rows
        .map((r) => `<tr><td>${r.slot}</td><td>${format(r.best_angle, 1)}°</td><td>${format(r.confidence * 100, 1)}%</td><td>${format(r.avg_power, 2)} W</td></tr>`)
        .join('');
    } catch (err) {
      console.error('Best angles load failed', err);
    }
  }

  async function loadEfficiency() {
    try {
      const res = await fetch(`/api/analytics/efficiency/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const map = [
        ['cardEnergyPerMove', `${format(data.energy_gain_per_move, 3)} Wh`],
        ['cardMovesPerHour', `${format(data.moves_per_hour, 2)}`],
        ['cardWastedMoves', `${format(data.wasted_moves_percent, 1)} %`],
      ];
      map.forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
      });
    } catch (err) {
      console.error('Efficiency load failed', err);
    }
  }

  async function loadDaily() {
    const canvas = document.getElementById('dailyChart');
    if (!canvas || !currentDeviceId) return;
    try {
      const res = await fetch(`/api/analytics/daily/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const labels = data.map((d) => d.date.slice(5));
      const energy = data.map((d) => d.energy_wh);
      if (dailyChart) {
        dailyChart.destroy();
        dailyChart = null;
      }
      dailyChart = new Chart(canvas, {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            label: 'Energy (Wh)',
            data: energy,
            backgroundColor: '#0d6efd',
          }],
        },
        options: {
          scales: { y: { beginAtZero: true } },
          plugins: { legend: { display: false } },
        },
      });
    } catch (err) {
      console.error('Daily summary load failed', err);
    }
  }

  async function loadDecision() {
    const tbody = document.querySelector('#decisionTable tbody');
    if (!tbody || !currentDeviceId) return;
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">Loading...</td></tr>';
    try {
      const res = await fetch(`/api/analytics/net_gain/${encodeURIComponent(currentDeviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (!data || Object.keys(data).length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No data yet</td></tr>';
        return;
      }
      const negative = Number(data.net_gain_wh) < 0;
      const row = `
        <tr class="${negative ? 'table-danger' : ''}">
          <td>${data.slot}</td>
          <td>${format(data.current_angle, 1)}°</td>
          <td>${format(data.recommended_angle, 1)}°</td>
          <td>${format(data.expected_gain_wh, 3)}</td>
          <td>${format(data.motor_cost_wh, 3)}</td>
          <td>${format(data.net_gain_wh, 3)}</td>
          <td><span class="badge ${data.decision === 'MOVE' ? 'bg-success' : 'bg-secondary'}">${data.decision}</span></td>
        </tr>`;
      tbody.innerHTML = row;
    } catch (err) {
      console.error('Decision load failed', err);
    }
  }

  async function refreshAll() {
    if (!currentDeviceId) return;
    await Promise.all([
      loadHeatmap(),
      loadBestAngles(),
      loadEfficiency(),
      loadDaily(),
      loadDecision(),
    ]);
  }

  if (currentDeviceId) {
    setDevice(currentDeviceId);
    refreshAll();
    setInterval(refreshAll, 60000);
  }
})();
