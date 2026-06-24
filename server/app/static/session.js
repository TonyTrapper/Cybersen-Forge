const root = document.querySelector('#session-root');
const agentId = root.dataset.agentId;

const state = {
  agent: null,
  tasks: [],
  filter: 'all',
  mode: 'host',
  openTasks: new Set(),
};

const QUICK_COMMANDS = {
  linux: [
    'hostname', 'whoami', 'id -un', 'uname -a', 'uptime',
    'ip addr', 'ip route', 'ss -lntup', 'ps aux', 'df -h',
    'free -h', 'cat /etc/os-release', 'ls -la /tmp',
  ],
  windows: [
    'hostname', 'whoami', 'whoami /all', 'systeminfo',
    'ipconfig /all', 'tasklist /v', 'netstat -ano', 'route print',
  ],
  sandbox: [
    'pwd', 'ls -la', 'echo "Cybersen / TonyTrapper"',
    'mkdir -p demo && printf "Cybersen Forge\\n" > demo/proof.txt && cat demo/proof.txt',
    'find . -maxdepth 2 -type f -print',
    'printf "alpha\\nbeta\\ngamma\\n" | grep beta',
  ],
};

function formatTimestamp(value) {
  if (!value) return '—';
  try { return new Date(value).toLocaleString(); } catch { return value; }
}

function statusText(task) {
  if (task.status === 'pending') return 'en cola';
  if (task.status === 'dispatched') return 'ejecutando';
  return task.status;
}

function taskOutput(task) {
  if (task.status === 'pending' || task.status === 'dispatched') return '';
  const chunks = [];
  if (task.stdout) chunks.push(task.stdout);
  if (task.stderr) chunks.push(task.stderr);
  if (!chunks.length) chunks.push('(sin salida)');
  return chunks.join(task.stdout && task.stderr ? '\n' : '');
}

function renderAgent() {
  const a = state.agent;
  if (!a) return;
  document.querySelector('#session-name').textContent = a.display_name;
  document.querySelector('#page-session-name').textContent = a.display_name;
  document.querySelector('#session-status').className = `status-badge ${a.status}`;
  document.querySelector('#session-status').textContent = a.status;
  document.querySelector('#session-subtitle').textContent = `${a.username} · ${Forge.platformLabel(a.os, a.arch)} · Agent ${a.agent_version}`;
  document.querySelector('#os-avatar').textContent = a.os.slice(0, 1).toUpperCase();
  document.querySelector('#detail-ip').textContent = a.remote_ip;
  document.querySelector('#detail-pid').textContent = a.process_id ?? '—';
  document.querySelector('#detail-route').textContent = a.connection_type === 'direct' ? 'Directa' : `Vía ${a.parent_agent_id}`;
  document.querySelector('#detail-seen').textContent = Forge.relativeTime(a.age_seconds);

  const sandboxButton = document.querySelector('#sandbox-mode-button');
  const sandboxLabel = document.querySelector('#capability-sandbox');
  if (a.sandbox_available) {
    sandboxButton.disabled = false;
    sandboxButton.title = '';
    sandboxLabel.textContent = `Sandbox: ${a.sandbox_runtime || 'available'}`;
    sandboxLabel.closest('.capability-item').classList.add('available');
  } else {
    sandboxButton.disabled = true;
    sandboxButton.title = 'El agente no se enroló con FORGE_ENABLE_SANDBOX=true';
    sandboxLabel.textContent = 'Sandbox: no disponible';
    sandboxLabel.closest('.capability-item').classList.remove('available');
    if (state.mode === 'sandbox') setMode('host');
  }
}

