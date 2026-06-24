const root = document.querySelector('#session-root');
const agentId = root.dataset.agentId;

const state = {
  agent: null,
  tasks: [],
};

const QUICK_COMMANDS = {
  linux: [
    'hostname', 'whoami', 'id', 'uname -a', 'uptime',
    'ip -br addr', 'ip route', 'ss -lntup', 'ps aux', 'df -h',
    'free -h', 'cat /etc/os-release', 'ls -la',
  ],
  windows: [
    'hostname', 'whoami', 'whoami /all', 'systeminfo',
    'ipconfig /all', 'tasklist /v', 'netstat -ano', 'route print',
  ],
};

function promptText() {
  const username = state.agent?.username || 'unknown';
  const hostname = state.agent?.hostname || state.agent?.display_name || 'unknown';
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
  document.querySelector('#terminal-prompt').textContent = promptText();
  document.querySelector('#command-input').placeholder = a.os === 'windows' ? 'systeminfo' : 'hostname';
  renderQuickCommands();
}

function renderQuickCommands() {
  const container = document.querySelector('#quick-commands');
  const commands = state.agent?.os === 'windows' ? QUICK_COMMANDS.windows : QUICK_COMMANDS.linux;
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

function renderTerminal() {
  const terminal = document.querySelector('#terminal-transcript');
  const wasNearBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 80;
  const items = [...state.tasks].reverse().slice(-60);
  if (!items.length) {
    terminal.innerHTML = '<div class="terminal-welcome"><strong>Cybersen Forge session console</strong><span>Escribe un comando para comenzar.</span></div>';
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
          <span class="terminal-entry-prompt">${Forge.escape(promptText())}</span>
          <code>${Forge.escape(task.command)}</code>
          <span class="terminal-mode-badge">system</span>
        </div>
        ${waiting
          ? `<div class="terminal-wait"><span class="spinner"></span>${Forge.escape(statusText(task))}</div>`
          : `<pre>${Forge.escape(output)}</pre>`}
        <div class="terminal-entry-meta">${Forge.escape(metadata)} · ${Forge.escape(formatTimestamp(task.created_at))}</div>
      </div>`;
  }).join('');
  if (wasNearBottom) terminal.scrollTop = terminal.scrollHeight;
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
      body: JSON.stringify({command, mode: 'host'}),
    });
    input.value = '';
    feedback.className = 'command-feedback success';
    feedback.textContent = `Task #${task.id} creada.`;
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

  refresh();
  const events = new EventSource('/api/operator/events');
  events.addEventListener('refresh', refresh);
  events.onerror = () => setTimeout(refresh, 1500);
  setInterval(refresh, 5000);
});
