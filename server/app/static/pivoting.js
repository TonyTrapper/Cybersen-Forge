const pivotState = {
  agents: [],
  pivots: [],
  drafts: {},
};

function connectedAgents() {
  return pivotState.agents.filter(agent => agent.status === 'online' || agent.status === 'idle');
}

function renderSummary() {
  const agents = connectedAgents();
  const available = agents.filter(agent => agent.pivot_available && agent.networks?.length).length;
  const badge = document.querySelector('#pivot-summary');
  badge.className = `status-badge ${available ? 'online' : 'unknown'}`;
  badge.textContent = `${available} disponible${available === 1 ? '' : 's'}`;
}

function renderAgents() {
  const grid = document.querySelector('#pivot-agent-grid');

  // Conserva los valores actuales antes de reconstruir las tarjetas.
  grid.querySelectorAll('.pivot-agent-card').forEach(card => {
    const form = card.querySelector('.agent-pivot-form');
    if (!form) return;

    pivotState.drafts[card.dataset.agentId] = {
      network: form.elements.network?.value || '',
      targetHost: form.elements.target_host?.value || '',
    };
  });

  const agents = connectedAgents();
  if (!agents.length) {
    grid.innerHTML = '<div class="empty-state panel">No hay agentes conectados.</div>';
    renderSummary();
    return;
  }

  grid.innerHTML = agents.map(agent => {
    const networks = agent.networks || [];
    const ready = agent.pivot_available && networks.length > 0;
    const options = networks.length
      ? networks.map(item => `<option value="${Forge.escape(item.network)}">${Forge.escape(item.network)} · ${Forge.escape(item.interface)}</option>`).join('')
      : '<option value="">Sin redes reportadas</option>';
    const networkCards = networks.length
      ? networks.map(item => `
          <article class="network-card compact-network-card">
            <strong>${Forge.escape(item.interface)}</strong>
            <code>${Forge.escape(item.address)}</code>
            <span>${Forge.escape(item.network)}</span>
          </article>`).join('')
      : '<div class="empty-state compact-empty">El agente todavía no reportó redes IPv4.</div>';
    return `
      <article class="panel pivot-agent-card" data-agent-id="${Forge.escape(agent.id)}">
        <div class="pivot-agent-header">
          <div class="host-cell">
            <span class="host-avatar">${Forge.escape(agent.os.slice(0, 1).toUpperCase())}</span>
            <span>
              <strong>${Forge.escape(agent.display_name)}</strong>
              <small>${Forge.escape(agent.username)}@${Forge.escape(agent.hostname)} · ${Forge.escape(Forge.platformLabel(agent.os, agent.arch))}</small>
            </span>
          </div>
          <span class="status-badge ${Forge.escape(agent.status)}">${Forge.escape(agent.status)}</span>
        </div>
        <div class="agent-network-list">${networkCards}</div>
        <form class="pivot-form agent-pivot-form">
          <label>
            <span>Red alcanzable</span>
            <select name="network" ${ready ? '' : 'disabled'}>${options}</select>
          </label>
          <label>
            <span>Host interno</span>
            <input name="target_host" type="text" inputmode="decimal" placeholder="10.20.30.10" required ${ready ? '' : 'disabled'}>
          </label>
          <label>
            <span>Servicio</span>
            <input value="SSH / TCP 22" disabled>
          </label>
          <button type="submit" class="primary-button" ${ready ? '' : 'disabled'}>Activar pivote</button>
          <div class="command-feedback pivot-card-feedback" aria-live="polite">${agent.pivot_available ? '' : 'Transporte de pivoting no configurado en este agente.'}</div>
        </form>
      </article>`;
  }).join('');

  grid.querySelectorAll('.pivot-agent-card').forEach(card => {
    const form = card.querySelector('.agent-pivot-form');
    if (!form) return;

    const agentId = card.dataset.agentId;
    const draft = pivotState.drafts[agentId] || {};

    if (
      draft.network &&
      Array.from(form.elements.network.options).some(
        option => option.value === draft.network
      )
    ) {
      form.elements.network.value = draft.network;
    }

    if (draft.targetHost) {
      form.elements.target_host.value = draft.targetHost;
    }

    const saveDraft = () => {
      pivotState.drafts[agentId] = {
        network: form.elements.network.value,
        targetHost: form.elements.target_host.value,
      };
    };

    form.elements.network.addEventListener('change', saveDraft);
    form.elements.target_host.addEventListener('input', saveDraft);
    form.addEventListener('submit', submitPivot);
  });

  renderSummary();
}

