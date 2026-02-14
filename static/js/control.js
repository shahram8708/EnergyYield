(() => {
  const deviceId = (window.appConfig && window.appConfig.selectedDeviceId) || null;
  if (!deviceId) {
    return;
  }

  const modeSaveBtn = document.getElementById('modeSaveBtn');
  const angleSlider = document.getElementById('angleSlider');
  const angleValue = document.getElementById('angleValue');
  const sendAngleBtn = document.getElementById('sendAngleBtn');
  const saveThresholdsBtn = document.getElementById('saveThresholdsBtn');
  const minNetGainInput = document.getElementById('minNetGainInput');
  const maxMovesInput = document.getElementById('maxMovesInput');
  const commandHistoryBody = document.getElementById('commandHistoryBody');
  const deviceStatusBadge = document.getElementById('deviceStatusBadge');
  const offlineNotice = document.getElementById('offlineNotice');
  const snapshotBtn = document.getElementById('requestSnapshotBtn');
  const cleaningType = document.getElementById('cleaningType');
  const cleaningNote = document.getElementById('cleaningNote');
  const recordCleaningBtn = document.getElementById('recordCleaningBtn');

  const controls = [
    modeSaveBtn,
    angleSlider,
    sendAngleBtn,
    saveThresholdsBtn,
    minNetGainInput,
    maxMovesInput,
    snapshotBtn,
    recordCleaningBtn,
    cleaningType,
    cleaningNote,
    document.getElementById('modeAuto'),
    document.getElementById('modeManual'),
    document.getElementById('modeExplore'),
  ];

  function showAlert(message, type = 'info') {
    const wrap = document.getElementById('controlAlerts');
    if (!wrap) return;
    const div = document.createElement('div');
    div.className = `alert alert-${type} alert-dismissible fade show`;
    div.role = 'alert';
    div.textContent = message;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-close';
    btn.setAttribute('data-bs-dismiss', 'alert');
    btn.setAttribute('aria-label', 'Close');
    div.appendChild(btn);
    wrap.innerHTML = '';
    wrap.appendChild(div);
  }

  function setDisabled(disabled) {
    controls.forEach((el) => {
      if (el) el.disabled = disabled;
    });
    if (offlineNotice) {
      offlineNotice.classList.toggle('d-none', !disabled);
    }
  }

  function updateStatus(status, lastSeen) {
    if (!deviceStatusBadge) return;
    const online = status === 'online';
    const badge = `<span class="badge ${online ? 'bg-success' : 'bg-secondary'}">${online ? 'Online' : 'Offline'}</span>`;
    const extra = lastSeen ? `<div class="small text-muted">Last seen ${new Date(lastSeen).toLocaleString()}</div>` : '';
    deviceStatusBadge.innerHTML = `${badge}${extra}`;
    setDisabled(!online);
  }

  async function fetchStatus() {
    try {
      const res = await fetch(`/api/device/status/${encodeURIComponent(deviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      updateStatus(data.status, data.last_seen);
    } catch (err) {
      console.error('status check failed', err);
    }
  }

  function selectedMode() {
    const checked = document.querySelector('input[name="modeOption"]:checked');
    return checked ? checked.value : null;
  }

  async function fetchSettings() {
    try {
      const res = await fetch(`/api/settings/${encodeURIComponent(deviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const mode = data.mode || 'auto';
      const radio = document.querySelector(`input[name="modeOption"][value="${mode}"]`);
      if (radio) radio.checked = true;
      if (minNetGainInput && data.min_net_gain_wh !== null && data.min_net_gain_wh !== undefined) {
        minNetGainInput.value = data.min_net_gain_wh;
      }
      if (maxMovesInput && data.max_moves_per_hour !== null && data.max_moves_per_hour !== undefined) {
        maxMovesInput.value = data.max_moves_per_hour;
      }
    } catch (err) {
      console.error('settings fetch failed', err);
    }
  }

  async function sendCommand(cmd, args = {}) {
    try {
      const res = await fetch(`/api/send_cmd/${encodeURIComponent(deviceId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd, args }),
      });
      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || 'Command failed');
      }
      const data = await res.json();
      showAlert(`Command ${cmd} queued (id ${data.cmd_id})`, 'success');
      await loadHistory();
    } catch (err) {
      console.error('command send failed', err);
      showAlert(err.message || 'Command failed', 'danger');
    }
  }

  async function saveMode() {
    const mode = selectedMode();
    if (!mode) {
      showAlert('Select a mode first', 'warning');
      return;
    }
    try {
      await fetch(`/api/settings/${encodeURIComponent(deviceId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      await sendCommand('set_mode', { mode });
    } catch (err) {
      console.error('mode save failed', err);
      showAlert('Failed to save mode', 'danger');
    }
  }

  async function saveThresholds() {
    const minGain = parseFloat(minNetGainInput.value);
    const maxMoves = parseInt(maxMovesInput.value, 10);
    if (Number.isNaN(minGain) || Number.isNaN(maxMoves)) {
      showAlert('Enter valid thresholds', 'warning');
      return;
    }
    const payload = { min_net_gain_wh: minGain, max_moves_per_hour: maxMoves };
    try {
      await fetch(`/api/settings/${encodeURIComponent(deviceId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      await sendCommand('set_thresholds', payload);
    } catch (err) {
      console.error('threshold save failed', err);
      showAlert('Failed to save thresholds', 'danger');
    }
  }

  async function sendAngle() {
    const angle = parseFloat(angleSlider.value);
    if (Number.isNaN(angle)) {
      showAlert('Angle invalid', 'warning');
      return;
    }
    await sendCommand('set_angle', { angle_deg: angle });
  }

  async function requestSnapshot() {
    await sendCommand('request_snapshot', {});
  }

  function renderHistory(rows) {
    if (!commandHistoryBody) return;
    commandHistoryBody.innerHTML = '';
    if (!rows || !rows.length) {
      commandHistoryBody.innerHTML = '<tr><td colspan="4" class="text-muted">No commands yet.</td></tr>';
      return;
    }
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      const argsText = typeof row.args === 'object' ? JSON.stringify(row.args) : row.args;
      let statusClass = 'secondary';
      if (row.status === 'sent') statusClass = 'warning';
      if (row.status === 'executed') statusClass = 'success';
      const statusLabel = row.status ? row.status.charAt(0).toUpperCase() + row.status.slice(1) : '';
      tr.innerHTML = `
        <td>${row.created_at ? new Date(row.created_at).toLocaleString() : '--'}</td>
        <td>${row.cmd}</td>
        <td><code>${argsText || ''}</code></td>
        <td><span class="badge bg-${statusClass}">${statusLabel}</span></td>
      `;
      commandHistoryBody.appendChild(tr);
    });
  }

  async function loadHistory() {
    try {
      const res = await fetch(`/api/cmd_history/${encodeURIComponent(deviceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      renderHistory(data);
    } catch (err) {
      console.error('history fetch failed', err);
    }
  }

  async function recordCleaning() {
    const type = cleaningType.value;
    const note = cleaningNote.value || '';
    try {
      const res = await fetch(`/api/cleaning/${encodeURIComponent(deviceId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cleaning_type: type, note }),
      });
      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || 'Cleaning record failed');
      }
      const data = await res.json();
      showAlert(`Cleaning recorded. Baseline energy ${data.energy_before_wh.toFixed(2)} Wh`, 'success');
    } catch (err) {
      console.error('cleaning record failed', err);
      showAlert(err.message || 'Cleaning record failed', 'danger');
    }
  }

  function bindEvents() {
    if (modeSaveBtn) modeSaveBtn.addEventListener('click', saveMode);
    if (sendAngleBtn) sendAngleBtn.addEventListener('click', sendAngle);
    if (angleSlider && angleValue) {
      angleValue.textContent = `${angleSlider.value}\u00b0`;
      angleSlider.addEventListener('input', (e) => {
        angleValue.textContent = `${e.target.value}\u00b0`;
      });
    }
    if (saveThresholdsBtn) saveThresholdsBtn.addEventListener('click', saveThresholds);
    if (snapshotBtn) snapshotBtn.addEventListener('click', requestSnapshot);
    if (recordCleaningBtn) recordCleaningBtn.addEventListener('click', recordCleaning);
  }

  async function init() {
    bindEvents();
    await fetchSettings();
    await fetchStatus();
    await loadHistory();
    setInterval(() => {
      fetchStatus();
      loadHistory();
    }, 5000);
  }

  init();
})();
