const state = {sessions: [], search: '', filter: 'all'};

function renderMetrics(summary) {
  document.querySelector('#metric-total').textContent = summary.sessions.total;
  document.querySelector('#metric-online').textContent = summary.sessions.online;
  document.querySelector('#metric-idle').textContent = summary.sessions.idle;
  document.querySelector('#metric-tasks').textContent = summary.tasks.last_hour;
}

function renderSessions() {
  const body = document.querySelector('#sessions-body');
  const query = state.search.toLowerCase();
  const items = state.sessions.filter(item => {
    const matchesStatus = state.filter === 'all' || item.status === state.filter;
    const haystack = [item.display_name, item.hostname, item.username, item.remote_ip, item.os, item.arch, item.id].join(' ').toLowerCase();
    return matchesStatus && haystack.includes(query);
  });
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="7"><div class="empty-state">No hay sesiones que coincidan con el filtro.</div></td></tr>';
    return;
  }
  body.innerHTML = items.map(item => `
    <tr>
      <td><span class="status-badge ${Forge.escape(item.status)}">${Forge.escape(item.status)}</span></td>
      <td>
        <div class="host-cell">
          <span class="host-avatar">${Forge.escape(item.os.slice(0, 1).toUpperCase())}</span>
          <span><strong>${Forge.escape(item.display_name)}</strong><small>${Forge.escape(item.id)}</small></span>
        </div>
      </td>
      <td>${Forge.escape(item.username)}</td>
      <td>
        <span class="platform-tag">${Forge.escape(Forge.platformLabel(item.os, item.arch))}</span>
        ${item.sandbox_available ? `<span class="shell-mini">shell</span>` : ''}
      </td>
      <td><span class="route-tag">${Forge.escape(item.connection_type === 'direct' ? item.remote_ip : `vía ${item.parent_agent_id}`)}</span></td>
      <td>${Forge.escape(Forge.relativeTime(item.age_seconds))}</td>
      <td><a class="open-button" href="/sessions/${encodeURIComponent(item.id)}">Abrir</a></td>
    </tr>
  `).join('');
}

async function refresh() {
  try {
    const [summary, sessions] = await Promise.all([
      Forge.request('/api/operator/summary'),
      Forge.request('/api/operator/sessions'),
    ]);
    state.sessions = sessions.items;
    renderMetrics(summary);
    renderSessions();
  } catch (error) {
    console.error(error);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const search = document.querySelector('#session-search');
  const filter = document.querySelector('#status-filter');
  search.addEventListener('input', () => { state.search = search.value.trim(); renderSessions(); });
  filter.addEventListener('change', () => { state.filter = filter.value; renderSessions(); });
  refresh();
  const events = new EventSource('/api/operator/events');
  events.addEventListener('refresh', refresh);
  events.onerror = () => setTimeout(refresh, 1500);
  setInterval(refresh, 5000);
});