function renderPivots() {
  const list = document.querySelector('#pivot-list');
  if (!pivotState.pivots.length) {
    list.innerHTML = '<div class="empty-state">No hay pivotes configurados.</div>';
    return;
  }
  list.innerHTML = pivotState.pivots.map(pivot => {
    const active = pivot.desired_state === 'active';
    const action = active ? 'stop' : 'start';
    const label = active ? 'Detener' : 'Iniciar';
    return `
      <article class="pivot-card">
        <div class="pivot-target">
          <strong>${Forge.escape(pivot.target_host)}:${Forge.escape(pivot.target_port)}</strong>
          <span>${Forge.escape(pivot.network)} mediante ${Forge.escape(pivot.agent?.display_name || pivot.agent?.hostname || pivot.agent_id)}</span>
        </div>
        <div class="pivot-listener">
          <span>Listener en Forge</span>
          <code>127.0.0.1:${Forge.escape(pivot.listen_port)}</code>
        </div>
        <span class="status-badge ${Forge.escape(pivot.status)}">${Forge.escape(pivot.status)}</span>
        <div class="pivot-actions">
          <button type="button" class="secondary-button small-button copy-pivot-command" data-port="${pivot.listen_port}">Copiar acceso</button>
          <button type="button" class="secondary-button small-button pivot-action" data-pivot-id="${pivot.id}" data-action="${action}">${label}</button>
        </div>
        ${pivot.last_error ? `<pre class="pivot-error">${Forge.escape(pivot.last_error)}</pre>` : ''}
      </article>`;
  }).join('');

  list.querySelectorAll('.copy-pivot-command').forEach(button => {
    button.addEventListener('click', async () => {
      const command = `ssh -p ${button.dataset.port} root@127.0.0.1`;
      try {
        await navigator.clipboard.writeText(command);
        const original = button.textContent;
        button.textContent = 'Copiado';
        setTimeout(() => { button.textContent = original; }, 1000);
      } catch {
        button.textContent = 'Error';
      }
    });
  });

  list.querySelectorAll('.pivot-action').forEach(button => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await Forge.request(`/api/operator/pivots/${button.dataset.pivotId}/${button.dataset.action}`, {method: 'POST'});
        await refreshPivoting();
      } catch (error) {
        showFeedback(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  });
}

function showFeedback(message, error = false) {
  const feedback = document.querySelector('#pivot-page-feedback');
  feedback.className = `command-feedback ${error ? 'error' : 'success'}`;
  feedback.textContent = message;
}

async function submitPivot(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const card = form.closest('.pivot-agent-card');
  const agentId = card.dataset.agentId;
  const network = form.elements.network.value;
  const targetHost = form.elements.target_host.value.trim();
  const button = form.querySelector('button[type="submit"]');
  const feedback = form.querySelector('.pivot-card-feedback');
  if (!network || !targetHost) return;
  button.disabled = true;
  feedback.className = 'command-feedback pivot-card-feedback';
  feedback.textContent = 'Solicitando pivote…';
  try {
    const pivot = await Forge.request(`/api/operator/sessions/${encodeURIComponent(agentId)}/pivots`, {
      method: 'POST',
      body: JSON.stringify({network, target_host: targetHost}),
    });
    feedback.className = 'command-feedback pivot-card-feedback success';
    feedback.textContent = `Pivote #${pivot.id} solicitado en 127.0.0.1:${pivot.listen_port}.`;
    form.elements.target_host.value = '';
    pivotState.drafts[agentId] = {
      network,
      targetHost: '',
    };
    await refreshPivoting();
  } catch (error) {
    feedback.className = 'command-feedback pivot-card-feedback error';
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function refreshPivoting() {
  try {
    const [sessions, pivots] = await Promise.all([
      Forge.request('/api/operator/sessions'),
      Forge.request('/api/operator/pivots'),
    ]);
    pivotState.agents = sessions.items;
    pivotState.pivots = pivots.items;
    renderAgents();
    renderPivots();
  } catch (error) {
    document.querySelector('#pivot-agent-grid').innerHTML = `<div class="empty-state panel">${Forge.escape(error.message)}</div>`;
  }
}

window.addEventListener('DOMContentLoaded', () => {
  refreshPivoting();
  const events = new EventSource('/api/operator/events');
  events.addEventListener('refresh', refreshPivoting);
  setInterval(refreshPivoting, 5000);
});
