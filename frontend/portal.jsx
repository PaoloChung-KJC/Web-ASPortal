// KJC 고객 A/S 접수 사이트 — CRM 화면(app.jsx)과 코드를 나누지 않는 단독 화면.
// 서버 주소는 /api/portal (로컬 http://localhost:8030/, 운영은 이 폴더가 사이트의 /).
const { useState, useEffect, useRef, useCallback } = React;

const API = "/api/portal";
const TOKEN_KEY = "kjc_as_token";
const DRAFT_KEY = "kjc_as_draft";
const REPORTER_KEY = "kjc_as_reporter";
const NET_MSG = "연결이 원활하지 않습니다. 잠시 후 다시 시도해 주세요";
const MUST_CHANGE_MSG = "비밀번호를 먼저 변경해 주세요";
const BAD_PHOTO_MSG = "이 사진은 올릴 수 없습니다";
const PHOTO_MAX = 10;
const PHOTO_LONG_SIDE = 2048; // 2026-11-24: 2K (전에는 1600)
// 2026-11-24: 영상 — 길이 제한 없음, 원본 한 개 1GB 까지. 서버가 FHD 로 줄여 저장한다(backend VIDEO_MAX_MB 와 같게)
const VIDEO_MAX = 3;
const VIDEO_MAX_MB = 1024;
// 2026-11-25: 영상은 1분까지. 넘으면 구간 선택 창에서 1분을 고르고, 서버가 그 구간만 떼어 저장한다(backend video.TRIM_MAX_SEC)
const VIDEO_MAX_SEC = 60;
const VIDEO_EXT = /\.(mp4|m4v|mov|3gp|3g2|webm|mkv)$/i;
// 2026-11-24: 증상 예시 — 고객이 골라 쓰고, 없거나 더 적을 내용은 '기타 · 추가 증상 설명'에
const SYMPTOM_EXAMPLES = [
  "프린터와 바코드 전원은 들어오나 화면이 안나옵니다.",
  "화면은 켜지나 이상하게 나옵니다.",
  "화면에 물방울이 생기고 터치가 안됩니다.",
  "프린터 출력시 글자가 이상하게 나옵니다(깨져서 출력되는거 포함).",
  "프린터 출력이 안됩니다.",
];
const PW_MIN = 8;
const QTY_MAX = 9999;
const FILTERS = [["all", "전체"], ["progress", "진행 중"], ["done", "완료·취소"]];

// ---- 브라우저 저장소 (사생활 보호 모드 등에서 막혀도 화면은 동작) ----

function readStore(kind, key) {
  try { return window[kind].getItem(key); } catch (e) { return null; }
}

function writeStore(kind, key, value) {
  try {
    if (value == null) window[kind].removeItem(key);
    else window[kind].setItem(key, value);
  } catch (e) { /* 저장 못 해도 화면은 계속 */ }
}

function readJson(kind, key) {
  const s = readStore(kind, key);
  if (!s) return null;
  try { return JSON.parse(s); } catch (e) { return null; }
}

// ---- 서버 호출 ----

// 401·403(비밀번호 변경 필요)은 App 이 받아 화면을 바꾼다
const authEvents = { onLost: null, onMustChange: null };

function apiError(status, message) {
  const e = new Error(message);
  e.status = status;
  return e;
}

async function api(path, opts = {}) {
  const { method = "GET", body, form, blob } = opts;
  const headers = {};
  const token = readStore("localStorage", TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(API + path, { method, headers, body: payload, cache: "no-store" });
  } catch (e) {
    throw apiError(0, NET_MSG);
  }
  if (res.ok) {
    try { return blob ? await res.blob() : await res.json(); } catch (e) { throw apiError(0, NET_MSG); }
  }
  let detail = "";
  try {
    const j = await res.json();
    if (j && typeof j.detail === "string") detail = j.detail;
  } catch (e) { /* 본문이 JSON 이 아니면 아래 기본 문구 */ }
  if (res.status === 401 && path !== "/login") {
    if (authEvents.onLost) authEvents.onLost(detail);
  } else if (res.status === 403 && detail === MUST_CHANGE_MSG) {
    if (authEvents.onMustChange) authEvents.onMustChange();
  }
  if (!detail) detail = res.status >= 502 ? NET_MSG : `요청을 처리하지 못했습니다 (오류 ${res.status})`;
  throw apiError(res.status, detail);
}

