#!/usr/bin/env node
/**
 * MCP (Model Context Protocol) stdio server for AI Video Studio.
 * Dependency-free: newline-delimited JSON-RPC 2.0 over stdin/stdout.
 *
 * It does not render anything itself: it talks to the running app over its local
 * agent API (see docs/agent-api.md in the app), so every job lands in the app's own
 * queue, is visible to the user in the window, and its result goes to the Library.
 *
 * Tools:
 *   studio_status            is the app running, which instance and port
 *   studio_capabilities      modes, what agents may start, limits, gpu, asset roots
 *   studio_asset             a picture on disk becomes a named input
 *   studio_image_to_video    start image-to-video (or text-to-video), returns a job id
 *   studio_job               state, progress and results of a job
 *   studio_wait              wait up to 120 s for the job to finish
 *   studio_cancel            stop a queued or running job
 *   studio_library           finished work with paths on disk
 *
 * Env:
 *   H3STUDIO_AGENT_FILE  where the app publishes port+token (default %LOCALAPPDATA%\MinimaxH3Workspace\agent.json)
 *   H3STUDIO_URL         pin the API base (e.g. http://127.0.0.1:8765/api/agent/v1), needs H3STUDIO_TOKEN
 *   H3STUDIO_TOKEN       the key, when H3STUDIO_URL is pinned
 */
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const SERVER_INFO = { name: 'h3studio', version: '1.0.0' };
const INSTRUCTIONS = [
  'AI Video Studio on this computer: local image-to-video with MiniMax H3 through the user\'s own app window.',
  'The app must be running (its window or its server), and "Settings -> Агенты" must be on.',
  'Flow: studio_status -> studio_capabilities -> studio_asset (a frame) -> studio_image_to_video -> studio_wait (repeat, up to 120 s per call) -> the clip path comes back.',
  'A render takes minutes: studio_image_to_video returns at once with a job id, so poll with studio_wait instead of starting the job again. Pass idempotency_key when a retry is possible.',
  'The user sees the job in the app\'s queue and the result in its Library; do not open the file, just report the path.',
].join(' ');

const log = (...a) => process.stderr.write('[h3studio-mcp] ' + a.join(' ') + '\n');

// ------------------------------------------------------------------ discovery
function agentFile() {
  if (process.env.H3STUDIO_AGENT_FILE) return process.env.H3STUDIO_AGENT_FILE;
  const base = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(base, 'MinimaxH3Workspace', 'agent.json');
}

function target() {
  const url = process.env.H3STUDIO_URL;
  if (url && process.env.H3STUDIO_TOKEN) {
    return { url: url.replace(/\/+$/, ''), token: process.env.H3STUDIO_TOKEN, file: null };
  }
  const file = agentFile();
  let data;
  try {
    data = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    throw new Error(
      `AI Video Studio is not running: ${file} is missing. ` +
        'Open the app (its window), then try again — the file appears by itself at start.',
    );
  }
  if (!data.port || !data.token) {
    throw new Error(`${file} has no port or token: the app probably just closed. Open it again.`);
  }
  return {
    url: `http://127.0.0.1:${data.port}/api/agent/v1`,
    token: data.token,
    file,
    instance: data.instance,
    version: data.version,
    port: data.port,
  };
}

async function api(method, endpoint, body, timeout = 30000) {
  const t = target();
  let res;
  try {
    res = await fetch(t.url + endpoint, {
      method,
      headers: {
        'X-Agent-Token': t.token,
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeout),
    });
  } catch (err) {
    if (err && err.name === 'TimeoutError') {
      throw new Error(`the app did not answer in ${Math.round(timeout / 1000)} s (${method} ${endpoint})`);
    }
    throw new Error(
      `cannot reach the app at ${t.url}: ${err && err.message ? err.message : err}. ` +
        (t.file ? `Is it still open? (${t.file} may be left from a previous run.)` : ''),
    );
  }
  const raw = await res.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    data = {};
  }
  if (!res.ok) {
    const e = data.error || {};
    const hint = e.hint ? ` — ${e.hint}` : '';
    throw new Error(`${e.code || 'HTTP ' + res.status}: ${e.message || raw.slice(0, 200)}${hint}`);
  }
  return data;
}

const text = (s) => [{ type: 'text', text: s }];

const seconds = (v) => (v ? `${Number(v).toFixed(1)} s` : '—');

function progressLine(p = {}) {
  if (!p || !p.stage) return 'progress: —';
  const step = p.total ? ` step ${p.step || 0}/${p.total}` : '';
  const scene = p.scenes ? ` scene ${p.scene || 1}/${p.scenes}` : '';
  const pct = typeof p.percent === 'number' ? ` · ${Math.round(p.percent * 100)}%` : '';
  return `progress: ${p.stage}${step}${scene}${pct}`;
}

