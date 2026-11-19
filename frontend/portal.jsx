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
const PHOTO_LONG_SIDE = 1600;
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

function uploadPhoto(caseId, blob, n) {
  const form = new FormData();
  form.append("file", blob, `photo-${n}.jpg`);
  return api(`/cases/${caseId}/photos`, { method: "POST", form });
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

// 고른 파일들을 줄인다: 10장 넘는 몫은 받지 않고, 줄이지 못한 파일은 이름을 모아 알린다
async function shrinkFiles(files, room) {
  const take = files.slice(0, Math.max(room, 0));
  const over = files.length - take.length;
  const blobs = [];
  const bad = [];
  for (const f of take) {
    try { blobs.push(await shrinkImage(f)); } catch (e) { bad.push(f.name || "사진"); }
  }
  const notes = [];
  if (bad.length) notes.push({ kind: "warn", text: `${BAD_PHOTO_MSG} — ${bad.join(", ")}` });
  if (over > 0) notes.push({ kind: "warn", text: `사진은 최대 ${PHOTO_MAX}장까지 올릴 수 있습니다. ${over}장은 넣지 않았습니다` });
  return { blobs, notes };
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
          {row.photo_count > 0 && <span>사진 {row.photo_count}장</span>}
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
  symptom: "", site_name: "", site_address: "", site_phone: "",
};
const FIELD_KEYS = Object.keys(EMPTY_FIELDS);
const SITE_KEYS = ["site_name", "site_address", "site_phone"];
// 접수 구분을 고객이 알아보기 쉬운 말로 (공통코드 AS_KIND 의 code 기준. 모르는 code 는 서버가 준 이름 그대로)
const KIND_TEXT = {
  CS: { title: "현장 수리", desc: "설치된 곳으로 방문해 수리합니다" },
  AS: { title: "입고 수리", desc: "제품을 보내 주시면 수리 후 돌려드립니다" },
};
const kindTitle = (code, name) => (KIND_TEXT[code] ? KIND_TEXT[code].title : name || code || "");

function initialFields(mode, c) {
  const f = { ...EMPTY_FIELDS };
  if (mode === "edit") {
    for (const k of FIELD_KEYS) f[k] = c[k] == null ? "" : String(c[k]);
    return { fields: f, restored: false };
  }
  const draft = readJson("sessionStorage", DRAFT_KEY);
  if (draft && typeof draft === "object") {
    for (const k of FIELD_KEYS) if (typeof draft[k] === "string") f[k] = draft[k];
    return { fields: f, restored: true };
  }
  const rep = readJson("localStorage", REPORTER_KEY) || {};
  if (typeof rep.name === "string") f.reporter_name = rep.name;
  if (typeof rep.phone === "string") f.reporter_phone = rep.phone;
  return { fields: f, restored: false };
}

const PHOTO_REQUIRED_MSG = "사진을 1장 이상 올려 주세요";

function validate(f) {
  if (!f.reporter_name.trim()) return "접수자 이름을 입력해 주세요";
  if (!f.reporter_phone.trim()) return "접수자 연락처를 입력해 주세요";
  if (!f.method) return "접수 구분을 선택해 주세요";
  if (!f.product_group) return "제품군을 선택해 주세요";
  const q = f.qty.trim();
  if (!/^\d+$/.test(q) || Number(q) < 1 || Number(q) > QTY_MAX) return "수량을 확인해 주세요";
  if (!f.symptom.trim()) return "증상을 입력해 주세요";
  return "";
}

function toPayload(f) {
  const p = {};
  for (const k of FIELD_KEYS) p[k] = f[k].trim();
  p.qty = Number(p.qty);
  return p;
}

function CaseForm({ mode, initial, onSaved, onCancel, onConflict }) {
  const isNew = mode === "new";
  const [init] = useState(() => initialFields(mode, initial));
  const [fields, setFields] = useState(init.fields);
  const [restored, setRestored] = useState(init.restored);
  const [opts, setOpts] = useState({ loading: true, error: "", groups: [], kinds: [], reporters: [] });
  const [photos, setPhotos] = useState([]); // [{key, blob, url}] — 접수 때만
  const [preparing, setPreparing] = useState(false);
  const [photoNotes, setPhotoNotes] = useState([]);
  const [photoError, setPhotoError] = useState(""); // 사진을 고르지 않고 접수하려 할 때
  const photoBox = useRef(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null); // {current, total}
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
    if (isNew && touched.current) writeStore("sessionStorage", DRAFT_KEY, JSON.stringify(fields));
  }, [fields, isNew]);

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
    setRestored(false);
  };

  const onPick = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    setPreparing(true);
    setPhotoNotes([]);
    const { blobs, notes } = await shrinkFiles(files, PHOTO_MAX - photos.length);
    if (!alive.current) return;
    const added = blobs.map((blob) => {
      const url = URL.createObjectURL(blob);
      urls.current.add(url);
      photoSeq.current += 1;
      return { key: photoSeq.current, blob, url };
    });
    setPhotos((p) => p.concat(added));
    if (added.length) setPhotoError("");
    setPhotoNotes(notes);
    setPreparing(false);
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
    const msg = validate(fields);
    if (msg) { setError(msg); return; }
    // 2026-11-19: 접수에는 사진이 1장 이상 있어야 한다 (수정 화면에는 걸지 않는다 — 사진은 상세에서 따로 관리)
    if (isNew && photos.length === 0) {
      setError("");
      setPhotoError(PHOTO_REQUIRED_MSG);
      if (photoBox.current) photoBox.current.scrollIntoView({ block: "center" });
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError("");
    const payload = toPayload(fields);
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
      if (failed) items.push({ kind: "warn", text: `사진 ${failed}장을 올리지 못했습니다 — 상세 화면에서 다시 올려 주세요` });
      setFlash(`case:${saved.id}`, items);
      go(`#/case/${saved.id}`, true); // 뒤로 가기가 빈 접수 화면으로 돌아가지 않게
    };

    // 건이 만들어진 뒤 사진을 한 장씩
    let failed = 0;
    for (let i = 0; i < photos.length; i++) {
      setProgress({ current: i + 1, total: photos.length });
      try {
        await uploadPhoto(saved.id, photos[i].blob, i + 1);
      } catch (err) {
        if (err.status === 401) { finish(failed + photos.length - i); return; } // 로그인 화면으로 바뀐다
        failed += 1;
      }
    }
    finish(failed);
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
          <div className="field">
            <label htmlFor="f-symptom">증상 <span className="req">(필수)</span></label>
            <textarea id="f-symptom" rows={5} value={fields.symptom} maxLength={4000}
              placeholder="예) 전원은 들어오는데 화면이 나오지 않습니다. 어제 오후부터 그렇습니다."
              onChange={(e) => setField("symptom", e.target.value)} />
          </div>
        </div>

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
              <label htmlFor="f-site-addr">주소</label>
              <input id="f-site-addr" type="text" value={fields.site_address} maxLength={255} autoComplete="street-address"
                onChange={(e) => setField("site_address", e.target.value)} />
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
            <h2 className="section-title">사진 <span className="req">(필수)</span> <span className="opt">({photos.length}/{PHOTO_MAX})</span></h2>
            <p className="field-hint">고장 부위와 제품 라벨이 보이게 찍어 주세요. 1장 이상 올려야 접수됩니다.</p>
            {photoError && <Banner kind="error">{photoError}</Banner>}
            <Messages items={photoNotes} />
            {photos.length > 0 && (
              <ul className="thumbs">
                {photos.map((p, i) => (
                  <li key={p.key} className="thumb">
                    <img src={p.url} alt={`고른 사진 ${i + 1}`} />
                    <button type="button" className="thumb-remove" onClick={() => removePhoto(p.key)} aria-label={`사진 ${i + 1} 빼기`}>
                      빼기
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <label htmlFor="f-photos" className="sr-only">사진 파일 고르기</label>
            <input id="f-photos" ref={fileRef} type="file" accept="image/*" multiple className="sr-only" tabIndex={-1} onChange={onPick} />
            <button type="button" className="btn btn-photo btn-block" disabled={preparing || photos.length >= PHOTO_MAX}
              onClick={() => fileRef.current && fileRef.current.click()}>
              {preparing ? <><Spinner /> 사진 준비하는 중…</> : photos.length >= PHOTO_MAX ? `사진은 ${PHOTO_MAX}장까지입니다` : <><span className="btn-photo-mark" aria-hidden="true">＋</span> 사진 찍기 / 고르기</>}
            </button>
          </div>
        )}

        {error && <Banner kind="error">{error}</Banner>}
        {progress && busy && <Progress current={progress.current} total={progress.total} label="사진 올리는 중" />}
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

function PhotoViewer({ url, index, total, onClose }) {
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
    <div className="viewer" role="dialog" aria-modal="true" aria-label={`사진 ${index + 1} / ${total}`}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="viewer-bar">
        <span className="viewer-count">{index + 1} / {total}</span>
        <button ref={closeRef} type="button" className="btn viewer-close" onClick={onClose}>닫기</button>
      </div>
      <img className="viewer-img" src={url} alt={`사진 ${index + 1}`} />
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

  const closeViewer = useCallback(() => setViewer(null), []);

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
    const startCount = c.photos.length;
    setUpload({ label: "사진 준비하는 중", current: 1, total: Math.min(files.length, PHOTO_MAX - startCount) });
    const { blobs, notes } = await shrinkFiles(files, PHOTO_MAX - startCount);
    let failed = 0;
    let lastErr = "";
    for (let i = 0; i < blobs.length; i++) {
      setUpload({ label: "사진 올리는 중", current: i + 1, total: blobs.length });
      try {
        await uploadPhoto(id, blobs[i], startCount + i + 1);
      } catch (err) {
        if (err.status === 401) return; // 로그인 화면으로 바뀐다
        failed += 1;
        lastErr = err.message;
      }
    }
    setUpload(null);
    const out = notes.slice();
    const ok = blobs.length - failed;
    if (ok > 0) out.unshift({ kind: "ok", text: `사진 ${ok}장을 올렸습니다` });
    if (failed) out.push({ kind: "warn", text: `사진 ${failed}장을 올리지 못했습니다 — ${lastErr}` });
    setMsgs(out);
    await reload(true);
    end();
  };

  const removePhoto = async (pid) => {
    // 2026-11-19: 접수에는 사진이 1장 이상 있어야 한다 → 마지막 한 장은 지우지 못한다 (다른 사진을 먼저 올리면 지울 수 있다)
    if ((c.photos || []).length <= 1) {
      setMsgs([{ kind: "warn", text: "사진은 1장 이상 있어야 합니다. 다른 사진을 먼저 올린 뒤 지워 주세요" }]);
      return;
    }
    if (!window.confirm("이 사진을 지울까요?")) return;
    if (!begin("remove")) return;
    try {
      await api(`/cases/${id}/photos/${pid}`, { method: "DELETE" });
      setViewer(null);
      setMsgs([{ kind: "ok", text: "사진을 지웠습니다" }]);
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
            {site && <InfoRow label="현장 연락처" value={c.site_phone} />}
          </dl>
        </div>
      )}

      <div className="card">
        <h2 className="section-title">사진 <span className="opt">({photos.length}장)</span></h2>
        {photos.length === 0 && <p className="muted">올린 사진이 없습니다.</p>}
        {photos.length > 0 && (
          <ul className="thumbs">
            {photos.map((p, i) => (
              <PhotoThumb key={p.id} photoId={p.id} index={i}
                onOpen={(url, index) => setViewer({ url, index })}
                onRemove={editing && c.can_edit ? removePhoto : null} removing={anyBusy} />
            ))}
          </ul>
        )}
        {upload && <Progress current={upload.current} total={upload.total} label={upload.label} />}
        {c.can_add_photo && !editing && (
          <>
            <label htmlFor="d-photos" className="sr-only">사진 파일 고르기</label>
            <input id="d-photos" ref={fileRef} type="file" accept="image/*" multiple className="sr-only" tabIndex={-1} onChange={onPick} />
            <button type="button" className="btn btn-secondary btn-block" disabled={anyBusy || photos.length >= PHOTO_MAX}
              onClick={() => fileRef.current && fileRef.current.click()}>
              {busy === "photo" ? <><Spinner /> 사진 올리는 중…</> : photos.length >= PHOTO_MAX ? `사진은 ${PHOTO_MAX}장까지입니다` : "사진 추가"}
            </button>
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

      {viewer && <PhotoViewer url={viewer.url} index={viewer.index} total={photos.length} onClose={closeViewer} />}
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

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