// 사진·영상 한 개 올리기. 진행률(0~100)을 알려 주려고 fetch 대신 XMLHttpRequest — 오류 처리는 api() 와 같게
function uploadMedia(caseId, item, n, onProgress) {
  const form = new FormData();
  form.append("file", item.blob, item.kind === "video" ? (item.name || `video-${n}.mp4`) : `photo-${n}.jpg`);
  if (item.trim) { // 고른 구간 — 서버가 이 구간(최대 1분)만 남긴다
    form.append("trim_start", item.trim.start.toFixed(3));
    form.append("trim_end", item.trim.end.toFixed(3));
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/cases/${caseId}/photos`);
    const token = readStore("localStorage", TOKEN_KEY);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    if (onProgress && xhr.upload) {
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.floor((e.loaded / e.total) * 100)); };
    }
    xhr.onerror = () => reject(apiError(0, NET_MSG));
    xhr.ontimeout = () => reject(apiError(0, NET_MSG));
    xhr.onload = () => {
      let j = null;
      try { j = JSON.parse(xhr.responseText); } catch (e) { /* 아래에서 처리 */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        if (j) resolve(j); else reject(apiError(0, NET_MSG));
        return;
      }
      let detail = j && typeof j.detail === "string" ? j.detail : "";
      if (xhr.status === 401) {
        if (authEvents.onLost) authEvents.onLost(detail);
      } else if (xhr.status === 403 && detail === MUST_CHANGE_MSG) {
        if (authEvents.onMustChange) authEvents.onMustChange();
      }
      if (xhr.status === 413) detail = `파일이 너무 큽니다. 영상은 한 개 ${VIDEO_MAX_MB.toLocaleString()}MB 까지 올릴 수 있습니다`;
      if (!detail) detail = xhr.status >= 502 ? NET_MSG : `요청을 처리하지 못했습니다 (오류 ${xhr.status})`;
      reject(apiError(xhr.status, detail));
    };
    xhr.send(form);
  });
}

const isVideoFile = (f) => (f.type ? f.type.startsWith("video/") : VIDEO_EXT.test(f.name || ""));
const countKind = (list, kind) => list.filter((x) => (x.kind || "photo") === kind).length;

// 증상 = 고른 예시(· 로 시작하는 줄) + 빈 줄 + 기타 · 추가 설명. 직원 화면에는 이 글이 그대로 '증상'으로 보인다
function composeSymptom(picks, extra) {
  const lines = picks.map((p) => `· ${p}`).join("\n");
  const more = (extra || "").trim();
  if (lines && more) return `${lines}\n\n${more}`;
  return lines || more;
}

// 저장된 증상 글을 다시 (고른 예시, 추가 설명)으로 나눈다. 예전 접수(자유 글)는 전부 추가 설명으로
function splitSymptom(text) {
  const lines = String(text || "").split("\n");
  const picks = [];
  let i = 0;
  while (i < lines.length && lines[i].startsWith("· ") && SYMPTOM_EXAMPLES.includes(lines[i].slice(2))) {
    picks.push(lines[i].slice(2));
    i += 1;
  }
  if (picks.length && i < lines.length && lines[i] === "") i += 1;
  return { picks, extra: lines.slice(i).join("\n") };
}

function newClientKey() {
  try {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
  } catch (e) { /* 아래 방식으로 */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

// ---- 사진 줄이기 (긴 변 1600px, JPEG 0.8) ----

async function decodeImage(file) {
  if (typeof window.createImageBitmap === "function") {
    try {
      const bmp = await window.createImageBitmap(file);
      return { src: bmp, w: bmp.width, h: bmp.height, release: () => { if (bmp.close) bmp.close(); } };
    } catch (e) { /* 아래 <img> 방식으로 다시 시도 */ }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error("decode"));
      im.src = url;
    });
    return { src: img, w: img.naturalWidth, h: img.naturalHeight, release: () => URL.revokeObjectURL(url) };
  } catch (e) {
    URL.revokeObjectURL(url);
    throw e;
  }
}

async function shrinkImage(file) {
  const d = await decodeImage(file);
  const canvas = document.createElement("canvas");
  try {
    if (!d.w || !d.h) throw new Error("size");
    const scale = Math.min(1, PHOTO_LONG_SIDE / Math.max(d.w, d.h));
    canvas.width = Math.max(1, Math.round(d.w * scale));
    canvas.height = Math.max(1, Math.round(d.h * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas");
    ctx.fillStyle = "#ffffff"; // 투명 PNG 는 흰 바탕으로
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(d.src, 0, 0, canvas.width, canvas.height);
    const out = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.8));
    if (!out) throw new Error("encode");
    return out;
  } finally {
    d.release();
    canvas.width = 0; // 폰 메모리 반환
    canvas.height = 0;
  }
}

// 고른 파일들을 준비한다: 사진은 줄이고(2K), 영상은 그대로(서버가 FHD 로 줄인다).
// 한도(사진 10장 · 영상 3개)를 넘는 몫은 받지 않고, 쓸 수 없는 파일은 이름을 모아 알린다
async function prepareFiles(files, photoRoom, videoRoom) {
  const items = [];
  const bad = [];
  const tooBig = [];
  let overPhoto = 0;
  let overVideo = 0;
  const skipped = [];
  for (const f of files) {
    if (isVideoFile(f)) {
      if (videoRoom <= 0) { overVideo += 1; continue; }
      if (f.size > VIDEO_MAX_MB * 1024 * 1024) { tooBig.push(f.name || "영상"); continue; }
      // 길이를 잰다. 1분이 넘으면 구간을 고르게 하고, 길이를 못 재면(브라우저가 못 여는 형식) 서버가 앞 1분만 남긴다
      const duration = await videoDuration(f);
      let trim = null;
      if (duration == null) trim = { start: 0, end: VIDEO_MAX_SEC };
      else if (duration > VIDEO_MAX_SEC + 0.5) {
        trim = await askTrim(f, duration, { start: 0, end: VIDEO_MAX_SEC });
        if (!trim) { skipped.push(f.name || "영상"); continue; }
      }
      videoRoom -= 1;
      items.push({ kind: "video", blob: f, name: f.name || "", duration, trim });
      continue;
    }
    if (photoRoom <= 0) { overPhoto += 1; continue; }
    try {
      items.push({ kind: "photo", blob: await shrinkImage(f) });
      photoRoom -= 1;
    } catch (e) { bad.push(f.name || "사진"); }
  }
  const notes = [];
  if (bad.length) notes.push({ kind: "warn", text: `${BAD_PHOTO_MSG} — ${bad.join(", ")}` });
  if (tooBig.length) notes.push({ kind: "warn", text: `영상은 한 개 ${VIDEO_MAX_MB.toLocaleString()}MB 까지 올릴 수 있습니다 — ${tooBig.join(", ")}` });
  if (overPhoto > 0) notes.push({ kind: "warn", text: `사진은 최대 ${PHOTO_MAX}장까지 올릴 수 있습니다. ${overPhoto}장은 넣지 않았습니다` });
  if (overVideo > 0) notes.push({ kind: "warn", text: `영상은 최대 ${VIDEO_MAX}개까지 올릴 수 있습니다. ${overVideo}개는 넣지 않았습니다` });
  if (skipped.length) notes.push({ kind: "warn", text: `구간을 고르지 않아 넣지 않았습니다 — ${skipped.join(", ")}` });
  return { items, notes };
}

// 영상 길이(초). 브라우저가 못 여는 형식이거나 8초 안에 못 읽으면 null
function videoDuration(file) {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    const url = URL.createObjectURL(file);
    let done = false;
    const finish = (d) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      v.removeAttribute("src");
      try { v.load(); } catch (e) { /* 무시 */ }
      URL.revokeObjectURL(url);
      resolve(d);
    };
    const timer = setTimeout(() => finish(null), 8000);
    v.preload = "metadata";
    v.muted = true;
    v.onloadedmetadata = () => {
      if (Number.isFinite(v.duration) && v.duration > 0) { finish(v.duration); return; }
      // 일부 webm 은 길이가 Infinity 로 나온다 → 끝으로 한 번 옮겨 보면 진짜 길이가 나온다
      v.ontimeupdate = () => { v.ontimeupdate = null; finish(Number.isFinite(v.duration) ? v.duration : null); };
      try { v.currentTime = 1e9; } catch (e) { finish(null); }
    };
    v.onerror = () => finish(null);
    v.src = url;
  });
}

// 2026-11-25: 웹 화면의 [찍기]로 바로 찍으면 폰이 화질을 낮춰 넘긴다(아이폰 360×480 확인) → 카메라 앱으로 찍고 고르게 안내.
// 아이폰은 '사진 보관함', 안드로이드(갤럭시 등)는 '갤러리' — 모르면 둘 다 적는다
function phoneKind() {
  const ua = (navigator.userAgent || "") + " " + (navigator.platform || "");
  if (/iPhone|iPad|iPod/i.test(ua) || (/Mac/i.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
  if (/Android/i.test(ua)) return "android";
  return "";
}

function CaptureTip() {
  const kind = phoneKind();
  const where = kind === "ios" ? "사진 보관함" : kind === "android" ? "갤러리" : "사진 보관함(갤러리)";
  return (
    <p className="capture-tip">
      <b>영상은 휴대폰 카메라 앱으로 먼저 찍어 주세요.</b> 그다음 아래 버튼을 눌러 <b>{where}</b>에서 고르면 됩니다.
      이 화면에서 바로 찍으면 화질이 낮게 저장될 수 있습니다.
    </p>
  );
}

function fmtTime(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ---- 영상 구간 선택 (1분이 넘는 영상) — 어디서든 askTrim() 으로 열고, 고른 구간 {start, end} 또는 null(취소)을 받는다 ----

const trimBus = { open: null };
function askTrim(file, duration, initial) {
  return new Promise((resolve) => {
    if (!trimBus.open) { resolve(initial); return; } // 창을 띄울 곳이 없으면 앞 1분
    trimBus.open({ file, duration, initial, resolve });
  });
}

function TrimHost() {
  const [req, setReq] = useState(null);
  useEffect(() => {
    trimBus.open = (r) => setReq(r);
    return () => { trimBus.open = null; };
  }, []);
  if (!req) return null;
  const close = (result) => { req.resolve(result); setReq(null); };
  return <TrimSheet file={req.file} duration={req.duration} initial={req.initial} onDone={close} />;
}

function TrimSheet({ file, duration, initial, onDone }) {
  const maxLen = Math.min(VIDEO_MAX_SEC, duration);
  const [range, setRange] = useState(() => {
    const s = Math.max(0, Math.min(initial.start, duration - 1));
    return { start: s, end: Math.min(duration, Math.max(s + 1, Math.min(initial.end, s + maxLen))) };
  });
  const [url] = useState(() => URL.createObjectURL(file));
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef(null);
  const trackRef = useRef(null);
  const boxRef = useRef(null);
  const drag = useRef(null);
  const rangeRef = useRef(range);
  rangeRef.current = range;

  useEffect(() => {
    const prev = document.activeElement;
    if (boxRef.current) boxRef.current.focus();
    document.body.classList.add("no-scroll");
    return () => {
      document.body.classList.remove("no-scroll");
      URL.revokeObjectURL(url);
      if (prev && prev.focus) prev.focus();
    };
  }, [url]);

  const seek = (t) => { const v = videoRef.current; if (v) { try { v.currentTime = t; } catch (e) { /* 무시 */ } } };
  const stop = () => { const v = videoRef.current; if (v) v.pause(); setPlaying(false); };
  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (playing) { stop(); return; }
    seek(range.start);
    v.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
  };
  const onTime = () => {
    const v = videoRef.current;
    if (v && playing && v.currentTime >= rangeRef.current.end) { stop(); seek(rangeRef.current.start); }
  };

  // what: "start" | "end" | "move" — 시작은 끝보다 1초 이상 앞, 길이는 최대 1분
  const apply = (what, t, grabOffset) => {
    setRange((r) => {
      let { start, end } = r;
      if (what === "start") {
        start = Math.min(Math.max(t, end - maxLen, 0), end - 1);
        seek(start);
      } else if (what === "end") {
        end = Math.max(Math.min(t, start + maxLen, duration), start + 1);
        seek(end);
      } else {
        const len = end - start;
        start = Math.min(Math.max(t - grabOffset, 0), duration - len);
        end = start + len;
        seek(start);
      }
      return { start, end };
    });
  };
  const timeAt = (clientX) => {
    const box = trackRef.current.getBoundingClientRect();
    return Math.min(Math.max((clientX - box.left) / box.width, 0), 1) * duration;
  };
  const onDown = (what) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    stop();
    const t = timeAt(e.clientX);
    drag.current = { what, offset: t - rangeRef.current.start };
    e.currentTarget.setPointerCapture && e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onMove = (e) => {
    if (!drag.current) return;
    apply(drag.current.what, timeAt(e.clientX), drag.current.offset);
  };
  const onUp = () => { drag.current = null; };
  const onKey = (what) => (e) => {
    const step = e.shiftKey ? 5 : 1;
    const delta = e.key === "ArrowLeft" || e.key === "ArrowDown" ? -step : e.key === "ArrowRight" || e.key === "ArrowUp" ? step : 0;
    if (!delta) return;
    e.preventDefault();
    stop();
    const r = rangeRef.current;
    if (what === "move") apply("move", r.start + delta, 0);
    else apply(what, (what === "start" ? r.start : r.end) + delta, 0);
  };

  const pct = (t) => `${(t / duration) * 100}%`;
  const len = range.end - range.start;
  return (
    <div className="sheet-wrap">
      <div className="sheet sheet-trim" role="dialog" aria-modal="true" aria-labelledby="trim-title" tabIndex={-1} ref={boxRef}>
        <div className="sheet-head">
          <h2 className="sheet-title" id="trim-title">영상 구간 선택</h2>
          <button type="button" className="sheet-close" onClick={() => onDone(null)} aria-label="닫기">✕</button>
        </div>
        <p className="sheet-hint">영상은 1분까지 올릴 수 있습니다(이 영상은 {fmtTime(duration)}). 양쪽 손잡이로 길이를, 가운데를 끌어 위치를 정해 주세요.</p>
        <div className="trim-video">
          <video ref={videoRef} src={url} playsInline preload="metadata" onTimeUpdate={onTime}
            onLoadedMetadata={() => seek(range.start)} onPause={() => setPlaying(false)} />
        </div>
        <div className="trim-body">
          <div className="trim-times">
            <span>{fmtTime(range.start)} ~ {fmtTime(range.end)}</span>
            <b>{fmtTime(len)} 선택</b>
          </div>
          <div className="trim-track" ref={trackRef} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
            onPointerDown={(e) => { // 빈 곳을 누르면 그 자리로 구간을 옮긴다
              if (e.target !== e.currentTarget) return;
              stop();
              const r = rangeRef.current;
              apply("move", timeAt(e.clientX), (r.end - r.start) / 2);
            }}>
            <div className="trim-sel" style={{ left: pct(range.start), width: pct(len) }}
              role="slider" tabIndex={0} aria-label="구간 위치" aria-valuemin={0} aria-valuemax={Math.round(duration)}
              aria-valuenow={Math.round(range.start)} aria-valuetext={`${fmtTime(range.start)}부터`}
              onPointerDown={onDown("move")} onKeyDown={onKey("move")} />
            <div className="trim-handle trim-handle-start" style={{ left: pct(range.start) }}
              role="slider" tabIndex={0} aria-label="시작" aria-valuemin={0} aria-valuemax={Math.round(duration)}
              aria-valuenow={Math.round(range.start)} aria-valuetext={fmtTime(range.start)}
              onPointerDown={onDown("start")} onKeyDown={onKey("start")}><span /></div>
            <div className="trim-handle trim-handle-end" style={{ left: pct(range.end) }}
              role="slider" tabIndex={0} aria-label="끝" aria-valuemin={0} aria-valuemax={Math.round(duration)}
              aria-valuenow={Math.round(range.end)} aria-valuetext={fmtTime(range.end)}
              onPointerDown={onDown("end")} onKeyDown={onKey("end")}><span /></div>
          </div>
          <div className="trim-scale"><span>0:00</span><span>{fmtTime(duration)}</span></div>
          <button type="button" className="btn btn-secondary btn-block" onClick={togglePlay}>
            {playing ? "■ 멈춤" : "▶ 고른 구간 재생"}
          </button>
          <p className="field-hint">저장할 때 시작 위치가 1~2초 앞당겨질 수 있습니다.</p>
        </div>
        <div className="sheet-foot trim-foot">
          <button type="button" className="btn btn-secondary btn-lg" onClick={() => onDone(null)}>취소</button>
          <button type="button" className="btn btn-primary btn-lg" onClick={() => { stop(); onDone({ start: range.start, end: range.end }); }}>
            이 구간 사용
          </button>
        </div>
      </div>
    </div>
  );
}

// 여러 개를 차례로 올린다. 401 이면 멈추고 { stopped: true }
async function uploadAll(caseId, items, startN, setUpload) {
  let failed = 0;
  let lastErr = "";
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const label = it.kind === "video" ? "영상 올리는 중" : "사진 올리는 중";
    setUpload({ label, current: i + 1, total: items.length, pct: null });
    try {
      await uploadMedia(caseId, it, startN + i + 1, it.kind === "video"
        ? (pct) => setUpload({ label, current: i + 1, total: items.length, pct })
        : null);
    } catch (err) {
      if (err.status === 401) return { failed: failed + items.length - i, lastErr: err.message, stopped: true };
      failed += 1;
      lastErr = err.message;
    }
  }
  return { failed, lastErr, stopped: false };
}

// ---- 주소 검색 (카카오 우편번호 서비스 — CRM 과 같은 것. 키가 필요 없다) ----
// 폰에서는 팝업이 새 탭으로 열려 불편하므로 화면 안 창에 끼워 넣는다(embed)

const POSTCODE_SRC = "https://t1.kakaocdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js";
let postcodeLoading = null;

function loadPostcode() {
  const ready = () => (window.kakao && window.kakao.Postcode) || (window.daum && window.daum.Postcode);
  if (ready()) return Promise.resolve(ready());
  if (!postcodeLoading) {
    postcodeLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = POSTCODE_SRC;
      s.async = true;
      const timer = setTimeout(() => reject(new Error("timeout")), 10000);
      s.onload = () => { clearTimeout(timer); const P = ready(); if (P) resolve(P); else reject(new Error("no postcode")); };
      s.onerror = () => { clearTimeout(timer); reject(new Error("load")); };
      document.head.appendChild(s);
    }).catch((e) => { postcodeLoading = null; throw e; }); // 실패하면 다음에 다시 시도
  }
  return postcodeLoading;
}

// 도로명/지번 + (동, 아파트 이름) — CRM 의 openKoreanAddressSearch 와 같은 규칙
function postcodeAddress(data) {
  const addr = data.userSelectedType === "R" ? data.roadAddress : data.jibunAddress;
  const extra = data.userSelectedType === "R"
    ? [/[동로가]$/.test(data.bname || "") ? data.bname : "", data.apartment === "Y" ? data.buildingName : ""].filter(Boolean).join(", ")
    : "";
  return extra ? `${addr} (${extra})` : addr;
}

function AddressSheet({ onPick, onClose }) {
  const boxRef = useRef(null);
  const holderRef = useRef(null);
  const [state, setState] = useState("loading"); // loading | ready | error
  useEffect(() => {
    const prev = document.activeElement;
    if (boxRef.current) boxRef.current.focus();
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("no-scroll");
    let live = true;
    loadPostcode().then((Postcode) => {
      if (!live || !holderRef.current) return;
      new Postcode({
        oncomplete: (data) => { if (live) onPick(postcodeAddress(data)); },
        width: "100%",
        height: "100%",
      }).embed(holderRef.current, { autoClose: false });
      setState("ready");
    }).catch(() => { if (live) setState("error"); });
    return () => {
      live = false;
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("no-scroll");
      if (prev && prev.focus) prev.focus();
    };
  }, [onPick, onClose]);
  return (
    <div className="sheet-wrap" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="sheet sheet-addr" role="dialog" aria-modal="true" aria-labelledby="addr-title" tabIndex={-1} ref={boxRef}>
        <div className="sheet-head">
          <h2 className="sheet-title" id="addr-title">주소 검색</h2>
          <button type="button" className="sheet-close" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        {state === "loading" && <p className="sheet-hint addr-msg"><Spinner /> 주소 검색을 여는 중…</p>}
        {state === "error" && (
          <div className="addr-msg">
            <Banner kind="warn">주소 검색을 열지 못했습니다. 창을 닫고 주소 칸에 직접 입력해 주세요.</Banner>
          </div>
        )}
        <div className="addr-holder" ref={holderRef} hidden={state === "error"} />
      </div>
    </div>
  );
}

// ---- 증상 고르기 (아래에서 올라오는 선택 창 — 폰. PC·태블릿은 가운데 창) ----

function SymptomSheet({ selected, onDone, onClose }) {
  const [picked, setPicked] = useState(selected);
  // 창 자체에 초점을 둔다 — 첫 항목에 두면 그 항목에 파란 네모(초점 테두리)가 떠서 골라 둔 것처럼 보인다
  const boxRef = useRef(null);
  useEffect(() => {
    const prev = document.activeElement;
    if (boxRef.current) boxRef.current.focus();
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("no-scroll");
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("no-scroll");
      if (prev && prev.focus) prev.focus();
    };
  }, [onClose]);
  const toggle = (s) => setPicked((p) => (p.includes(s) ? p.filter((x) => x !== s) : p.concat([s])));
  // 고른 순서가 아니라 목록 순서로 담는다
  const done = () => onDone(SYMPTOM_EXAMPLES.filter((s) => picked.includes(s)));
  return (
    <div className="sheet-wrap" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title" tabIndex={-1} ref={boxRef}>
        <div className="sheet-head">
          <h2 className="sheet-title" id="sheet-title">증상 선택</h2>
          <button type="button" className="sheet-close" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        <p className="sheet-hint">해당하는 증상을 모두 골라 주세요. 여러 개를 고를 수 있습니다.</p>
        <ul className="sheet-list">
          {SYMPTOM_EXAMPLES.map((s, i) => {
            const on = picked.includes(s);
            return (
              <li key={s}>
                <button type="button" className={"sheet-item" + (on ? " on" : "")}
                  aria-pressed={on} onClick={() => toggle(s)}>
                  <span className="sheet-check" aria-hidden="true">{on ? "✓" : ""}</span>
                  <span className="sheet-text">{s}</span>
                </button>
              </li>
            );
          })}
        </ul>
        <div className="sheet-foot">
          <button type="button" className="btn btn-primary btn-lg btn-block" onClick={done}>
            {picked.length ? `선택 완료 (${picked.length})` : "선택 완료"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- 화면 전환 (location.hash — 폰 뒤로 가기 동작) ----

function parseHash() {
  const h = (window.location.hash || "").replace(/^#/, "");
  if (h === "/new") return { name: "new", key: "new" };
  if (h === "/password") return { name: "password", key: "password" };
  const m = h.match(/^\/case\/(\d+)$/);
  if (m) return { name: "case", id: Number(m[1]), key: `case:${m[1]}` };
  return { name: "list", key: "list" };
}

function go(hash, replace) {
  if (replace) window.location.replace(hash);
  else window.location.hash = hash;
}

function useRoute() {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

// 다음 화면에 넘길 안내 문구 (접수 완료 → 상세 등)
let pendingFlash = null;
function setFlash(target, items) { pendingFlash = { target, items }; }
function takeFlash(target) {
  if (pendingFlash && pendingFlash.target === target) {
    const items = pendingFlash.items;
    pendingFlash = null;
    return items;
  }
  return [];
}

// ---- 공통 조각 ----

function Banner({ kind, children, action }) {
  return (
    <div className={`banner banner-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <span className="banner-mark" aria-hidden="true">{kind === "ok" ? "✓" : kind === "info" ? "i" : "!"}</span>
      <div className="banner-text">{children}</div>
      {action}
    </div>
  );
}