function resultLines(results = []) {
  if (!results.length) return [];
  const out = ['results:'];
  for (const r of results) {
    out.push(
      ` - ${r.library_id}: ${r.kind || 'video'}${r.res ? `, ${r.res}` : ''}${r.seconds ? `, ${seconds(r.seconds)}` : ''}`,
    );
    if (r.path) out.push(`   file: ${r.path}`);
    if (r.url) out.push(`   url: ${r.url}`);
  }
  return out;
}

// ------------------------------------------------------------------ tools
async function studioStatus() {
  const t = target();
  const ping = await api('GET', '/ping', null, 5000);
  return {
    content: text(
      [
        `app: ${ping.app} ${ping.version} (api ${ping.api})`,
        `instance: ${ping.instance}` + (t.file ? `, ${t.file}` : ''),
        t.port ? `port: ${t.port}` : null,
        'status: running',
      ]
        .filter(Boolean)
        .join('\n'),
    ),
    structuredContent: { running: true, ...ping, port: t.port || null, file: t.file || null },
  };
}

async function studioCapabilities() {
  const caps = await api('GET', '/capabilities', null, 20000);
  const gpu = caps.gpu || {};
  const canStart = (caps.jobs && caps.jobs.can_start) || [];
  const enabled = caps.agents && caps.agents.enabled;
  return {
    content: text(
      [
        `agents: ${enabled ? 'on' : 'OFF (Settings -> Агенты)'}`,
        `comfy: ${caps.comfy && caps.comfy.online ? 'online' : 'offline'}${caps.comfy ? ' · ' + caps.comfy.url : ''}`,
        gpu.name ? `gpu: ${gpu.name}, vram ${gpu.vram_used_gb ?? '?'}/${gpu.vram_total_gb ?? '?'} GB` : null,
        `modes: ${(caps.modes || []).join(', ')}`,
        `agents can start: ${canStart.length ? canStart.join(', ') : 'nothing right now'}`,
        caps.limits
          ? `limits: ${caps.limits.max_seconds} s max, aspects ${caps.limits.aspects.join(' ')}`
          : null,
        `asset roots: ${(caps.agents && caps.agents.asset_roots) || []}`,
        caps.cloud && caps.cloud.enabled ? 'cloud: RunningHub is on (agents cannot start jobs there)' : null,
      ]
        .filter(Boolean)
        .join('\n'),
    ),
    structuredContent: caps,
  };
}

async function studioAsset(args = {}) {
  if (!args.path) throw new Error('path is required: a full path to a picture or a video on this computer');
  const asset = await api('POST', '/assets', { path: args.path }, 120000);
  return {
    content: text(
      [
        `asset: ${asset.asset}`,
        `kind: ${asset.kind}` + (asset.width ? ` · ${asset.width}×${asset.height}` : ''),
        `bytes: ${asset.bytes}` + (asset.deduplicated ? ' (already known)' : ''),
        `file: ${asset.path}`,
      ].join('\n'),
    ),
    structuredContent: asset,
  };
}

async function studioImageToVideo(args = {}) {
  const body = { mode: 'video', client: args.client || 'dsh' };
  if (args.prompt) body.prompt = args.prompt;
  const params = {};
  for (const key of ['seconds', 'aspect', 'quality', 'seed', 'loop']) {
    if (args[key] !== undefined && args[key] !== null) params[key] = args[key];
  }
  if (Object.keys(params).length) body.params = params;
  const inputs = {};
  if (args.first_frame) inputs.first_frame = args.first_frame;
  if (args.last_frame) inputs.last_frame = args.last_frame;
  if (Object.keys(inputs).length) body.inputs = inputs;
  if (args.idempotency_key) body.idempotency_key = String(args.idempotency_key);

  const job = await api('POST', '/jobs', body, 120000);
  const first = job.inputs && job.inputs.first_frame;
  return {
    content: text(
      [
        `job: ${job.job}${job.deduplicated ? ' (this key already had a job; not started again)' : ''}`,
        `state: ${job.state}`,
        `seed: ${job.seed} · ${seconds(job.seconds)} · ${job.res} · ${job.quality}`,
        first ? `first frame: asset ${first.asset} -> ${first.comfy}` : 'text to video (no frame)',
        `the user sees it in the app queue. Poll: studio_wait {"job": "${job.job}"}`,
      ].join('\n'),
    ),
    structuredContent: job,
  };
}

