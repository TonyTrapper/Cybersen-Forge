const auditState = {
  agents: [],
  tasks: [],
  openTasks: new Set(),
};

function formatTimestamp(value) {
  if (!value) return '—';
  try { return new Date(value).toLocaleString(); } catch { return value; }
}

function renderAgentFilter() {
  const select = document.querySelector('#audit-agent');
  const previous = select.value;
  select.innerHTML = '<option value="">Todos los agentes</option>' + auditState.agents.map(agent =>
    `<option value="${Forge.escape(agent.id)}">${Forge.escape(agent.display_name)} · ${Forge.escape(agent.hostname)}</option>`
  ).join('');
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function renderTasks() {
  const container = document.querySelector('#audit-task-list');
  if (!auditState.tasks.length) {
    container.innerHTML = '<div class="empty-state">No existen tareas que coincidan con los filtros.</div>';
    return;
  }
  container.innerHTML = auditState.tasks.map(task => {
    const open = auditState.openTasks.has(String(task.id)) ? ' open' : '';
    const exit = task.exit_code === null ? '—' : task.exit_code;
    const duration = task.duration_ms === null ? '—' : `${task.duration_ms} ms`;
    const stdout = task.stdout || '(sin salida estándar)';
    const stderr = task.stderr || '';
    const agent = task.agent || {};
    return `
      <article class="task-card${open}" data-task-id="${task.id}">
        <button class="task-summary audit-task-summary" type="button" aria-expanded="${open ? 'true' : 'false'}">
          <span class="task-command">
            <code>${Forge.escape(task.command)}</code>
            <small>${Forge.escape(agent.username || 'unknown')}@${Forge.escape(agent.hostname || 'unknown')} · Task #${task.id} · ${Forge.escape(formatTimestamp(task.created_at))}</small>
          </span>
          <span class="status-badge ${Forge.escape(task.status)}">${Forge.escape(task.status)}</span>
          <span class="task-meta">exit ${Forge.escape(exit)} · ${Forge.escape(duration)}</span>
        </button>
        <div class="task-output">
          <div class="audit-metadata-grid">
            <span><strong>Agente</strong>${Forge.escape(agent.display_name || agent.hostname || '—')}</span>
            <span><strong>Modo registrado</strong>${Forge.escape(task.mode || 'host')}</span>
            <span><strong>Creada</strong>${Forge.escape(formatTimestamp(task.created_at))}</span>
            <span><strong>Completada</strong>${Forge.escape(formatTimestamp(task.completed_at))}</span>
          </div>
          <div class="output-toolbar">
            <span class="output-label">stdout</span>
            <button type="button" class="copy-output" data-copy="stdout">Copiar</button>
          </div>
          <pre class="output-block" data-output="stdout">${Forge.escape(stdout)}</pre>
          ${stderr ? `
            <div class="output-toolbar stderr-toolbar">
              <span class="output-label">stderr</span>
              <button type="button" class="copy-output" data-copy="stderr">Copiar</button>
            </div>
            <pre class="output-block stderr" data-output="stderr">${Forge.escape(stderr)}</pre>` : ''}
        </div>
      </article>`;
  }).join('');

  container.querySelectorAll('.task-summary').forEach(button => {
    button.addEventListener('click', () => {
      const card = button.closest('.task-card');
      const id = card.dataset.taskId;
      card.classList.toggle('open');
      button.setAttribute('aria-expanded', card.classList.contains('open') ? 'true' : 'false');
      if (card.classList.contains('open')) auditState.openTasks.add(id);
      else auditState.openTasks.delete(id);
    });
  });

  container.querySelectorAll('.copy-output').forEach(button => {
    button.addEventListener('click', async event => {
      event.stopPropagation();
      const card = button.closest('.task-card');
      const target = card.querySelector(`[data-output="${button.dataset.copy}"]`);
      try {
        await navigator.clipboard.writeText(target.textContent);
        const original = button.textContent;
        button.textContent = 'Copiado';
        setTimeout(() => { button.textContent = original; }, 1000);
      } catch {
        button.textContent = 'Error';
      }
    });
  });
}

async function refreshAudit() {
  const agentId = document.querySelector('#audit-agent').value;
  const taskStatus = document.querySelector('#audit-status').value;
  const params = new URLSearchParams({limit: '300'});
  if (agentId) params.set('agent_id', agentId);
  if (taskStatus) params.set('task_status', taskStatus);
  try {
    const [sessions, tasks] = await Promise.all([
      Forge.request('/api/operator/sessions'),
      Forge.request(`/api/operator/audit/tasks?${params}`),
    ]);
    auditState.agents = sessions.items;
    auditState.tasks = tasks.items;
    renderAgentFilter();
    renderTasks();
  } catch (error) {
    document.querySelector('#audit-task-list').innerHTML = `<div class="empty-state">${Forge.escape(error.message)}</div>`;
  }
}

window.addEventListener('DOMContentLoaded', () => {
  document.querySelector('#audit-agent').addEventListener('change', refreshAudit);
  document.querySelector('#audit-status').addEventListener('change', refreshAudit);
  document.querySelector('#audit-refresh').addEventListener('click', refreshAudit);
  refreshAudit();
  const events = new EventSource('/api/operator/events');
  events.addEventListener('refresh', refreshAudit);
  setInterval(refreshAudit, 7000);
});