function Messages({ items }) {
  if (!items || !items.length) return null;
  return items.map((m, i) => <Banner key={i} kind={m.kind}>{m.text}</Banner>);
}

function Spinner() {
  return <span className="spin" aria-hidden="true" />;
}

function StatusBadge({ status, label }) {
  return (
    <span className={`badge badge-${status}`}>
      <span className="badge-mark" aria-hidden="true" />
      {label}
    </span>
  );
}

function Loading({ text }) {
  return (
    <div className="loading" role="status">
      <Spinner /> {text || "불러오는 중…"}
    </div>
  );
}

function Progress({ current, total, label }) {
  const pct = total ? Math.round((Math.max(current - 1, 0) / total) * 100) : 0;
  return (
    <div className="progress" role="status" aria-live="polite">
      <div className="progress-text">{label} {current}/{total}</div>
      <div className="progress-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

// 사진·영상 올리기 진행 — 영상은 그 파일의 보낸 % 를 막대에 반영한다
function UploadProgress({ up }) {
  const { current, total, label, pct } = up;
  const share = total ? 100 / total : 0;
  const width = Math.round(Math.max(current - 1, 0) * share + (pct != null ? (pct / 100) * share : 0));
  return (
    <div className="progress" role="status" aria-live="polite">
      <div className="progress-text">
        {label} {current}/{total}{pct != null ? ` · ${pct}%` : ""}
      </div>
      <div className="progress-bar" aria-hidden="true"><span style={{ width: `${width}%` }} /></div>
      {pct != null && <div className="progress-sub">영상은 크기에 따라 시간이 걸립니다. 화면을 닫지 말고 기다려 주세요.</div>}
    </div>
  );
}

// ---- 로그인 ----

function LoginScreen({ notice, onLoggedIn }) {
  const [loginId, setLoginId] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!loginId.trim() || !pw) { setError("아이디와 비밀번호를 입력해 주세요"); return; }
    setBusy(true);
    setError("");
    try {
      const r = await api("/login", { method: "POST", body: { login_id: loginId.trim(), password: pw } });
      onLoggedIn(r.token, r.account);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <section className="login">
      <div className="login-head">
        <h1>A/S 접수 로그인</h1>
        <p className="lead">KJC 디스플레이 제품의 고장·불량을 접수하고 처리 상황을 확인하실 수 있습니다.</p>
      </div>
      {notice && <Banner kind="info">{notice}</Banner>}
      {error && <Banner kind="error">{error}</Banner>}
      <form className="card form" onSubmit={submit} noValidate>
        <fieldset disabled={busy}>
          <div className="field">
            <label htmlFor="login-id">아이디</label>
            <input id="login-id" type="text" value={loginId} onChange={(e) => setLoginId(e.target.value)}
              autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={30} />
          </div>
          <div className="field">
            <label htmlFor="login-pw">비밀번호</label>
            <input id="login-pw" type="password" value={pw} onChange={(e) => setPw(e.target.value)}
              autoComplete="current-password" />
          </div>
          <button type="submit" className="btn btn-primary btn-block btn-lg">
            {busy ? <><Spinner /> 확인하는 중…</> : "로그인"}
          </button>
        </fieldset>
      </form>
      <p className="hint">계정은 KJC 담당자가 발급해 드립니다. 아이디·비밀번호를 모르시면 담당자에게 문의해 주세요.</p>
    </section>
  );
}

// ---- 비밀번호 변경 ----

function PasswordScreen({ forced, onChanged }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!cur) { setError("현재 비밀번호를 입력해 주세요"); return; }
    if (next.length < PW_MIN) { setError(`비밀번호는 ${PW_MIN}자 이상이어야 합니다`); return; }
    if (next !== again) { setError("새 비밀번호가 서로 다릅니다. 다시 확인해 주세요"); return; }
    setBusy(true);
    setError("");
    try {
      const r = await api("/password", { method: "POST", body: { current: cur, new: next } });
      onChanged(r.token);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <section>
      <h1 className="page-title">비밀번호 변경</h1>
      {forced && <Banner kind="info">처음 로그인하셨습니다. 안전을 위해 새 비밀번호로 바꾼 뒤 이용해 주세요.</Banner>}
      {error && <Banner kind="error">{error}</Banner>}
      <form className="card form" onSubmit={submit} noValidate>
        <fieldset disabled={busy}>
          <div className="field">
            <label htmlFor="pw-cur">현재 비밀번호</label>
            <input id="pw-cur" type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" />
          </div>
          <div className="field">
            <label htmlFor="pw-new">새 비밀번호</label>
            <input id="pw-new" type="password" value={next} onChange={(e) => setNext(e.target.value)}
              autoComplete="new-password" aria-describedby="pw-new-hint" />
            <p className="field-hint" id="pw-new-hint">{PW_MIN}자 이상</p>
          </div>
          <div className="field">
            <label htmlFor="pw-again">새 비밀번호 확인</label>
            <input id="pw-again" type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" />
          </div>
          <div className="actions">
            <button type="submit" className="btn btn-primary btn-lg">
              {busy ? <><Spinner /> 바꾸는 중…</> : "비밀번호 바꾸기"}
            </button>
            {!forced && <button type="button" className="btn btn-secondary btn-lg" onClick={() => go("#/")}>취소</button>}
          </div>
        </fieldset>
      </form>
    </section>
  );
}