function jobViewText(v) {
  const lines = [`job: ${v.job}`, `state: ${v.state}`];
  if (v.mode) lines.push(`mode: ${v.mode}`);
  if (v.state === 'running' || v.state === 'queued') {
    lines.push(progressLine(v.progress));
    if (v.queue_position !== null && v.queue_position !== undefined) lines.push(`queue position: ${v.queue_position}`);
    if (v.elapsed) lines.push(`elapsed: ${Math.round(v.elapsed)} s`);
    lines.push('the job is still going; call studio_wait again');
  }
  if (v.error && v.error.message) lines.push(`error: ${v.error.message}`);
  lines.push(...resultLines(v.results));
  if (v.state === 'done' && v.results && v.results.length) {
    lines.push('the clip is already in the app Library; report the file path to the user.');
  }
  return lines.join('\n');
}

async function studioJob(args = {}) {
  if (!args.job) throw new Error('job is required (the id returned by studio_image_to_video)');
  const view = await api('GET', `/jobs/${encodeURIComponent(args.job)}`, null, 15000);
  return { content: text(jobViewText(view)), structuredContent: view };
}

async function studioWait(args = {}) {
  if (!args.job) throw new Error('job is required');
  const budget = Math.min(120, Math.max(5, Number(args.timeout_seconds) || 90));
  const deadline = Date.now() + budget * 1000;
  let view = null;
  for (;;) {
    view = await api('GET', `/jobs/${encodeURIComponent(args.job)}`, null, 15000);
    if (view.state !== 'queued' && view.state !== 'running') break;
    if (Date.now() >= deadline) break;
    await new Promise((r) => setTimeout(r, 2000));
  }
  const pending = view.state === 'queued' || view.state === 'running';
  return {
    content: text(jobViewText(view) + (pending ? `\n(waited ${budget} s, not finished yet)` : '')),
    structuredContent: { ...view, still_running: pending },
  };
}

async function studioCancel(args = {}) {
  if (!args.job) throw new Error('job is required');
  const res = await api('POST', `/jobs/${encodeURIComponent(args.job)}/cancel`, {}, 30000);
  return {
    content: text(`job: ${res.job}\nstate: ${res.state}`),
    structuredContent: res,
  };
}

async function studioLibrary(args = {}) {
  const limit = Math.min(50, Math.max(1, Number(args.limit) || 10));
  const q = `/library?limit=${limit}` + (args.mode ? `&mode=${encodeURIComponent(args.mode)}` : '');
  const data = await api('GET', q, null, 20000);
  const items = data.items || [];
  const lines = items.length ? [`finished work: ${items.length} of ${data.total}`] : ['the Library is empty'];
  for (const it of items) {
    lines.push(
      `- ${it.library_id} ${it.kind || 'video'} ${it.mode || ''}` +
        `${it.res ? ' · ' + it.res : ''}${it.seconds ? ' · ' + seconds(it.seconds) : ''}` +
        `${it.date ? ' · ' + it.date.replace('T', ' ') : ''}`,
    );
    if (it.path) lines.push(`  file: ${it.path}`);
    if (it.prompt) lines.push(`  prompt: ${String(it.prompt).split('\n')[0].slice(0, 110)}`);
  }
  return { content: text(lines.join('\n')), structuredContent: data };
}

