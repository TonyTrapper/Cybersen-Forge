const artifactState = {
  manifest: null,
  commands: new Map(),
};

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function normalizeServerUrl(value) {
  const trimmed = String(value || '').trim().replace(/\/+$/, '');
  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error('La URL del servidor no es válida.');
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('La URL debe comenzar con http:// o https://.');
  }
  return parsed.origin + parsed.pathname.replace(/\/+$/, '');
}

function shellSingleQuote(value) {
  return `'${String(value).replace(/'/g, `'"'"'`)}'`;
}

function powershellSingleQuote(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function linuxCommand(baseUrl, token, artifact) {
  const base = shellSingleQuote(baseUrl);
  const secret = shellSingleQuote(token);
  const download = shellSingleQuote(`${baseUrl}${artifact.download_url}`);
  const destination = '/opt/cybersen-forge/cybersen-forge';
  return `install -d -m 0755 /opt/cybersen-forge && curl -fsSL -H "X-Enrollment-Token: ${token}" ${download} -o ${destination} && chmod 0755 ${destination} && FORGE_SERVER=${base} FORGE_ENROLLMENT_TOKEN=${secret} ${destination}`;
}

function windowsCommand(baseUrl, token, artifact) {
  const base = powershellSingleQuote(baseUrl);
  const secret = powershellSingleQuote(token);
  const download = powershellSingleQuote(`${baseUrl}${artifact.download_url}`);
  const destination = `C:\\ProgramData\\CybersenForge\\cybersen-forge.exe`;
  return `$base=${base};$token=${secret};$dst='${destination}';New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null;Invoke-WebRequest -UseBasicParsing -Headers @{'X-Enrollment-Token'=$token} -Uri ${download} -OutFile $dst;$env:FORGE_SERVER=$base;$env:FORGE_ENROLLMENT_TOKEN=$token;& $dst`;
}

function setFeedback(message, type = '') {
  const element = document.querySelector('#artifact-feedback');
  element.className = `command-feedback ${type}`.trim();
  element.textContent = message;
}

function renderArtifacts() {
  const container = document.querySelector('#artifact-grid');
  const manifest = artifactState.manifest;
  if (!manifest || !manifest.items.length) {
    container.innerHTML = '<div class="empty-state panel">No hay artefactos disponibles.</div>';
    return;
  }

  container.innerHTML = manifest.items.map(item => `
    <article class="panel artifact-card" data-platform="${Forge.escape(item.platform)}">
      <div class="artifact-card-header">
        <div class="artifact-icon">${item.platform === 'windows' ? 'W' : 'L'}</div>
        <div>
          <p class="eyebrow">${Forge.escape(item.platform.toUpperCase())}</p>
          <h2>${Forge.escape(item.label)}</h2>
          <span class="status-badge ${item.available ? 'online' : 'offline'}">${item.available ? 'disponible' : 'no disponible'}</span>
        </div>
      </div>
      <dl class="artifact-meta">
        <div><dt>Versión</dt><dd>${Forge.escape(manifest.version)}</dd></div>
        <div><dt>Tamaño</dt><dd>${Forge.escape(formatBytes(item.size_bytes))}</dd></div>
        <div><dt>Archivo</dt><dd>${Forge.escape(item.filename)}</dd></div>
        <div><dt>SHA256</dt><dd class="checksum" title="${Forge.escape(item.sha256)}">${Forge.escape(item.sha256 || '—')}</dd></div>
      </dl>
      <div class="artifact-actions">
        <button type="button" class="secondary-button download-artifact" ${item.available ? '' : 'disabled'}>Descargar</button>
        <button type="button" class="primary-button generate-command" ${item.available ? '' : 'disabled'}>Generar comando</button>
      </div>
      <div class="generated-command" hidden>
        <div class="generated-command-header">
          <span>Comando listo para copiar</span>
          <button type="button" class="copy-command secondary-button">Copiar</button>
        </div>
        <pre></pre>
        <small class="token-expiry"></small>
      </div>
    </article>
  `).join('');

  container.querySelectorAll('.artifact-card').forEach(card => {
    const platform = card.dataset.platform;
    const item = manifest.items.find(candidate => candidate.platform === platform);
    card.querySelector('.download-artifact').addEventListener('click', () => downloadArtifact(item));
    card.querySelector('.generate-command').addEventListener('click', () => generateCommand(card, item));
    card.querySelector('.copy-command').addEventListener('click', () => copyCommand(card, platform));
  });
}

async function downloadArtifact(item) {
  setFeedback(`Descargando ${item.filename}…`);
  try {
    const response = await fetch(item.download_url, {credentials: 'same-origin'});
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = item.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setFeedback(`${item.filename} descargado.`, 'success');
  } catch (error) {
    setFeedback(error.message, 'error');
  }
}

async function generateCommand(card, item) {
  const button = card.querySelector('.generate-command');
  button.disabled = true;
  setFeedback(`Generando credencial temporal para ${item.label}…`);
  try {
    const baseUrl = normalizeServerUrl(document.querySelector('#server-url').value);
    localStorage.setItem('forge-server-url', baseUrl);
    const credential = await Forge.request('/api/operator/enrollment-tokens', {
      method: 'POST',
      body: JSON.stringify({platform: item.platform, ttl_seconds: 600, max_uses: 1}),
    });
    const command = item.platform === 'windows'
      ? windowsCommand(baseUrl, credential.token, item)
      : linuxCommand(baseUrl, credential.token, item);
    artifactState.commands.set(item.platform, command);
    const generated = card.querySelector('.generated-command');
    generated.hidden = false;
    generated.querySelector('pre').textContent = command;
    generated.querySelector('.token-expiry').textContent = `Token de un solo uso · vence ${new Date(credential.expires_at).toLocaleString()}`;
    setFeedback(`Comando ${item.label} generado.`, 'success');
  } catch (error) {
    setFeedback(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function copyCommand(card, platform) {
  const command = artifactState.commands.get(platform) || card.querySelector('.generated-command pre').textContent;
  try {
    await navigator.clipboard.writeText(command);
    const button = card.querySelector('.copy-command');
    const previous = button.textContent;
    button.textContent = 'Copiado';
    setTimeout(() => { button.textContent = previous; }, 1200);
  } catch {
    setFeedback('No se pudo copiar el comando.', 'error');
  }
}

async function initializeArtifacts() {
  try {
    artifactState.manifest = await Forge.request('/api/operator/artifacts');
    const configured = artifactState.manifest.public_base_url;
    const stored = localStorage.getItem('forge-server-url');
    document.querySelector('#server-url').value = stored || configured || window.location.origin;
    renderArtifacts();
  } catch (error) {
    setFeedback(error.message, 'error');
  }
}

window.addEventListener('DOMContentLoaded', () => {
  document.querySelector('#use-current-url').addEventListener('click', () => {
    document.querySelector('#server-url').value = window.location.origin;
    localStorage.setItem('forge-server-url', window.location.origin);
  });
  initializeArtifacts();
});