// ---- 목록 ----

function CaseCard({ row }) {
  return (
    <li>
      <a className="case-card" href={`#/case/${row.id}`}>
        <div className="case-card-top">
          <span className="case-no">{row.as_no}</span>
          <StatusBadge status={row.status} label={row.status_label} />
        </div>
        <div className="case-card-meta">
          <span>{row.received_date}</span>
          {row.product_group_name && <span>{row.product_group_name}</span>}
          {row.photo_count > 0 && <span>사진·영상 {row.photo_count}개</span>}
        </div>
        <p className="case-card-symptom">{row.symptom_head || "증상 내용 없음"}</p>
      </a>
    </li>
  );
}

function ListScreen({ filter, setFilter }) {
  const [rows, setRows] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [st, setSt] = useState({ loading: true, more: false, error: "" });
  const [flash] = useState(() => takeFlash("list"));
  const reqId = useRef(0);
  const lastOffset = useRef(0);

  const load = useCallback(async (offset) => {
    const my = ++reqId.current;
    lastOffset.current = offset;
    setSt({ loading: offset === 0, more: offset > 0, error: "" });
    try {
      const r = await api(`/cases?filter=${filter}&offset=${offset}`);
      if (my !== reqId.current) return;
      setRows((prev) => {
        if (offset === 0) return r.rows;
        const seen = new Set(prev.map((x) => x.id));
        return prev.concat(r.rows.filter((x) => !seen.has(x.id)));
      });
      setHasMore(!!r.has_more);
      setSt({ loading: false, more: false, error: "" });
    } catch (e) {
      if (my !== reqId.current) return;
      setSt({ loading: false, more: false, error: e.message });
    }
  }, [filter]);

  useEffect(() => {
    setRows([]);
    setHasMore(false);
    load(0);
  }, [load]);

  const empty = !st.loading && !st.error && rows.length === 0;
  const newButton = (
    <button type="button" className="btn btn-primary btn-block btn-lg btn-new" onClick={() => go("#/new")}>
      <span aria-hidden="true">＋</span> A/S 접수
    </button>
  );

  return (
    <section>
      <Messages items={flash} />
      <div className="list-head">
        <h1 className="page-title">내 접수 내역</h1>
      </div>
      {!empty && newButton}
      <div className="filters" role="group" aria-label="상태로 보기">
        {FILTERS.map(([code, label]) => (
          <button key={code} type="button" className="filter" aria-pressed={filter === code}
            onClick={() => setFilter(code)}>{label}</button>
        ))}
      </div>
      {st.error && (
        <Banner kind="error" action={
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => load(lastOffset.current)}>다시 시도</button>
        }>{st.error}</Banner>
      )}
      {st.loading && (
        <>
          <Loading text="접수 내역을 불러오는 중…" />
          <ul className="case-list" aria-hidden="true">
            {[0, 1, 2].map((i) => <li key={i} className="case-card skeleton"><span /><span /><span /></li>)}
          </ul>
        </>
      )}
      {empty && (
        <div className="card empty">
          <p className="empty-title">접수 내역이 없습니다</p>
          <p className="empty-text">제품에 문제가 있으면 사진과 함께 접수해 주세요. 담당자가 확인 후 연락드립니다.</p>
          {newButton}
        </div>
      )}
      {!st.loading && rows.length > 0 && (
        <ul className="case-list">
          {rows.map((r) => <CaseCard key={r.id} row={r} />)}
        </ul>
      )}
      {!st.loading && hasMore && (
        <button type="button" className="btn btn-secondary btn-block" disabled={st.more} onClick={() => load(rows.length)}>
          {st.more ? <><Spinner /> 불러오는 중…</> : "더 보기"}
        </button>
      )}
    </section>
  );
}

// ---- 접수 · 수정 양식 ----

// method = 접수 구분(현장 / 입고 수리). 처음에는 비워 두고 고객이 반드시 고른다
const EMPTY_FIELDS = {
  reporter_name: "", reporter_phone: "", method: "", product_group: "", qty: "1", model_text: "",
  symptom: "", site_name: "", site_address: "", site_address_detail: "", site_phone: "",
};
const FIELD_KEYS = Object.keys(EMPTY_FIELDS);
const SITE_KEYS = ["site_name", "site_address", "site_address_detail", "site_phone"];
// 접수 구분을 고객이 알아보기 쉬운 말로 (공통코드 AS_KIND 의 code 기준. 모르는 code 는 서버가 준 이름 그대로)
const KIND_TEXT = {
  CS: { title: "현장 수리", desc: "설치된 곳으로 방문해 수리합니다" },
  AS: { title: "입고 수리", desc: "제품을 보내 주시면 수리 후 돌려드립니다" },
};
const kindTitle = (code, name) => (KIND_TEXT[code] ? KIND_TEXT[code].title : name || code || "");