function renderQuickCommands() {
  const container = document.querySelector('#quick-commands');
  const commands = state.mode === 'sandbox'
    ? QUICK_COMMANDS.sandbox
    : (state.agent?.os === 'windows' ? QUICK_COMMANDS.windows : QUICK_COMMANDS.linux);
  container.innerHTML = commands.map(command =>
    `<button type="button" data-command="${Forge.escape(command)}">${Forge.escape(command)}</button>`
  ).join('');
  container.querySelectorAll('button').forEach(button => {
    button.addEventListener('click', () => {
      const input = document.querySelector('#command-input');
      input.value = button.dataset.command;
      input.focus();
    });
  });
}

function setMode(mode) {
  if (mode === 'sandbox' && !state.agent?.sandbox_available) return;
  state.mode = mode;
  document.querySelectorAll('.mode-button').forEach(button => {
    button.classList.toggle('active', button.dataset.mode === mode);
  });
  const input = document.querySelector('#command-input');
  const prompt = document.querySelector('#terminal-prompt');
  const description = document.querySelector('#mode-description');
  if (mode === 'sandbox') {
    prompt.textContent = 'sandbox$';
    input.placeholder = 'echo "Cybersen" && ls -la';
    description.textContent = 'Comandos libres dentro de un contenedor efímero aislado, sin red y con recursos limitados.';
  } else {
    prompt.textContent = 'host$';
    input.placeholder = state.agent?.os === 'windows' ? 'systeminfo' : 'hostname';
    description.textContent = `Diagnóstico ${state.agent?.os === 'windows' ? 'Windows' : 'Linux'} mediante módulos auditables y salida estructurada.`;
  }
  renderQuickCommands();
  input.focus();
}

function renderTerminal() {
  const terminal = document.querySelector('#terminal-transcript');
  const wasNearBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 80;
  const items = [...state.tasks].reverse().slice(-40);
  if (!items.length) {
    terminal.innerHTML = '<div class="terminal-welcome"><strong>Cybersen Forge session console</strong><span>Selecciona un modo y envía una tarea.</span></div>';
    return;
  }
  terminal.innerHTML = items.map(task => {
    const output = taskOutput(task);
    const waiting = task.status === 'pending' || task.status === 'dispatched';
    const metadata = task.duration_ms === null
      ? `task #${task.id} · ${statusText(task)}`
      : `task #${task.id} · exit ${task.exit_code} · ${task.duration_ms} ms`;
    return `
      <div class="terminal-entry ${Forge.escape(task.status)}">
        <div class="terminal-entry-command">
          <span class="terminal-entry-prompt">${task.mode === 'sandbox' ? 'sandbox$' : 'host$'}</span>
          <code>${Forge.escape(task.command)}</code>
          <span class="terminal-mode-badge">${Forge.escape(task.mode)}</span>
        </div>
        ${waiting
          ? `<div class="terminal-wait"><span class="spinner"></span>${Forge.escape(statusText(task))}</div>`
          : `<pre>${Forge.escape(output)}</pre>`}
        <div class="terminal-entry-meta">${Forge.escape(metadata)} · ${Forge.escape(formatTimestamp(task.created_at))}</div>
      </div>`;
  }).join('');
  if (wasNearBottom) terminal.scrollTop = terminal.scrollHeight;
}

