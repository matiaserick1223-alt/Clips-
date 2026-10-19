const $ = id => document.getElementById(id);
const input = $('videoInput');
let currentJobId = null;
let currentFile = null;
let maxUploadMb = 250;
let polling = false;
const formatBytes = n => n < 1048576 ? `${(n/1024).toFixed(0)} KB` : `${(n/1048576).toFixed(1)} MB`;
const safeError = (data, status) => typeof data?.detail === 'string' ? data.detail : `Error del servidor (HTTP ${status}).`;
const clipFile = (job, name) => `/api/jobs/${encodeURIComponent(job)}/files/${encodeURIComponent(name)}`;
const headers = () => ({'X-App-Pin': $('pin').value.trim()});
function error(message) {
  $('errorSection').textContent = message;
  $('errorSection').classList.remove('hidden');
  $('progressSection').classList.add('hidden');
}
function clearError() { $('errorSection').classList.add('hidden'); }
function setProgress(title, note, pct) {
  $('progressSection').classList.remove('hidden');
  $('progressTitle').textContent = title;
  $('progressText').textContent = note;
  $('progressFill').style.width = `${Math.min(pct, 98)}%`;
  $('uploadPct').textContent = pct <= 90 ? `${Math.round(pct)}%` : 'El procesamiento puede tardar varios minutos';
}
function updatePick(file) {
  currentFile = file;
  if(file) {
    $('selectedFile').classList.remove('hidden');
    $('selectedName').textContent = file.name;
    $('selectedSize').textContent = formatBytes(file.size);
    $('fileHeading').textContent = 'Video seleccionado';
  } else {
    input.value = '';
    $('selectedFile').classList.add('hidden');
    $('fileHeading').textContent = 'Toca para subir un video';
  }
}
$('pickVideo').addEventListener('click', () => input.click());
$('clearVideo').addEventListener('click', () => updatePick(null));
input.addEventListener('change', e => {
  const f = e.target.files?.[0];
  if (!f) return;
  if (!/\.(mp4|mov|mkv|avi|webm)$/i.test(f.name)) { error('Elige un archivo MP4, MOV, MKV, AVI o WEBM.'); return; }
  if(f.size > maxUploadMb*1048576) { error(`Tu video pesa ${formatBytes(f.size)}. El límite es ${maxUploadMb} MB.`); return; }
  clearError(); updatePick(f);
});
$('clipCount').addEventListener('input', e => $('clipCountVal').textContent = `${e.target.value} ${+e.target.value === 1 ? 'clip':'clips'}`);
$('clipDuration').addEventListener('input', e => $('clipDurationVal').textContent = `${e.target.value} s`);
$('subtitles').addEventListener('change', e => $('languageRow').classList.toggle('hidden', !e.target.checked));
$('languageRow').classList.add('hidden');
function upload(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.setRequestHeader('X-App-Pin', $('pin').value.trim());
    xhr.upload.onprogress = ev => {
      if(ev.lengthComputable) setProgress('Subiendo video…', 'Enviando desde tu celular al servidor.', 10+75*ev.loaded/ev.total);
    };
    xhr.onload = () => {
      let result;
      try { result = JSON.parse(xhr.responseText); } catch { result = null; }
      if(xhr.status >= 200 && xhr.status < 300) resolve(result);
      else reject(new Error(safeError(result, xhr.status)));
    };
    xhr.onerror = () => reject(new Error('Se perdió la conexión. Revisa tu internet.'));
    xhr.ontimeout = () => reject(new Error('La carga tardó demasiado. Intenta con un video más corto.'));
    xhr.timeout = 20*60*1000;
    xhr.send(form);
  });
}
const sleep = ms => new Promise(res => setTimeout(res, ms));
async function pollJob(id) {
  if(polling) return;
  polling = true;
  try {
    let attempts = 0;
    while(attempts++ < 1200) {
      const response = await fetch(`/api/jobs/${encodeURIComponent(id)}`, {headers:headers(), cache:'no-store'});
      const job = await response.json();
      if(!response.ok) throw new Error(safeError(job, response.status));
      if(job.state === 'done') {
        $('progressSection').classList.add('hidden');
        renderResults(id, job);
        $('generate').disabled = false;
        return;
      }
      if(job.state === 'error') throw new Error(job.status || 'Hubo un problema creando el clip.');
      setProgress('Creando tus clips…', job.status || 'Analizando el video…', 92);
      await sleep(3000);
    }
    throw new Error('El trabajo está tardando demasiado. Prueba con un video más corto.');
  } catch (err) { error(err.message); $('generate').disabled = false; }
  finally { polling = false; }
}
$('generate').addEventListener('click', async () => {
  clearError();
  $('resultsSection').classList.add('hidden');
  if(!currentFile) return error('Selecciona un video para empezar.');
  if(!$('authorized').checked) return error('Confirma que tienes derecho a reutilizar este video.');
  if(currentFile.size > maxUploadMb * 1048576) return error(`Tu archivo supera los ${maxUploadMb} MB.`);
  const form = new FormData();
  form.append('video', currentFile);
  form.append('count', $('clipCount').value);
  form.append('duration', $('clipDuration').value);
  form.append('framing', document.querySelector('input[name="framing"]:checked').value);
  form.append('subtitles', $('subtitles').checked);
  form.append('language', $('language').value);
  form.append('authorized', 'true');
  $('generate').disabled = true;
  setProgress('Preparando tu video…', 'Comenzando la carga.', 4);
  try {
    const out = await upload(form);
    currentJobId = out.job_id;
    sessionStorage.setItem('kaiclip_job', currentJobId);
    setProgress('Buscando los mejores momentos…', 'Tu video se está procesando.', 90);
    await pollJob(currentJobId);
  } catch(err) { error(err.message || 'Ocurrió un error al procesar el video.'); $('generate').disabled = false; }
});
function makeButtonAction(btn, callback) {
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try { await callback(); } catch(err) { error(`No se pudo compartir: ${err.message}`); }
    finally { btn.disabled = false; }
  });
}
function renderResults(id, job) {
  const list = $('clipsList');
  list.replaceChildren();
  $('warnings').replaceChildren();
  for(const warning of job.warnings || []) {
    const p = document.createElement('p'); p.textContent = `⚠ ${warning}`; $('warnings').appendChild(p);
  }
  for(const clip of job.clips || []) {
    const node = $('clipTemplate').content.cloneNode(true);
    const url = clipFile(id, clip.name);
    node.querySelector('.clip-index').textContent = `CLIP ${String(clip.number).padStart(2,'0')}`;
    node.querySelector('.clip-time').textContent = `${clip.duration} s`;
    node.querySelector('video').src = url;
    node.querySelector('.from-time').textContent = `Desde ${Math.floor(clip.start/60)}:${String(Math.floor(clip.start%60)).padStart(2,'0')} del original`;
    node.querySelector('.caption-box').textContent = clip.caption;
    const download = node.querySelector('.download-btn');
    download.href = url;
    download.download = clip.name;
    makeButtonAction(node.querySelector('.share-btn'), async () => {
      if(!navigator.share) return window.location.assign(url);
      // A tap on a mobile browser's Share control does not guarantee TikTok appears.
      const res = await fetch(url);
      if(!res.ok) throw new Error('No se pudo recuperar el clip.');
      const file = new File([await res.blob()], clip.name, {type:'video/mp4'});
      if(navigator.canShare?.({files:[file]})) {
        try { await navigator.share({files:[file],title:`KaiClip ${clip.number}`}); }
        catch(err) { if(err.name !== 'AbortError') throw err; }
      } else if(navigator.canShare?.({title:'KaiClip',url:location.origin+url})) {
        await navigator.share({title:'KaiClip',url:location.origin+url});
      } else window.location.assign(url);
    });
    makeButtonAction(node.querySelector('.copy-btn'), async () => {
      await navigator.clipboard.writeText(clip.caption);
    });
    list.appendChild(node);
  }
  $('allDownload').href = clipFile(id, 'clips_tiktok.zip');
  $('resultsSection').classList.remove('hidden');
  $('resultsSection').scrollIntoView({behavior:'smooth',block:'start'});
}
async function init() {
  try {
    const response = await fetch('/api/config');
    if(!response.ok) throw new Error('Config no disponible');
    const conf = await response.json();
    maxUploadMb = conf.max_upload_mb;
    $('fileDesc').textContent = `MP4, MOV, MKV, WEBM · Máx. ${maxUploadMb} MB`;
    $('pinField').classList.toggle('hidden', !conf.pin_required);
    if(!conf.captions_available) {
      $('subtitles').disabled = true;
      $('subtitleHelp').textContent = 'No instalado en este servidor (opcional)';
    } else {
      $('subtitleHelp').textContent = 'Whisper IA · puede tardar más en procesar';
    }
    const previous = sessionStorage.getItem('kaiclip_job');
    if(previous) {
      currentJobId = previous;
      const r = await fetch(`/api/jobs/${encodeURIComponent(previous)}`, {headers:headers()});
      if(r.ok) {
        const job = await r.json();
        if(job.state === 'done') renderResults(previous,job);
        else if(job.state === 'queued'||job.state==='processing') pollJob(previous);
      }
    }
  } catch(e) { /* The app stays usable if a network status check failed. */ }
}
init();
if('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/static/service-worker.js').catch(()=>{});