// fields.symptom = 화면의 '기타 · 추가 증상 설명' 칸. 고른 예시(picks)는 따로 들고 있다가 보낼 때 합친다
function initialFields(mode, c) {
  const f = { ...EMPTY_FIELDS };
  if (mode === "edit") {
    for (const k of FIELD_KEYS) f[k] = c[k] == null ? "" : String(c[k]);
    const s = splitSymptom(f.symptom);
    f.symptom = s.extra;
    return { fields: f, picks: s.picks, restored: false };
  }
  const draft = readJson("sessionStorage", DRAFT_KEY);
  if (draft && typeof draft === "object") {
    for (const k of FIELD_KEYS) if (typeof draft[k] === "string") f[k] = draft[k];
    const picks = Array.isArray(draft._picks) ? SYMPTOM_EXAMPLES.filter((s) => draft._picks.includes(s)) : [];
    return { fields: f, picks, restored: true };
  }
  const rep = readJson("localStorage", REPORTER_KEY) || {};
  if (typeof rep.name === "string") f.reporter_name = rep.name;
  if (typeof rep.phone === "string") f.reporter_phone = rep.phone;
  return { fields: f, picks: [], restored: false };
}

const PHOTO_REQUIRED_MSG = "사진 또는 영상을 1개 이상 올려 주세요";

function validate(f, picks) {
  if (!f.reporter_name.trim()) return "접수자 이름을 입력해 주세요";
  if (!f.reporter_phone.trim()) return "접수자 연락처를 입력해 주세요";
  if (!f.method) return "접수 구분을 선택해 주세요";
  if (!f.product_group) return "제품군을 선택해 주세요";
  const q = f.qty.trim();
  if (!/^\d+$/.test(q) || Number(q) < 1 || Number(q) > QTY_MAX) return "수량을 확인해 주세요";
  if (!picks.length && !f.symptom.trim()) return "증상을 고르거나 입력해 주세요";
  return "";
}

function toPayload(f, picks) {
  const p = {};
  for (const k of FIELD_KEYS) p[k] = f[k].trim();
  p.qty = Number(p.qty);
  p.symptom = composeSymptom(picks, f.symptom);
  return p;
}