function renderTasks() {
  const container = document.querySelector('#task-history');
  const tasks = state.tasks.filter(task => state.filter === 'all' || task.status === state.filter);
  if (!tasks.length) {
    container.innerHTML = '<div class="empty-state">No existen tareas que coincidan con el filtro.</div>';
    return;
  }
  container.innerHTML = tasks.map(task => {
    const exit = task.exit_code === null ? '—' : task.exit_code;
    const duration = task.duration_ms === null ? '—' : `${task.duration_ms} ms`;
    const open = state.openTasks.has(String(task.id)) ? ' open' : '';
    const stdout = task.stdout || '(sin salida estándar)';
    const stderr = task.stderr || '';
    return `
      <article class="task-card${open}" data-task-id="${task.id}">
        <button class="task-summary" type="button" aria-expanded="${open ? 'true' : 'false'}">
          <span class="task-command">
            <code>${task.mode === 'sandbox' ? 'sandbox$' : 'host$'} ${Forge.escape(task.command)}</code>
            <small>Task #${task.id} · ${Forge.escape(formatTimestamp(task.created_at))}</small>
          </span>
          <span class="status-badge ${Forge.escape(task.status)}">${Forge.escape(task.status)}</span>
          <span class="task-meta">exit ${Forge.escape(exit)} · ${Forge.escape(duration)}</span>
        </button>
        <div class="task-output">
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
      if (card.classList.contains('open')) state.openTasks.add(id);
      else state.openTasks.delete(id);
    });
  });

  container.querySelectorAll('.copy-output').forEach(button => {
    button.addEventListener('click', async event => {
      event.stopPropagation();
      const card = button.closest('.task-card');
      const target = card.querySelector(`[data-output="${button.dataset.copy}"]`);
      try {
        await navigator.clipboard.writeText(target.textContent);
        const previous = button.textContent;
        button.textContent = 'Copiado';
        setTimeout(() => { button.textContent = previous; }, 1000);
      } catch {
        button.textContent = 'Error';
      }
    });
  });
}

async function refresh() {
  try {
    const [agent, tasks] = await Promise.all([
      Forge.request(`/api/operator/sessions/${encodeURIComponent(agentId)}`),
      Forge.request(`/api/operator/sessions/${encodeURIComponent(agentId)}/tasks`),
    ]);
    state.agent = agent;
    state.tasks = tasks.items;
    renderAgent();
    renderTerminal();
    renderTasks();
  } catch (error) {
    console.error(error);
  }
}

async function submitCommand(event) {
  event.preventDefault();
  const input = document.querySelector('#command-input');
  const button = event.currentTarget.querySelector('button[type="submit"]');
  const feedback = document.querySelector('#command-feedback');
  const command = input.value.trim();
  if (!command) return;
  button.disabled = true;
  feedback.className = 'command-feedback';
  feedback.textContent = 'Enviando tarea…';
  try {
    const task = await Forge.request(`/api/operator/sessions/${encodeURIComponent(agentId)}/tasks`, {
      method: 'POST',
      body: JSON.stringify({command, mode: state.mode}),
    });
    input.value = '';
    feedback.className = 'command-feedback success';
    feedback.textContent = `Task #${task.id} creada en modo ${task.mode}.`;
    await refresh();
  } catch (error) {
    feedback.className = 'command-feedback error';
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
    input.focus();
  }
}

window.addEventListener('DOMContentLoaded', () => {
  document.querySelector('#command-form').addEventListener('submit', submitCommand);
  document.querySelectorAll('.mode-button').forEach(button => {
    button.addEventListener('click', () => setMode(button.dataset.mode));
  });
  document.querySelector('#task-filter').addEventListener('change', event => {
    state.filter = event.target.value;
    renderTasks();
  });
  document.querySelector('#expand-all').addEventListener('click', () => {
    state.tasks.forEach(task => state.openTasks.add(String(task.id)));
    renderTasks();
  });
  document.querySelector('#collapse-all').addEventListener('click', () => {
    state.openTasks.clear();
    renderTasks();
  });
  document.querySelector('#rename-button').addEventListener('click', async () => {
    const name = prompt('Nombre visible de la sesión:', state.agent?.display_name || '');
    if (!name || !name.trim()) return;
    try {
      await Forge.request(`/api/operator/sessions/${encodeURIComponent(agentId)}`, {
        method: 'PATCH', body: JSON.stringify({display_name: name.trim()}),
      });
      await refresh();
    } catch (error) { alert(error.message); }
  });

  setMode('host');
  refresh();
  const events = new EventSource('/api/operator/events');
  events.addEventListener('refresh', refresh);
  events.onerror = () => setTimeout(refresh, 1500);
  setInterval(refresh, 5000);
});