const TOOLS = [
  {
    name: 'studio_status',
    description:
      'Is AI Video Studio running and which instance/port. Call it first: every other tool needs the app open.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'studio_capabilities',
    description:
      'What the app can do right now: installed modes, what agents are allowed to start, limits (seconds, aspects, qualities), GPU memory, folders an agent may read pictures from.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'studio_asset',
    description:
      'Make a picture on disk a named input (the name is the content hash, the same file imported twice is one asset). Returns the asset id for studio_image_to_video.',
    inputSchema: {
      type: 'object',
      properties: { path: { type: 'string', description: 'Full path, e.g. E:\\\\frames\\\\s01.png (allowed folders are in studio_capabilities).' } },
      required: ['path'],
    },
  },
  {
    name: 'studio_image_to_video',
    description:
      'Start a MiniMax H3 video job in the running app: a first frame (asset id or path) plus an H3 prompt. Returns at once with a job id; poll with studio_wait. The job appears in the user\'s queue.',
    inputSchema: {
      type: 'object',
      properties: {
        first_frame: {
          type: 'string',
          description: 'Asset id from studio_asset, or a full path to a picture. Omit for text-to-video.',
        },
        last_frame: { type: 'string', description: 'Optional last frame (asset id or path); only together with first_frame.' },
        prompt: {
          type: 'string',
          description:
            'H3 prompt: subject_definitions / summary / detailed_description / overall_soundscape / non_diegetic_music. Describe camera, action with timings and sound.',
        },
        seconds: { type: 'number', description: 'Clip length, 3..25 (default 5).' },
        aspect: { type: 'string', description: '16:9 (default), 9:16, 1:1, 4:3, 3:4, 21:9.' },
        quality: { type: 'string', description: 'From studio_capabilities limits.qualities, e.g. 480p, 720p, 1080p.' },
        seed: { type: 'number', description: 'Integer seed; omit for random (the seed is returned).' },
        loop: { type: 'boolean', description: 'Seamless loop: the clip ends on the first frame.' },
        idempotency_key: {
          type: 'string',
          description: 'Same key = same job. Pass one when the call may be retried, so a retry does not render twice.',
        },
        client: { type: 'string', description: 'Who asks (default dsh), stored with the job.' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'studio_job',
    description: 'State, progress and results of a job: queued/running/done/failed/canceled, step, scene, percent.',
    inputSchema: {
      type: 'object',
      properties: { job: { type: 'string', description: 'Job id from studio_image_to_video.' } },
      required: ['job'],
    },
  },
  {
    name: 'studio_wait',
    description:
      'Wait up to 120 s for a job to finish; returns the state and, when done, the clip path and Library id. Call it again while it is still running.',
    inputSchema: {
      type: 'object',
      properties: {
        job: { type: 'string', description: 'Job id.' },
        timeout_seconds: { type: 'number', description: 'How long to wait this call, 5..120 (default 90).' },
      },
      required: ['job'],
    },
  },
  {
    name: 'studio_cancel',
    description: 'Stop a queued or running job in the app.',
    inputSchema: {
      type: 'object',
      properties: { job: { type: 'string', description: 'Job id.' } },
      required: ['job'],
    },
  },
  {
    name: 'studio_library',
    description: 'Finished work from the app Library, newest first, with full paths on disk.',
    inputSchema: {
      type: 'object',
      properties: {
        limit: { type: 'number', description: 'How many items, 1..50 (default 10).' },
        mode: { type: 'string', description: 'Only this mode, e.g. video.' },
      },
    },
  },
];

const HANDLERS = {
  studio_status: studioStatus,
  studio_capabilities: studioCapabilities,
  studio_asset: studioAsset,
  studio_image_to_video: studioImageToVideo,
  studio_job: studioJob,
  studio_wait: studioWait,
  studio_cancel: studioCancel,
  studio_library: studioLibrary,
};

// ------------------------------------------------------------------ JSON-RPC
function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}
const ok = (id, result) => send({ jsonrpc: '2.0', id, result });
const fail = (id, code, message) => send({ jsonrpc: '2.0', id, error: { code, message } });

async function handle(msg) {
  const { id, method, params } = msg;
  switch (method) {
    case 'initialize': {
      const requested = params && params.protocolVersion;
      return ok(id, {
        protocolVersion: requested || '2025-06-18',
        capabilities: { tools: { listChanged: false } },
        serverInfo: SERVER_INFO,
        instructions: INSTRUCTIONS,
      });
    }
    case 'notifications/initialized':
    case 'initialized':
      return;
    case 'ping':
      return ok(id, {});
    case 'tools/list':
      return ok(id, { tools: TOOLS });
    case 'resources/list':
      return ok(id, { resources: [] });
    case 'prompts/list':
      return ok(id, { prompts: [] });
    case 'tools/call': {
      const name = params && params.name;
      const args = (params && params.arguments) || {};
      const handler = HANDLERS[name];
      if (!handler) return fail(id, -32602, `unknown tool: ${name}`);
      try {
        const result = await handler(args);
        return ok(id, { ...result, isError: false });
      } catch (err) {
        log('tool error:', err && err.stack ? err.stack : String(err));
        return ok(id, {
          content: text('ERROR: ' + (err && err.message ? err.message : String(err))),
          isError: true,
        });
      }
    }
    default:
      if (id === undefined) return;
      return fail(id, -32601, `method not found: ${method}`);
  }
}

let buf = '';
let pending = 0;
let stdinEnded = false;
function maybeExit() {
  if (stdinEnded && pending === 0) setTimeout(() => process.exit(0), 50);
}

process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buf += chunk;
  let nl;
  while ((nl = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, nl).trim();
    buf = buf.slice(nl + 1);
    if (!line) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      log('bad JSON:', line.slice(0, 200));
      continue;
    }
    pending++;
    Promise.resolve(handle(msg))
      .catch((err) => {
        log('handler error:', err && err.stack ? err.stack : String(err));
        if (msg && msg.id !== undefined) fail(msg.id, -32603, String(err && err.message ? err.message : err));
      })
      .finally(() => {
        pending--;
        maybeExit();
      });
  }
});
process.stdin.on('end', () => {
  stdinEnded = true;
  maybeExit();
});
log('ready');
