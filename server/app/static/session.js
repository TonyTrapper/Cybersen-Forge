const root = document.querySelector('#session-root');
const agentId = root.dataset.agentId;

const state = {
  agent: null,
  tasks: [],
  filter: 'all',
  mode: 'host',
  modeInitialized: false,
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
  shell: [
    'pwd', 'ls -la', 'cd /tmp && pwd',
    'export FORGE_DEMO=Cybersen && echo "$FORGE_DEMO"',
    'mkdir -p /workspace/demo && printf "Cybersen Forge\\n" > /workspace/demo/proof.txt && cat /workspace/demo/proof.txt',
    'printf "alpha\\nbeta\\ngamma\\n" | grep beta',
  ],
};

function isShellMode(mode) {
  return mode === 'shell' || mode === 'sandbox';
}

function displayMode(mode) {
  return isShellMode(mode) ? 'shell' : 'system';
}

function promptForMode(mode) {
  const username =
    state.agent?.username ||
    state.agent?.user ||
    state.agent?.os_username ||
    "unknown";

  const hostname =
    state.agent?.hostname ||
    state.agent?.display_name ||
    "unknown";

  return `${username}@${hostname}$`;
}

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

  const shellButton = document.querySelector('#shell-mode-button');
  const shellLabel = document.querySelector('#capability-shell');
  if (a.sandbox_available) {
    shellButton.disabled = false;
    shellButton.title = '';
    shellLabel.textContent = `Shell: ${a.sandbox_runtime || 'available'}`;
    shellLabel.closest('.capability-item').classList.add('available');
  } else {
    shellButton.disabled = true;
    shellButton.title = 'Inicia el agente con FORGE_ENABLE_SHELL=true y Podman o Docker disponible.';
    shellLabel.textContent = 'Shell: no disponible';
    shellLabel.closest('.capability-item').classList.remove('available');
    if (state.mode === 'shell') setMode('host');
  }

  if (!state.modeInitialized) {
    setMode(a.sandbox_available ? 'shell' : 'host');
    state.modeInitialized = true;
  }
}

function renderQuickCommands() {
  const container = document.querySelector('#quick-commands');
  const commands = state.mode === 'shell'
    ? QUICK_COMMANDS.shell
    : (state.agent?.os === 'windows' ? QUICK_COMMANDS.windows : QUICK_COMMANDS.linux);
  container.innerHTML = '';
  commands.forEach(command => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = command;
    button.addEventListener('click', () => {
      const input = document.querySelector('#command-input');
      input.value = command;
      input.focus();
    });
    container.appendChild(button);
  });
}

function setMode(mode) {
  if (mode === 'shell' && !state.agent?.sandbox_available) return;
  state.mode = mode;
  document.querySelectorAll('.mode-button').forEach(button => {
    const active = button.dataset.mode === mode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  const input = document.querySelector('#command-input');
  const prompt = document.querySelector('#terminal-prompt');
  const description = document.querySelector('#mode-description');
  if (mode === 'shell') {
    prompt.textContent = promptForMode('shell');
    input.placeholder = 'cd /workspace && ls -la';
    description.textContent = 'Shell persistente del laboratorio: conserva directorio, variables, pipes y redirecciones dentro del runtime aislado.';
  } else {
    prompt.textContent = promptForMode('host');
    input.placeholder = state.agent?.os === 'windows' ? 'systeminfo' : 'hostname';
    description.textContent = `Vista del sistema ${state.agent?.os === 'windows' ? 'Windows' : 'Linux'} mediante tareas de diagnóstico del host.`;
  }
  renderQuickCommands();
  input.focus();
}

function renderTerminal() {
  const terminal = document.querySelector('#terminal-transcript');
  const wasNearBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 80;
  const items = [...state.tasks].reverse().slice(-60);
  if (!items.length) {
    terminal.innerHTML = '<div class="terminal-welcome"><strong>Cybersen Forge session console</strong><span>Abre Shell para trabajar en el laboratorio persistente o System para consultar el host.</span></div>';
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
          <span class="terminal-entry-prompt">${promptForMode(task.mode)}</span>
          <code>${Forge.escape(task.command)}</code>
          <span class="terminal-mode-badge">${displayMode(task.mode)}</span>
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
            <code>${promptForMode(task.mode)} ${Forge.escape(task.command)}</code>
            <small>Task #${task.id} · ${Forge.escape(formatTimestamp(task.created_at))}</small>
          </span>
          <span class="status-badge ${Forge.escape(task.status)}">${Forge.escape(task.status)}</span>
          <span class="task-meta">exit ${Forge.escape(exit)} · ${Forge.escape(duration)}</span>
        </button>
        <div class="task-output">
          <div class="output-toolbar">
            <span class="output-label">output</span>
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
    feedback.textContent = `Task #${task.id} creada en modo ${displayMode(task.mode)}.`;
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
  const form = document.querySelector('#command-form');
  const input = document.querySelector('#command-input');
  form.addEventListener('submit', submitCommand);
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  });
  document.querySelectorAll('.mode-button').forEach(button => {
    button.addEventListener('click', () => {
      state.modeInitialized = true;
      setMode(button.dataset.mode);
    });
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