function CaseForm({ mode, initial, onSaved, onCancel, onConflict }) {
  const isNew = mode === "new";
  const [init] = useState(() => initialFields(mode, initial));
  const [fields, setFields] = useState(init.fields);
  const [restored, setRestored] = useState(init.restored);
  const [picks, setPicks] = useState(init.picks); // 고른 증상 예시
  const [sheetOpen, setSheetOpen] = useState(false);
  const [opts, setOpts] = useState({ loading: true, error: "", groups: [], kinds: [], reporters: [] });
  const [photos, setPhotos] = useState([]); // [{key, kind, blob, url, name}] — 접수 때만 (사진·영상)
  const [preparing, setPreparing] = useState(false);
  const [photoNotes, setPhotoNotes] = useState([]);
  const [photoError, setPhotoError] = useState(""); // 사진을 고르지 않고 접수하려 할 때
  const photoBox = useRef(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null); // {label, current, total, pct}
  const [error, setError] = useState("");
  const busyRef = useRef(false);
  const touched = useRef(false);
  const clientKey = useRef(isNew ? newClientKey() : null); // 화면을 열 때 한 번
  const urls = useRef(new Set());
  const alive = useRef(true);
  const fileRef = useRef(null);
  const photoSeq = useRef(0);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      urls.current.forEach((u) => URL.revokeObjectURL(u));
      urls.current.clear();
    };
  }, []);

  const loadOptions = useCallback(async () => {
    setOpts((o) => ({ ...o, loading: true, error: "" }));
    try {
      const r = await api("/options");
      setOpts({ loading: false, error: "", groups: r.product_groups || [], kinds: r.kinds || [], reporters: r.recent_reporters || [] });
    } catch (e) {
      setOpts((o) => ({ ...o, loading: false, error: e.message }));
    }
  }, []);
  useEffect(() => { loadOptions(); }, [loadOptions]);

  // 작성 중인 글자 칸은 sessionStorage 에 보관 (로그인이 풀려도 복원)
  useEffect(() => {
    if (isNew && touched.current) writeStore("sessionStorage", DRAFT_KEY, JSON.stringify({ ...fields, _picks: picks }));
  }, [fields, picks, isNew]);

  // 주소 검색: 고르면 주소 칸에 넣고 상세주소 칸으로 바로 넘어간다 (CRM 화면들과 같은 동작)
  const [addrOpen, setAddrOpen] = useState(false);
  const detailRef = useRef(null);
  const closeAddr = useCallback(() => setAddrOpen(false), []);
  const pickAddress = useCallback((addr) => {
    touched.current = true;
    setFields((f) => ({ ...f, site_address: addr.slice(0, 255) }));
    setAddrOpen(false);
    setTimeout(() => { if (detailRef.current) detailRef.current.focus(); }, 50);
  }, []);

  const choosePicks = (list) => {
    touched.current = true;
    setPicks(list);
    setSheetOpen(false);
  };
  const closeSheet = useCallback(() => setSheetOpen(false), []);

  const setField = (k, v) => {
    touched.current = true;
    setFields((f) => ({ ...f, [k]: v }));
  };

  const pickReporter = (r) => {
    touched.current = true;
    setFields((f) => ({ ...f, reporter_name: r.name || "", reporter_phone: r.phone || "" }));
  };

  const resetDraft = () => {
    writeStore("sessionStorage", DRAFT_KEY, null);
    touched.current = false;
    const rep = readJson("localStorage", REPORTER_KEY) || {};
    setFields({
      ...EMPTY_FIELDS,
      reporter_name: typeof rep.name === "string" ? rep.name : "",
      reporter_phone: typeof rep.phone === "string" ? rep.phone : "",
    });
    setPicks([]);
    setRestored(false);
  };

  const onPick = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    setPreparing(true);
    setPhotoNotes([]);
    const { items, notes } = await prepareFiles(files, PHOTO_MAX - countKind(photos, "photo"), VIDEO_MAX - countKind(photos, "video"));
    if (!alive.current) return;
    const added = items.map((it) => {
      const url = URL.createObjectURL(it.blob);
      urls.current.add(url);
      photoSeq.current += 1;
      return { ...it, key: photoSeq.current, url };
    });
    setPhotos((p) => p.concat(added));
    if (added.length) setPhotoError("");
    setPhotoNotes(notes);
    setPreparing(false);
  };

  // 1분이 넘는 영상: 미리 보기 칸을 누르면 구간을 다시 고른다(취소하면 그대로)
  const changeTrim = async (key) => {
    const it = photos.find((x) => x.key === key);
    if (!it || !(it.duration > VIDEO_MAX_SEC + 0.5)) return;
    const t = await askTrim(it.blob, it.duration, it.trim || { start: 0, end: VIDEO_MAX_SEC });
    if (t && alive.current) setPhotos((list) => list.map((x) => (x.key === key ? { ...x, trim: t } : x)));
  };

  const removePhoto = (key) => {
    setPhotos((p) => p.filter((x) => {
      if (x.key !== key) return true;
      URL.revokeObjectURL(x.url);
      urls.current.delete(x.url);
      return false;
    }));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (busyRef.current) return; // 연타 막기 (다시 그리기 전에 들어온 두 번째 누름까지)
    const msg = validate(fields, picks);
    if (msg) { setError(msg); return; }
    // 2026-11-19: 접수에는 사진이 1장 이상 있어야 한다 → 2026-11-24: 사진·영상 중 1개 이상
    // (수정 화면에는 걸지 않는다 — 사진·영상은 상세에서 따로 관리)
    if (isNew && photos.length === 0) {
      setError("");
      setPhotoError(PHOTO_REQUIRED_MSG);
      if (photoBox.current) photoBox.current.scrollIntoView({ block: "center" });
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError("");
    const payload = toPayload(fields, picks);
    let saved;
    try {
      if (isNew) saved = await api("/cases", { method: "POST", body: { ...payload, client_key: clientKey.current } });
      else saved = await api(`/cases/${initial.id}`, { method: "PUT", body: payload });
    } catch (err) {
      busyRef.current = false;
      setBusy(false);
      if (!isNew && err.status === 409 && onConflict) { onConflict(err.message); return; }
      setError(err.message);
      return;
    }
    if (!isNew) { onSaved(saved); return; }

    // 건이 만들어졌으므로 임시 보관을 바로 지운다 (사진 올리다 로그인이 풀려도 같은 내용을 다시 접수하지 않게)
    writeStore("sessionStorage", DRAFT_KEY, null);
    writeStore("localStorage", REPORTER_KEY, JSON.stringify({ name: payload.reporter_name, phone: payload.reporter_phone }));

    // 어떻게 끝나든(성공·일부 실패·로그인 풀림·화면 닫힘) 만든 건의 상세로 간다.
    // 로그인이 풀려도 주소는 #/new 로 남으므로 바꿔 두면 다시 로그인한 뒤 빈 접수 화면 대신 이 건이 열린다.
    // 사용자가 이미 다른 화면으로 옮겼으면 그대로 둔다.
    const finish = (failed) => {
      if (parseHash().name !== "new") return;
      const items = [{ kind: "ok", text: `접수되었습니다. 접수번호 ${saved.as_no}` }];
      if (failed) items.push({ kind: "warn", text: `사진·영상 ${failed}개를 올리지 못했습니다 — 상세 화면에서 다시 올려 주세요` });
      setFlash(`case:${saved.id}`, items);
      go(`#/case/${saved.id}`, true); // 뒤로 가기가 빈 접수 화면으로 돌아가지 않게
    };

    // 건이 만들어진 뒤 사진·영상을 하나씩 (401 이면 로그인 화면으로 바뀐다)
    const r = await uploadAll(saved.id, photos, 0, setProgress);
    finish(r.failed);
  };

  // 수정 화면에서 지금은 쓰지 않는 제품군이 걸려 있으면 그대로 보이게
  const groups = opts.groups.slice();
  if (fields.product_group && !groups.some((g) => g.code === fields.product_group)) {
    groups.push({ code: fields.product_group, name: (initial && initial.product_group_name) || fields.product_group });
  }
  const formBusy = busy || preparing;

  return (
    <form className="case-form" onSubmit={submit} noValidate>
      {restored && (
        <Banner kind="info" action={<button type="button" className="btn btn-secondary btn-sm" onClick={resetDraft}>새로 쓰기</button>}>
          작성 중이던 내용을 불러왔습니다.
        </Banner>
      )}
      {opts.error && (
        <Banner kind="error" action={<button type="button" className="btn btn-secondary btn-sm" onClick={loadOptions}>다시 시도</button>}>
          {opts.error}
        </Banner>
      )}
      <fieldset disabled={busy}>
        <div className="card form">
          <h2 className="section-title">접수자</h2>
          {opts.reporters.length > 0 && (
            <div className="chips" role="group" aria-label="최근 접수자">
              <span className="chips-label" aria-hidden="true">최근 접수자</span>
              {opts.reporters.map((r, i) => (
                <button key={i} type="button" className="chip" onClick={() => pickReporter(r)}
                  aria-label={`최근 접수자 ${r.name}${r.phone ? ` ${r.phone}` : ""} 채우기`}>
                  {r.name}{r.phone ? <span className="chip-sub"> {r.phone}</span> : null}
                </button>
              ))}
            </div>
          )}
          <div className="row-2">
            <div className="field">
              <label htmlFor="f-name">이름 <span className="req">(필수)</span></label>
              <input id="f-name" type="text" value={fields.reporter_name} maxLength={60} autoComplete="name"
                onChange={(e) => setField("reporter_name", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="f-phone">연락처 <span className="req">(필수)</span></label>
              <input id="f-phone" type="tel" inputMode="tel" value={fields.reporter_phone} maxLength={40} autoComplete="tel"
                placeholder="010-0000-0000" onChange={(e) => setField("reporter_phone", e.target.value)} />
            </div>
          </div>
        </div>

        {/* 2026-11-19: 접수 구분 — 처음에는 아무것도 고르지 않은 상태, 반드시 골라야 접수된다. 직원 화면의 '구분'으로 그대로 들어간다 */}
        <div className="card form">
          <h2 className="section-title" id="f-kind-title">접수 구분 <span className="req">(필수)</span></h2>
          <div className="kinds" role="radiogroup" aria-labelledby="f-kind-title">
            {opts.kinds.map((k) => (
              <label key={k.code} className={"kind" + (fields.method === k.code ? " on" : "")}>
                <input type="radio" name="f-kind" value={k.code} checked={fields.method === k.code}
                  onChange={() => setField("method", k.code)} />
                <span className="kind-body">
                  <span className="kind-title">{kindTitle(k.code, k.name)}</span>
                  {KIND_TEXT[k.code] && <span className="kind-desc">{KIND_TEXT[k.code].desc}</span>}
                </span>
              </label>
            ))}
            {opts.loading && opts.kinds.length === 0 && <p className="muted">불러오는 중…</p>}
          </div>
        </div>

        <div className="card form">
          <h2 className="section-title">제품·증상</h2>
          <div className="row-2">
            <div className="field">
              <label htmlFor="f-group">제품군 <span className="req">(필수)</span></label>
              <select id="f-group" value={fields.product_group} disabled={opts.loading && !groups.length}
                onChange={(e) => setField("product_group", e.target.value)}>
                <option value="">{opts.loading ? "불러오는 중…" : "선택해 주세요"}</option>
                {groups.map((g) => <option key={g.code} value={g.code}>{g.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="f-qty">수량 <span className="req">(필수)</span></label>
              <input id="f-qty" type="text" inputMode="numeric" pattern="[0-9]*" value={fields.qty} maxLength={4}
                onChange={(e) => setField("qty", e.target.value.replace(/[^0-9]/g, ""))} />
            </div>
          </div>
          <div className="field">
            <label htmlFor="f-model">모델명 / 시리얼</label>
            <input id="f-model" type="text" value={fields.model_text} maxLength={200} autoCapitalize="characters"
              onChange={(e) => setField("model_text", e.target.value)} aria-describedby="f-model-hint" />
            <p className="field-hint" id="f-model-hint">제품 뒷면 라벨에 적힌 내용을 알려 주시면 더 빨리 확인할 수 있습니다.</p>
          </div>
          {/* 2026-11-24: 증상은 예시에서 고르고(여러 개), 없거나 더 적을 내용은 아래 칸에. 둘 중 하나는 있어야 접수된다 */}
          <div className="field">
            <span className="field-label" id="f-symptom-label">증상 <span className="req">(필수)</span></span>
            <button type="button" className={"picker" + (picks.length ? " has" : "")} onClick={() => setSheetOpen(true)}
              aria-haspopup="dialog" aria-labelledby="f-symptom-label f-symptom-value">
              <span id="f-symptom-value" className="picker-value">
                {picks.length
                  ? <ul className="picker-list">{picks.map((p) => <li key={p}>{p}</li>)}</ul>
                  : <span className="picker-placeholder">자주 있는 증상에서 고르기</span>}
              </span>
              <span className="picker-mark" aria-hidden="true">{picks.length ? "변경" : ""}</span>
            </button>
          </div>
          <div className="field">
            <label htmlFor="f-symptom">기타 · 추가 증상 설명</label>
            <textarea id="f-symptom" rows={4} value={fields.symptom} maxLength={3600}
              placeholder="예) 어제 오후부터 그렇습니다. 위 목록에 없는 증상은 여기에 적어 주세요."
              onChange={(e) => setField("symptom", e.target.value)} />
          </div>
        </div>
        {sheetOpen && <SymptomSheet selected={picks} onDone={choosePicks} onClose={closeSheet} />}
        {addrOpen && <AddressSheet onPick={pickAddress} onClose={closeAddr} />}

        <div className="card form">
          {/* 2026-11-19: "(선택)" 글자를 빼고 처음부터 펼쳐 둔다 — 한 번 더 누르지 않게. 비워도 접수된다 */}
          <h2 className="section-title">설치 장소</h2>
          <div id="site-fields">
            <div className="field">
              <label htmlFor="f-site-name">매장명</label>
              <input id="f-site-name" type="text" value={fields.site_name} maxLength={120}
                onChange={(e) => setField("site_name", e.target.value)} />
            </div>
            <div className="field">
              {/* 2026-11-24: CRM 표준과 같은 '주소(카카오 검색) + 상세주소' 두 칸. 직원 화면에도 두 칸으로 보인다. 직접 입력도 된다 */}
              <label htmlFor="f-site-addr">주소</label>
              <div className="addr-row">
                <input id="f-site-addr" type="text" value={fields.site_address} maxLength={255} autoComplete="street-address"
                  placeholder="주소 검색을 누르세요" onChange={(e) => setField("site_address", e.target.value)} />
                <button type="button" className="btn btn-secondary addr-btn" onClick={() => setAddrOpen(true)}>주소 검색</button>
              </div>
            </div>
            <div className="field">
              <label htmlFor="f-site-addr-detail">상세주소</label>
              <input id="f-site-addr-detail" ref={detailRef} type="text" value={fields.site_address_detail} maxLength={200}
                placeholder="동·층·호수 등" onChange={(e) => setField("site_address_detail", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="f-site-phone">현장 연락처</label>
              <input id="f-site-phone" type="tel" inputMode="tel" value={fields.site_phone} maxLength={40}
                onChange={(e) => setField("site_phone", e.target.value)} />
            </div>
          </div>
        </div>

        {isNew && (
          <div className={"card form" + (photoError ? " card-invalid" : "")} ref={photoBox}>
            <h2 className="section-title">사진·영상 <span className="req">(필수)</span>{" "}
              <span className="opt">(사진 {countKind(photos, "photo")}/{PHOTO_MAX} · 영상 {countKind(photos, "video")}/{VIDEO_MAX})</span></h2>
            <p className="field-hint">고장 부위와 제품 라벨이 보이게 찍어 주세요. 사진 또는 영상을 1개 이상 올려야 접수됩니다.
              깜박임·소리처럼 움직임이 있는 증상은 영상으로 찍어 주시면 좋습니다. 영상은 1분까지이며, 길면 올릴 구간을 고를 수 있습니다.</p>
            <CaptureTip />
            {photoError && <Banner kind="error">{photoError}</Banner>}
            <Messages items={photoNotes} />
            {photos.length > 0 && (
              <ul className="thumbs">
                {photos.map((p, i) => (
                  <li key={p.key} className="thumb">
                    {p.kind === "video"
                      ? (
                        <button type="button" className="thumb-video" onClick={() => changeTrim(p.key)}
                          disabled={!(p.duration > VIDEO_MAX_SEC + 0.5)}
                          aria-label={p.duration > VIDEO_MAX_SEC + 0.5 ? `영상 ${i + 1} 구간 바꾸기` : `고른 영상 ${i + 1}`}>
                          <video src={`${p.url}#t=${p.trim ? p.trim.start + 0.1 : 0.1}`} muted playsInline preload="metadata" />
                          <span className="thumb-badge">
                            ▶ {p.trim && p.duration ? `${fmtTime(p.trim.start)}~${fmtTime(p.trim.end)}` : p.duration ? fmtTime(p.duration) : "영상"}
                          </span>
                          {p.duration > VIDEO_MAX_SEC + 0.5 && <span className="thumb-edit">구간 바꾸기</span>}
                        </button>
                      )
                      : <img src={p.url} alt={`고른 사진 ${i + 1}`} />}
                    <button type="button" className="thumb-remove" onClick={() => removePhoto(p.key)}
                      aria-label={`${p.kind === "video" ? "영상" : "사진"} ${i + 1} 빼기`}>
                      빼기
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <label htmlFor="f-photos" className="sr-only">사진·영상 파일 고르기</label>
            <input id="f-photos" ref={fileRef} type="file" accept="image/*,video/*" multiple className="sr-only" tabIndex={-1} onChange={onPick} />
            <button type="button" className="btn btn-photo btn-block"
              disabled={preparing || (countKind(photos, "photo") >= PHOTO_MAX && countKind(photos, "video") >= VIDEO_MAX)}
              onClick={() => fileRef.current && fileRef.current.click()}>
              {preparing ? <><Spinner /> 준비하는 중…</> : <><span className="btn-photo-mark" aria-hidden="true">＋</span> 사진·영상 찍기 / 고르기</>}
            </button>
          </div>
        )}

        {error && <Banner kind="error">{error}</Banner>}
        {progress && busy && <UploadProgress up={progress} />}
        <div className="actions">
          <button type="submit" className="btn btn-primary btn-lg" disabled={formBusy}>
            {busy
              ? <><Spinner /> {isNew ? "접수하는 중…" : "저장하는 중…"}</>
              : isNew ? "접수하기" : "수정 저장"}
          </button>
          <button type="button" className="btn btn-secondary btn-lg" onClick={onCancel}>취소</button>
        </div>
      </fieldset>
    </form>
  );
}

function NewScreen() {
  return (
    <section>
      <a className="back" href="#/">← 목록</a>
      <h1 className="page-title">A/S 접수</h1>
      <CaseForm mode="new" onCancel={() => go("#/")} />
    </section>
  );
}

// ---- 상세 ----

// 영상은 목록에서 미리 받지 않는다(크다) — 누르면 그때 받아 재생 창을 연다
function VideoThumb({ photoId, index, onOpen, onRemove, removing }) {
  const [st, setSt] = useState("idle"); // idle | loading | missing | error
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  const open = () => {
    if (st === "loading") return;
    setSt("loading");
    api(`/photos/${photoId}`, { blob: true }).then((b) => {
      if (!live.current) return;
      setSt("idle");
      onOpen(URL.createObjectURL(b), index, "video");
    }).catch((e) => {
      if (live.current) setSt(e.status === 404 ? "missing" : "error");
    });
  };
  return (
    <li className="thumb">
      <button type="button" className="thumb-open thumb-video-tile" onClick={open} aria-label={`영상 ${index + 1} 재생`}>
        {st === "loading" ? <Spinner /> : <span className="thumb-play" aria-hidden="true">▶</span>}
        <span className="thumb-video-text">
          {st === "missing" ? "영상 없음 — 새로 고침" : st === "error" ? "불러오지 못함 — 다시 누르기" : st === "loading" ? "불러오는 중…" : "영상 보기"}
        </span>
      </button>
      {onRemove && (
        <button type="button" className="thumb-remove" disabled={removing} onClick={() => onRemove(photoId)}
          aria-label={`영상 ${index + 1} 지우기`}>지우기</button>
      )}
    </li>
  );
}

function PhotoThumb({ photoId, index, onOpen, onRemove, removing }) {
  const [st, setSt] = useState({ state: "loading", url: "" });
  useEffect(() => {
    let live = true;
    let made = "";
    api(`/photos/${photoId}`, { blob: true }).then((b) => {
      if (!live) return;
      made = URL.createObjectURL(b);
      setSt({ state: "ok", url: made });
    }).catch((e) => {
      if (live) setSt({ state: e.status === 404 ? "missing" : "error", url: "" });
    });
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [photoId]);

  return (
    <li className="thumb">
      {st.state === "ok" ? (
        <button type="button" className="thumb-open" onClick={() => onOpen(st.url, index)}
          aria-label={`사진 ${index + 1} 크게 보기`}>
          <img src={st.url} alt="" />
        </button>
      ) : (
        <div className="thumb-empty">
          {st.state === "loading" ? <Spinner /> : st.state === "missing" ? "사진 없음" : "불러오지 못함"}
        </div>
      )}
      {onRemove && (
        <button type="button" className="thumb-remove" disabled={removing} onClick={() => onRemove(photoId)}
          aria-label={`사진 ${index + 1} 지우기`}>지우기</button>
      )}
    </li>
  );
}

function PhotoViewer({ url, index, total, kind, onClose }) {
  const closeRef = useRef(null);
  useEffect(() => {
    const prev = document.activeElement;
    if (closeRef.current) closeRef.current.focus();
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("no-scroll");
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("no-scroll");
      if (prev && prev.focus) prev.focus();
    };
  }, [onClose]);

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={`${kind === "video" ? "영상" : "사진"} ${index + 1} / ${total}`}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="viewer-bar">
        <span className="viewer-count">{index + 1} / {total}</span>
        <button ref={closeRef} type="button" className="btn viewer-close" onClick={onClose}>닫기</button>
      </div>
      {kind === "video"
        ? <video className="viewer-img viewer-video" src={url} controls autoPlay playsInline />
        : <img className="viewer-img" src={url} alt={`사진 ${index + 1}`} />}
    </div>
  );
}

function InfoRow({ label, value, multiline }) {
  const empty = value == null || String(value).trim() === "";
  return (
    <div className="info-row">
      <dt>{label}</dt>
      <dd className={`${multiline ? "multiline" : ""}${empty ? " none" : ""}`}>{empty ? "—" : String(value)}</dd>
    </div>
  );
}

function CaseScreen({ id }) {
  const [c, setC] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [msgs, setMsgs] = useState(() => takeFlash(`case:${id}`));
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(""); // cancel | photo | remove
  const [upload, setUpload] = useState(null); // {label, current, total}
  const [viewer, setViewer] = useState(null); // {url, index}
  const fileRef = useRef(null);
  const busyRef = useRef(false);

  const reload = useCallback(async (quiet) => {
    if (!quiet) setLoading(true);
    try {
      const r = await api(`/cases/${id}`);
      setC(r);
      setLoadError("");
    } catch (e) {
      if (quiet) setMsgs((m) => m.concat([{ kind: "error", text: e.message }]));
      else setLoadError(e.message);
    } finally {
      setLoading(false);
    }
  }, [id]);
  useEffect(() => { reload(false); }, [reload]);

  // 영상 창의 blob 주소는 창을 닫을 때 돌려준다 (사진 주소는 미리 보기 칸이 주인)
  const closeViewer = useCallback(() => setViewer((v) => {
    if (v && v.kind === "video") URL.revokeObjectURL(v.url);
    return null;
  }), []);

  const begin = (what) => {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(what);
    return true;
  };
  const end = () => { busyRef.current = false; setBusy(""); };

  const cancelCase = async () => {
    if (!window.confirm("이 접수를 취소할까요?\n취소한 접수는 되돌릴 수 없습니다.")) return;
    if (!begin("cancel")) return;
    try {
      const r = await api(`/cases/${id}/cancel`, { method: "POST" });
      setC(r);
      setMsgs([{ kind: "ok", text: "접수를 취소했습니다" }]);
    } catch (e) {
      setMsgs([{ kind: "error", text: e.message }]);
      if (e.status === 409) reload(true);
    }
    end();
  };

  const onPick = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length || !c) return;
    if (!begin("photo")) return;
    setMsgs([]);
    const have = c.photos || [];
    const startCount = have.length;
    setUpload({ label: "준비하는 중", current: 1, total: files.length, pct: null });
    const { items, notes } = await prepareFiles(files, PHOTO_MAX - countKind(have, "photo"), VIDEO_MAX - countKind(have, "video"));
    const r = await uploadAll(id, items, startCount, setUpload);
    if (r.stopped) return; // 로그인 화면으로 바뀐다
    const { failed, lastErr } = r;
    setUpload(null);
    const out = notes.slice();
    const ok = items.length - failed;
    if (ok > 0) out.unshift({ kind: "ok", text: `${ok}개를 올렸습니다` });
    if (failed) out.push({ kind: "warn", text: `${failed}개를 올리지 못했습니다 — ${lastErr}` });
    setMsgs(out);
    await reload(true);
    end();
  };

  const removePhoto = async (pid) => {
    // 2026-11-19: 접수에는 사진이 1장 이상 있어야 한다 → 마지막 한 장은 지우지 못한다 (다른 사진을 먼저 올리면 지울 수 있다)
    // 2026-11-24: 사진·영상을 합쳐 1개 이상
    if ((c.photos || []).length <= 1) {
      setMsgs([{ kind: "warn", text: "사진·영상은 1개 이상 있어야 합니다. 다른 사진이나 영상을 먼저 올린 뒤 지워 주세요" }]);
      return;
    }
    const target = (c.photos || []).find((p) => p.id === pid);
    const what = target && target.kind === "video" ? "영상" : "사진";
    if (!window.confirm(`이 ${what}을 지울까요?`)) return;
    if (!begin("remove")) return;
    try {
      await api(`/cases/${id}/photos/${pid}`, { method: "DELETE" });
      closeViewer();
      setMsgs([{ kind: "ok", text: `${what}을 지웠습니다` }]);
    } catch (e) {
      setMsgs([{ kind: "error", text: e.message }]);
    }
    await reload(true);
    end();
  };

  if (loading && !c) {
    return <section><a className="back" href="#/">← 목록</a><Loading text="접수 내용을 불러오는 중…" /></section>;
  }
  if (!c) {
    return (
      <section>
        <a className="back" href="#/">← 목록</a>
        <Banner kind="error" action={<button type="button" className="btn btn-secondary btn-sm" onClick={() => reload(false)}>다시 시도</button>}>
          {loadError || NET_MSG}
        </Banner>
      </section>
    );
  }

  const photos = c.photos || [];
  const site = SITE_KEYS.some((k) => c[k]);
  const anyBusy = !!busy;

  return (
    <section>
      <a className="back" href="#/">← 목록</a>
      <Messages items={msgs} />

      <div className="card case-head">
        <div className="case-head-top">
          <h1 className="case-head-no">{c.as_no}</h1>
          <StatusBadge status={c.status} label={c.status_label} />
        </div>
        <p className="case-head-date">접수일 {c.received_date}</p>
      </div>

      {c.customer_notice && (
        <div className="notice">
          <h2 className="notice-title">KJC 안내</h2>
          <p className="multiline">{c.customer_notice}</p>
        </div>
      )}

      {editing ? (
        <>
          <h2 className="page-subtitle">접수 내용 수정</h2>
          <CaseForm mode="edit" initial={c}
            onSaved={(u) => { setC(u); setEditing(false); setMsgs([{ kind: "ok", text: "수정했습니다" }]); }}
            onCancel={() => setEditing(false)}
            onConflict={(m) => { setEditing(false); setMsgs([{ kind: "error", text: m }]); reload(true); }} />
        </>
      ) : (
        <div className="card">
          <h2 className="section-title">접수 내용</h2>
          <dl className="info">
            <InfoRow label="접수자" value={c.reporter_name} />
            <InfoRow label="연락처" value={c.reporter_phone} />
            <InfoRow label="접수 구분" value={c.method ? kindTitle(c.method, c.method_name) : ""} />
            <InfoRow label="제품군" value={c.product_group_name} />
            <InfoRow label="모델명 / 시리얼" value={c.model_text} />
            <InfoRow label="수량" value={c.qty} />
            <InfoRow label="증상" value={c.symptom} multiline />
            {site && <InfoRow label="매장명" value={c.site_name} />}
            {site && <InfoRow label="주소" value={c.site_address} />}
            {site && <InfoRow label="상세주소" value={c.site_address_detail} />}
            {site && <InfoRow label="현장 연락처" value={c.site_phone} />}
          </dl>
        </div>
      )}

      <div className="card">
        <h2 className="section-title">사진·영상{" "}
          <span className="opt">(사진 {countKind(photos, "photo")}장 · 영상 {countKind(photos, "video")}개)</span></h2>
        {photos.length === 0 && <p className="muted">올린 사진·영상이 없습니다.</p>}
        {photos.length > 0 && (
          <ul className="thumbs">
            {photos.map((p, i) => {
              const Thumb = p.kind === "video" ? VideoThumb : PhotoThumb;
              return (
                <Thumb key={p.id} photoId={p.id} index={i}
                  onOpen={(url, index, kind) => setViewer({ url, index, kind: kind || "photo" })}
                  onRemove={editing && c.can_edit ? removePhoto : null} removing={anyBusy} />
              );
            })}
          </ul>
        )}
        {upload && <UploadProgress up={upload} />}
        {c.can_add_photo && !editing && (
          <>
            <label htmlFor="d-photos" className="sr-only">사진·영상 파일 고르기</label>
            <input id="d-photos" ref={fileRef} type="file" accept="image/*,video/*" multiple className="sr-only" tabIndex={-1} onChange={onPick} />
            <button type="button" className="btn btn-secondary btn-block"
              disabled={anyBusy || (countKind(photos, "photo") >= PHOTO_MAX && countKind(photos, "video") >= VIDEO_MAX)}
              onClick={() => fileRef.current && fileRef.current.click()}>
              {busy === "photo" ? <><Spinner /> 올리는 중…</> : "사진·영상 추가"}
            </button>
            <CaptureTip />
          </>
        )}
      </div>

      {!editing && c.can_edit && (
        <div className="actions">
          <button type="button" className="btn btn-primary btn-lg" disabled={anyBusy} onClick={() => { setMsgs([]); setEditing(true); }}>수정</button>
          <button type="button" className="btn btn-danger btn-lg" disabled={anyBusy} onClick={cancelCase}>
            {busy === "cancel" ? <><Spinner /> 취소하는 중…</> : "접수 취소"}
          </button>
        </div>
      )}
      {!c.can_edit && c.status === "progress" && (
        <p className="hint">담당자가 확인한 접수는 직접 수정할 수 없습니다. 변경할 내용은 담당자에게 문의해 주세요.</p>
      )}

      {viewer && <PhotoViewer url={viewer.url} index={viewer.index} total={photos.length} kind={viewer.kind} onClose={closeViewer} />}
    </section>
  );
}

// ---- 전체 틀 ----

function Shell({ company, loggedIn, onLogout, children, mainRef }) {
  return (
    <div className="app">
      <header className="topbar">
        <div className="wrap topbar-in">
          <div className="brand">
            <span className="brand-name"><span className="brand-kjc">KJC</span> A/S 접수</span>
            {company && <span className="brand-company">{company}</span>}
          </div>
          {loggedIn && (
            <nav className="top-actions" aria-label="계정">
              <a className="top-link" href="#/password">비밀번호</a>
              <button type="button" className="top-link" onClick={onLogout}>로그아웃</button>
            </nav>
          )}
        </div>
      </header>
      <main id="main" className="wrap" tabIndex={-1} ref={mainRef}>{children}</main>
    </div>
  );
}

function App() {
  const [token, setToken] = useState(() => readStore("localStorage", TOKEN_KEY));
  const [account, setAccount] = useState(null);
  const [mustChange, setMustChange] = useState(false);
  const [boot, setBoot] = useState({ loading: false, error: "" });
  const [notice, setNotice] = useState("");
  const [listFilter, setListFilter] = useState("all");
  const route = useRoute();
  const mainRef = useRef(null);
  const firstRoute = useRef(true);

  const signOut = useCallback((msg) => {
    writeStore("localStorage", TOKEN_KEY, null);
    setToken(null);
    setAccount(null);
    setMustChange(false);
    setNotice(msg || "");
  }, []);

  useEffect(() => {
    authEvents.onLost = (detail) => {
      const draft = readStore("sessionStorage", DRAFT_KEY);
      const base = detail || "다시 로그인해 주세요";
      signOut(draft ? `${base} — 작성 중이던 접수 내용은 로그인 후 이어서 쓰실 수 있습니다` : base);
    };
    authEvents.onMustChange = () => setMustChange(true);
    return () => { authEvents.onLost = null; authEvents.onMustChange = null; };
  }, [signOut]);

  const loadMe = useCallback(async () => {
    setBoot({ loading: true, error: "" });
    try {
      const me = await api("/me");
      setAccount(me);
      setMustChange(!!me.must_change_pw);
      setBoot({ loading: false, error: "" });
    } catch (e) {
      setBoot({ loading: false, error: e.status === 401 ? "" : e.message });
    }
  }, []);

  useEffect(() => {
    if (token && !account) loadMe();
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  // 화면이 바뀌면 맨 위로, 화면 읽기 프로그램은 본문부터
  useEffect(() => {
    if (firstRoute.current) { firstRoute.current = false; return; }
    window.scrollTo(0, 0);
    if (mainRef.current) mainRef.current.focus({ preventScroll: true });
  }, [route.key]);

  const onLoggedIn = (tk, acc) => {
    writeStore("localStorage", TOKEN_KEY, tk);
    setNotice("");
    setAccount(acc);
    setMustChange(!!(acc && acc.must_change_pw));
    setBoot({ loading: false, error: "" });
    setToken(tk);
  };

  const onPasswordChanged = (tk) => {
    writeStore("localStorage", TOKEN_KEY, tk);
    setToken(tk);
    setAccount((a) => (a ? { ...a, must_change_pw: false } : a));
    setMustChange(false);
    setFlash("list", [{ kind: "ok", text: "비밀번호를 변경했습니다" }]);
    go("#/", true); // 강제 변경이었다면 비밀번호 화면 대신 목록이 새로 그려지며 안내를 받는다
  };

  const logout = () => {
    writeStore("sessionStorage", DRAFT_KEY, null); // 직접 로그아웃하면 작성 중이던 내용도 지운다 (로그인이 풀린 경우는 남김)
    signOut("로그아웃했습니다");
  };

  if (!token) {
    return (
      <Shell mainRef={mainRef}>
        <LoginScreen notice={notice} onLoggedIn={onLoggedIn} />
      </Shell>
    );
  }

  if (!account) {
    return (
      <Shell loggedIn onLogout={logout} mainRef={mainRef}>
        {boot.error ? (
          <Banner kind="error" action={<button type="button" className="btn btn-secondary btn-sm" onClick={loadMe}>다시 시도</button>}>
            {boot.error}
          </Banner>
        ) : <Loading />}
      </Shell>
    );
  }

  let screen;
  if (mustChange) screen = <PasswordScreen forced onChanged={onPasswordChanged} />;
  else if (route.name === "password") screen = <PasswordScreen onChanged={onPasswordChanged} />;
  else if (route.name === "new") screen = <NewScreen />;
  else if (route.name === "case") screen = <CaseScreen key={route.id} id={route.id} />;
  else screen = <ListScreen filter={listFilter} setFilter={setListFilter} />;

  return (
    <Shell company={account.company_name} loggedIn onLogout={logout} mainRef={mainRef}>
      {screen}
    </Shell>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<><App /><TrimHost /></>);
