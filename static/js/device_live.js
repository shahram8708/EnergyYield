(() => {
  const script = document.currentScript;
  const deviceId = script.dataset.deviceId;
  if (!deviceId) { return; }

  const fields = [
    "v_panel","i_panel","p_w","angle_deg","mode","seq","slot","e_wh_today",
    "move_count_today","v_sys_5v","acs_offset_v","rssi","fault_flags","ts"
  ];

  async function refresh() {
    try {
      const res = await fetch(`/api/latest/${encodeURIComponent(deviceId)}`);
      if (!res.ok) {
        return;
      }
      const data = await res.json();
      Object.entries(data).forEach(([key, value]) => {
        if (!fields.includes(key)) return;
        const el = document.getElementById(key);
        if (!el) return;
        if (typeof value === 'number') {
          if (key === 'angle_deg') {
            el.textContent = `${value.toFixed(0)}°`;
          } else if (key === 'v_panel' || key === 'i_panel' || key === 'p_w' || key === 'e_wh_today' || key === 'v_sys_5v' || key === 'acs_offset_v') {
            el.textContent = value.toFixed(2) + (key === 'p_w' ? ' W' : key.includes('v') ? ' V' : key === 'e_wh_today' ? ' Wh' : '');
          } else {
            el.textContent = value;
          }
        } else {
          el.textContent = value;
        }
      });
    } catch (err) {
      console.error('Live update failed', err);
    }
  }

  refresh();
  setInterval(refresh, 5000);
})();
