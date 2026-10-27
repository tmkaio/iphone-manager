"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const enc = encodeURIComponent;
// Locale for dates and numbers, following the UI language (LANG comes from i18n.js)
const LOCALE = LANG === "pt" ? "pt-BR" : "en-US";

const state = {
  devices: [],
  udid: null,
  view: "overview",
  items: [],        // full photo list for current device
  filtered: [],
  filter: "all",
  selected: new Set(),
  lastClicked: null,
  rendered: 0,
  photosLoadedFor: null,
  jobs: new Map(),  // id -> job (photo export + ipsw downloads)
  updateLogLen: 0,
};

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}
const MONTH_FMT = new Intl.DateTimeFormat(LOCALE, { month: "long", year: "numeric" });
const itemTime = (it) => (it.btime || it.mtime) * 1000;
const monthLabel = (k) => { const [y, m] = k.split("-").map(Number); return MONTH_FMT.format(new Date(y, m - 1, 1)); };
const current = () => state.devices.find((d) => d.udid === state.udid);
const itemBytes = (it) => it.size + (it.live_size || 0);
function fmtEta(sec) {
  if (!isFinite(sec) || sec <= 0) return "";
  if (sec < 60) return t("{n} s left", { n: Math.ceil(sec) });
  if (sec < 3600) return t("{n} min left", { n: Math.ceil(sec / 60) });
  return t("{h} h {m} min left", { h: Math.floor(sec / 3600), m: Math.ceil((sec % 3600) / 60) });
}

// App-own confirmation dialog. window.confirm() is not used: once the user ticks "don't let this page
// create more dialogs", Edge/Chrome make it return false silently and the button seems dead.
function ask(title, body, okLabel = t("Continue"), danger = false) {
  const dlg = $("#dlg-ask");
  $("#ask-title").textContent = title;
  $("#ask-body").textContent = body;
  const ok = $("#ask-ok");
  ok.textContent = okLabel;
  ok.className = "btn " + (danger ? "danger-solid" : "primary");
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => { dlg.onclose = () => resolve(dlg.returnValue === "ok"); });
}

function toast(msg, isError = true) {
  const el = document.createElement("div");
  el.className = "tjob";
  el.innerHTML = `<div class="t"><span></span></div>`;
  el.querySelector("span").textContent = msg;
  if (isError) el.style.borderColor = "var(--danger)";
  $("#tray").hidden = false;
  $("#tray").prepend(el);
  setTimeout(() => { el.remove(); if (!$("#tray").children.length) $("#tray").hidden = true; }, 6000);
}

/* ---------------- devices ---------------- */

async function refreshDevices(wifi = false) {
  const btn = $("#btn-refresh");
  if (wifi) btn.classList.add("spin");
  try {
    state.devices = await api(`/api/devices${wifi ? "?wifi=true" : ""}`);
    $("#device-hint").innerHTML = t("Connect the iPhone via USB and tap <b>Trust</b>.");
  } catch (e) {
    state.devices = [];
    $("#device-hint").textContent = e.message;
  } finally {
    btn.classList.remove("spin");
  }
  // devices in Recovery / DFU / Restore don't show up in usbmux: MobileDevice.dll reports them
  try {
    const m = await api("/api/modes");
    for (const md of m.devices) {
      if (md.state !== "normal") state.devices.push({ ...md, udid: `ecid:${md.ecid}`, mode: md.state, connection: "USB" });
    }
  } catch {}
  renderDevices();
}

const MODE_LABEL = { recovery: "Recovery", dfu: "DFU", restore: t("Restore"), unknown: t("Unknown") };
const USB_ICON = '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 2v14m0 0-3-3m3 3 3-3M7 20h10" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>';
const WIFI_ICON = '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/><circle cx="12" cy="19.5" r="1.3" fill="currentColor"/></svg>';

function renderDevices() {
  let followed = false;
  if (state.udid && !current() && state.ecid) {
    // same phone in another boot state (normal <-> recovery/dfu): keep it selected
    const same = state.devices.find((d) => !d.error && d.ecid != null && String(d.ecid) === state.ecid);
    if (same) {
      state.udid = same.udid;
      state.items = []; state.filtered = []; state.selected.clear(); state.photosLoadedFor = null;
      followed = true;
    }
  }

  const ul = $("#device-list");
  ul.innerHTML = "";
  for (const d of state.devices) {
    const li = document.createElement("li");
    li.className = d.udid === state.udid ? "active" : "";
    li.innerHTML = `${d.connection === "USB" ? USB_ICON : WIFI_ICON}<div><div class="dname"></div><div class="meta muted"></div><div class="err"></div></div>`;
    if (d.mode) {
      li.querySelector(".dname").textContent = d.product_type || "iPhone";
      li.querySelector(".meta").textContent = "USB";
      li.querySelector(".meta").insertAdjacentHTML("afterbegin", `<span class="mode-tag"></span> `);
      li.querySelector(".mode-tag").textContent = MODE_LABEL[d.mode] || d.mode;
    } else {
      li.querySelector(".dname").textContent = d.name || d.udid.slice(0, 12) + "…";
      if (d.error) li.querySelector(".err").textContent = d.error;
      else li.querySelector(".meta").textContent = `iOS ${d.ios} · ${d.connection === "USB" ? "USB" : "Wi-Fi"}`;
    }
    li.onclick = () => selectDevice(d.udid);
    ul.appendChild(li);
  }
  $("#device-hint").hidden = state.devices.length > 0;
  renderHero();

  if (state.udid && !current()) {  // device disconnected
    state.udid = null;
    showView();
  } else if (!state.udid) {
    const first = state.devices.find((d) => !d.error);
    if (first) selectDevice(first.udid);
  } else if (followed) {
    showView();
  } else if (current().mode) {
    renderMode();
  } else if (state.view === "overview") {
    renderOverview();
  }
}

function selectDevice(udid) {
  const d = state.devices.find((x) => x.udid === udid);
  if (!d || d.error) { renderDevices(); return; }
  if (state.udid !== udid) {
    state.udid = udid;
    state.ecid = d.ecid != null ? String(d.ecid) : null;
    state.items = []; state.filtered = []; state.selected.clear(); state.photosLoadedFor = null;
  }
  renderDevices();
  showView();
}

/* ---------------- views ---------------- */

function showView(view = state.view) {
  state.view = view;
  renderHero();
  $$(".view").forEach((v) => (v.hidden = true));
  // during an update the device list is empty on purpose: keep showing the progress
  $("#view-update").classList.toggle("updating", state.updating);
  if (state.updating) { $("#nav").hidden = true; $("#view-update").hidden = false; return; }
  $("#nav").hidden = !state.udid || !!current()?.mode;
  if (!state.udid) { $("#view-empty").hidden = false; return; }
  if (current()?.mode) { $("#view-mode").hidden = false; renderMode(); return; }
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $(`#view-${view}`).hidden = false;
  if (view === "overview") renderOverview();
  if (view === "photos") openPhotos();
  if (view === "apps") openApps();
  if (view === "files") openFiles();
  if (view === "backups") openBackups();
  if (view === "screen") openScreen(); else stopScreen();
  if (view === "update") openUpdate();
}

$$("#nav button").forEach((b) => (b.onclick = () => showView(b.dataset.view)));

state.ovShowAll = false;
function renderOverview() {
  const d = current();
  if (!d) return;
  const yesNo = (v) => v == null ? null : v ? t("Yes") : t("No");
  const rows = [
    [t("Status"), d.connection === "USB" ? t("Connected via USB") : t("Connected via Wi-Fi")],
    [t("Name"), d.name],
    [t("Model"), d.model_name || d.product_type],
    ["iOS", d.ios ? `iOS ${d.ios} (${d.build})` : null],
    [t("Phone number"), d.phone],
    [t("Carrier"), d.carrier],
    [t("Serial number"), d.serial],
    ["UDID", d.udid],
    [t("Passcode enabled"), yesNo(d.passcode)],
    [t("Product type"), d.product_type],
    [t("Model number"), [d.model, d.region].filter(Boolean).join(" ") || null],
    [t("Board"), d.hardware_model],
    ["ECID", d.ecid != null ? `${d.ecid} (0x${BigInt(d.ecid).toString(16).toUpperCase()})` : null],
    ["IMEI", d.imei],
    ["IMEI 2", d.imei2],
    [t("Wi-Fi address"), d.wifi_mac],
    ["Bluetooth", d.bt_mac],
    [t("Modem (baseband)"), d.baseband],
    ["iBoot", d.iboot],
    [t("Activation"), d.activation === "Activated" ? t("Activated") : d.activation],
    [t("Time zone"), d.timezone],
  ].filter(([, v]) => v != null && v !== "");
  const dl = $("#ov-info");
  dl.innerHTML = "";
  for (const [k, v] of state.ovShowAll ? rows : rows.slice(0, 9)) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v;
    dl.append(dt, dd);
  }
  $("#ov-showall").textContent = state.ovShowAll ? t("Show less") : t("Show all");

  // battery
  $("#ov-battery").textContent = d.battery != null ? `${d.battery}%` : "—";
  $("#ov-battery-fill").style.width = `${d.battery ?? 0}%`;
  $("#ov-battery-fill").classList.toggle("low", d.battery != null && d.battery <= 20);
  $("#ov-charging").textContent = d.charging ? t("⚡ Charging") : d.battery != null ? "" : t("unlock the iPhone to see");

  renderStorage(d);
  const sw = $("#ov-wifi");
  sw.checked = !!d.wifi_enabled;
  sw.disabled = d.wifi_enabled == null;
}

// Storage bar: the iPhone only reports totals, so the parts come from what the app measured
function renderStorage(d) {
  const bar = $("#ov-storage-bar"), legend = $("#ov-storage-legend");
  bar.innerHTML = legend.innerHTML = "";
  if (!d.disk_total) { $("#ov-disk").textContent = t("unlock the iPhone to see"); return; }
  const free = d.disk_free || 0;
  const used = d.disk_total - free;
  $("#ov-disk").textContent = t("{used} used of {total}", { used: fmtBytes(used), total: fmtBytes(d.disk_total) });
  const photos = state.photosLoadedFor === d.udid && state.items.length ? state.items.reduce((a, i) => a + itemBytes(i), 0) : null;
  const parts = [
    [t("System"), d.system_size, "#8e8e93"],
    [t("Apps and data"), d.apps_total, "#0a84ff"],
    [t("Photos and videos on the iPhone"), photos, "#34c759"],
  ].filter(([, v]) => v);
  const known = parts.reduce((a, [, v]) => a + v, 0);
  parts.push([t("Other"), Math.max(0, used - known), "#ff9f0a"], [t("Free"), free, "var(--line)"]);
  for (const [label, bytes, color] of parts) {
    if (!bytes) continue;
    const seg = document.createElement("span");
    seg.style.width = `${(bytes / d.disk_total) * 100}%`;
    seg.style.background = color;
    seg.title = `${label}: ${fmtBytes(bytes)}`;
    bar.appendChild(seg);
    const li = document.createElement("span");
    li.innerHTML = `<i></i><b></b> <span class="muted"></span>`;
    li.querySelector("i").style.background = color;
    li.querySelector("b").textContent = label;
    li.querySelector(".muted").textContent = fmtBytes(bytes);
    legend.appendChild(li);
  }
  if (d.apps_total == null || photos == null) {
    const hint = document.createElement("span");
    hint.className = "muted small";
    hint.textContent = t("Open Apps and Photos to break down \"Other\".");
    legend.appendChild(hint);
  }
}

$("#ov-showall").onclick = () => { state.ovShowAll = !state.ovShowAll; renderOverview(); };
$$("[data-go]").forEach((b) => (b.onclick = () => showView(b.dataset.go)));

function renderHero() {
  const d = current();
  $("#hero").hidden = !d;
  if (!d) return;
  $("#hero-name").textContent = d.mode ? (d.product_type || "iPhone") : (d.name || "iPhone");
  $("#hero-sub").textContent = d.mode ? t("{mode} mode", { mode: MODE_LABEL[d.mode] || d.mode })
    : [d.model_name || d.product_type, d.ios ? `iOS ${d.ios}` : null].filter(Boolean).join(" · ");
  $("#hero-time").textContent = new Date().toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
  $("#hero-screen").classList.toggle("off", !!d.mode);
}

$("#ov-wifi").onchange = async (e) => {
  try {
    await api(`/api/devices/${state.udid}/wifi`, { json: { enabled: e.target.checked } });
    current().wifi_enabled = e.target.checked;
  } catch (err) {
    e.target.checked = !e.target.checked;
    toast(t("Couldn't change it: {error}", { error: err.message }));
  }
};

/* ---------------- Recovery / DFU ---------------- */

const MODE_NOTICE = {
  recovery: t("The device is in Recovery mode (iBoot). You can restart it into iOS or switch to DFU mode."),
  dfu: t("The device is in DFU mode (black screen). DFU can't be exited by software: do a force restart — the app shows how."),
  restore: t("The device is in the restore environment. Wait for the process to finish without unplugging the cable."),
  unknown: t("Unknown state."),
};

function renderMode() {
  const d = current();
  $("#md-name").textContent = d.product_type || "iPhone";
  $("#md-state").textContent = t("{mode} mode", { mode: MODE_LABEL[d.mode] || d.mode });
  $("#md-notice").textContent = MODE_NOTICE[d.mode] || "";
  $("#md-model").textContent = d.product_type || t("unknown");
  $("#md-serial").textContent = d.serial || "—";
  $("#md-ecid").textContent = `${d.ecid} (0x${BigInt(d.ecid).toString(16).toUpperCase()})`;
  $("#md-board").textContent = `CPID 0x${d.cpid.toString(16).toUpperCase()} · BDID 0x${d.bdid.toString(16).toUpperCase().padStart(2, "0")}`;
  $("#md-exit-recovery").hidden = d.mode !== "recovery";
  $("#md-enter-dfu").hidden = d.mode !== "recovery";
  $("#md-exit-dfu").hidden = d.mode !== "dfu";
}

$("#ov-recovery").onclick = () => {
  if (current()?.connection !== "USB") { toast(t("Connect the iPhone with the USB cable.")); return; }
  $("#dlg-recovery").showModal();
};
$("#dlg-recovery").onclose = async () => {
  if ($("#dlg-recovery").returnValue !== "ok") return;
  const btn = $("#ov-recovery");
  btn.disabled = true;
  try {
    await api(`/api/devices/${state.udid}/recovery/enter`, { method: "POST" });
    toast(t("Restarting into Recovery mode…"), false);
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; }
};
$("#md-exit-recovery").onclick = async (e) => {
  e.target.disabled = true;
  try {
    await api(`/api/modes/${current().ecid}/recovery/exit`, { method: "POST" });
    toast(t("Restarting into iOS… this can take up to a minute."), false);
  } catch (err) { toast(err.message); }
  finally { e.target.disabled = false; }
};
$("#ov-dfu").onclick = () => {
  if (current()?.connection !== "USB") { toast(t("Connect the iPhone with the USB cable.")); return; }
  openDfuGuide("enter");
};
$("#md-enter-dfu").onclick = () => openDfuGuide("enter");
$("#md-exit-dfu").onclick = () => openDfuGuide("exit");

// Which button combination this model uses
function dfuMethod(pt) {
  const m = /^(iPhone|iPad|iPod)(\d+),/.exec(pt || "");
  if (!m) return "modern";
  const fam = m[1], major = +m[2];
  if (fam === "iPod") return "home";
  if (fam === "iPhone") return major >= 10 ? "modern" : major === 9 ? "volume" : "home";
  return major <= 7 || major === 11 || major === 12 ? "home" : "modern";  // iPads with a Home button
}

function dfuSteps(kind, pt) {
  const ipad = (pt || "").startsWith("iPad");
  const side = ipad ? t("the top button") : t("the side button");
  const up = ipad ? t("the volume button nearest the top button") : t("the volume up button");
  const down = ipad ? t("the volume button farthest from the top button") : t("the volume down button");
  const v = { side, up, down };
  const method = dfuMethod(pt);
  if (kind === "exit") {  // force restart
    if (method === "home") return [{ t: t("Hold the Home button and {side} together until the Apple logo appears", v), s: 15 }];
    if (method === "volume") return [{ t: t("Hold {side} and {down} together until the Apple logo appears", v), s: 15 }];
    return [
      { t: t("Quickly press and release {up}", v), s: 1 },
      { t: t("Quickly press and release {down}", v), s: 1 },
      { t: t("Hold {side} until the Apple logo appears", v), s: 15 },
    ];
  }
  if (method === "home") return [
    { t: t("Hold the Home button and {side} together", v), s: 8 },
    { t: t("Release {side} and keep holding the Home button", v), s: 10 },
  ];
  if (method === "volume") return [
    { t: t("Hold {side} and {down} together", v), s: 8 },
    { t: t("Release {side} and keep holding {down}", v), s: 10 },
  ];
  return [
    { t: t("Quickly press and release {up}", v), s: 1 },
    { t: t("Quickly press and release {down}", v), s: 1 },
    { t: t("Hold {side} until the screen goes dark", v), s: 10 },
    { t: t("Without letting go, also hold {down}", v), s: 5 },
    { t: t("Release {side} and keep holding {down}", v), s: 10 },
  ];
}

const dfu = { kind: null, ecid: null, steps: [], step: -1, left: 0, timer: null, poll: null, done: false };

function openDfuGuide(kind) {
  const d = current();
  Object.assign(dfu, { kind, ecid: String(d.ecid), steps: dfuSteps(kind, d.product_type), step: -1, done: false });
  $("#dfu-title").textContent = kind === "enter" ? t("Enter DFU mode") : t("Exit DFU mode");
  $("#dfu-intro").textContent = kind === "enter"
    ? t("Keep the cable connected. Read the steps, rest your fingers on the buttons and click Start: the app times each step. In DFU the screen stays completely black — if the Apple logo or the Recovery screen appears, you held too long; try again.")
    : t("DFU can't be exited by software. Do a force restart:");
  setDfuStatus("");
  $("#dfu-start").hidden = false;
  $("#dfu-start").textContent = t("Start");
  renderDfuSteps();
  $("#dlg-dfu").showModal();
  clearInterval(dfu.poll);
  dfu.poll = setInterval(checkDfu, 1000);
}

function renderDfuSteps() {
  const ol = $("#dfu-steps");
  ol.innerHTML = "";
  dfu.steps.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = i < dfu.step || dfu.done ? "done" : i === dfu.step ? "now" : "";
    li.textContent = s.t;
    if (i === dfu.step && !dfu.done && s.s > 1) li.insertAdjacentHTML("beforeend", `<span class="count">${dfu.left}s</span>`);
    ol.appendChild(li);
  });
}

function setDfuStatus(text, cls = "") {
  const el = $("#dfu-status");
  el.textContent = text;
  el.className = "dfu-status " + cls;
}

$("#dfu-start").onclick = () => {
  clearInterval(dfu.timer);
  Object.assign(dfu, { step: 0, left: dfu.steps[0].s, done: false });
  $("#dfu-start").hidden = true;
  setDfuStatus("");
  renderDfuSteps();
  dfu.timer = setInterval(() => {
    if (--dfu.left > 0) { renderDfuSteps(); return; }
    if (++dfu.step < dfu.steps.length) { dfu.left = dfu.steps[dfu.step].s; renderDfuSteps(); return; }
    clearInterval(dfu.timer);
    dfu.step = dfu.steps.length;
    renderDfuSteps();
    setDfuStatus(t("Waiting for the device…"));
    dfu.timer = setTimeout(() => {  // give it a few seconds to show up before calling it a failure
      if (dfu.done) return;
      setDfuStatus(dfu.kind === "enter" ? t("DFU not detected. The timing of each step matters — try again.") : t("The device is still in DFU. Try again."), "bad");
      $("#dfu-start").hidden = false;
      $("#dfu-start").textContent = t("Try again");
    }, 8000);
  }, 1000);
};

async function checkDfu() {
  if (dfu.done) return;
  let m;
  try { m = await api("/api/modes"); } catch { return; }
  const d = m.devices.find((x) => x.ecid === dfu.ecid);
  let ok = null;
  if (dfu.kind === "enter" && d?.state === "dfu") ok = t("DFU detected! You can release the buttons.");
  if (dfu.kind === "exit" && dfu.step >= 0 && d?.state !== "dfu") ok = d ? t("Out of DFU — the device is starting up.") : t("Out of DFU — the device is restarting.");
  if (!ok) return;
  dfu.done = true;
  clearInterval(dfu.timer); clearTimeout(dfu.timer);
  renderDfuSteps();
  setDfuStatus(ok, "ok");
  $("#dfu-start").hidden = true;
  refreshDevices();
}

$("#dlg-dfu").onclose = () => { clearInterval(dfu.poll); clearInterval(dfu.timer); clearTimeout(dfu.timer); };

/* ---------------- photos ---------------- */

// Two sources, merged: the files on the iPhone (/DCIM scan: real size, downloadable) and the Photos
// database (the whole library incl. items only in iCloud, media types, albums).
async function openPhotos(force = false) {
  const udid = state.udid;
  if (state.photosLoadedFor === udid && !force) { renderPhotoSide(); return; }
  state.photosLoadedFor = udid;
  if (state.libFor !== udid || force) { state.lib = null; state.libFor = null; }
  $("#ph-grid").innerHTML = "";
  $("#ph-status").textContent = t("Reading the iPhone library…");
  const libPromise = state.lib ? Promise.resolve() : loadLibrary(udid, force);
  try {
    await api(`/api/devices/${udid}/photos/scan${force ? "?force=true" : ""}`, { method: "POST" });
    while (true) {
      const res = await api(`/api/devices/${udid}/photos`);
      if (state.udid !== udid) return;
      if (res.status === "done") { state.scanItems = res.items; break; }
      if (res.status === "error") throw new Error(res.error);
      $("#ph-status").textContent = t("Reading the iPhone library… {n} items", { n: res.done });
      await new Promise((r) => setTimeout(r, 700));
    }
  } catch (e) {
    state.photosLoadedFor = null;
    $("#ph-status").textContent = t("Error reading photos: {error}", { error: e.message });
    return;
  }
  state.selected.clear();
  mergeLibrary();
  renderPhotoSide();
  applyFilter();
  await libPromise;
}

async function loadLibrary(udid, refresh) {
  $("#ph-side").innerHTML = `<div class="muted small"><span class="spinner"></span> ${t("Reading albums and media types from the Photos app…")}</div>`;
  try {
    const lib = await api(`/api/devices/${udid}/library${refresh ? "?refresh=true" : ""}`);
    if (state.udid !== udid) return;
    state.lib = lib;
    state.libFor = udid;
    state.albumSets = new Map(lib.albums.map((a) => [a.id, new Set(a.items)]));
  } catch (e) {
    if (state.udid === udid) $("#ph-side").innerHTML = `<div class="muted small"></div>`, $("#ph-side .small").textContent = t("Albums unavailable: {error}", { error: e.message });
    return;
  }
  if (state.scanItems) { mergeLibrary(); applyFilter(); }
  renderPhotoSide();
}

function mergeLibrary() {
  const scan = state.scanItems || [];
  if (!state.lib) {
    state.items = scan.map((i) => ({ ...i, flags: i.kind === "video" ? F.VIDEO : 0, cloud: false }));
    return;
  }
  const byPath = new Map(scan.map((i) => [i.path, i]));
  const seen = new Set();
  state.items = state.lib.items.map(([pk, path, date, flags, size, guid]) => {
    const s = byPath.get(path);
    if (s) { seen.add(path); return { ...s, pk, guid, flags, cloud: false, btime: date || s.btime }; }
    // size: the original's size as the Photos database records it (the file itself is in iCloud)
    return { path, pk, guid, flags, cloud: true, name: path.slice(path.lastIndexOf("/") + 1),
      kind: flags & F.VIDEO ? "video" : "image", size: size || 0, btime: date, mtime: date };
  });
  for (const s of scan) if (!seen.has(s.path)) state.items.push({ ...s, flags: s.kind === "video" ? F.VIDEO : 0, cloud: false });
}

/* sort + group (shared by photos and the file browser) */
const DAY_FMT = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "long", year: "numeric", weekday: "short" });
function dateGroupKey(ms, group) {
  const d = new Date(ms), y = d.getFullYear(), m = String(d.getMonth() + 1).padStart(2, "0");
  if (group === "year") return `${y}`;
  if (group === "month") return `${y}-${m}`;
  return `${y}-${m}-${String(d.getDate()).padStart(2, "0")}`;
}
function dateGroupLabel(key, group) {
  if (group === "year") return key;
  if (group === "month") return monthLabel(key);
  const [y, m, d] = key.split("-").map(Number);
  return DAY_FMT.format(new Date(y, m - 1, d));
}
// Sorts in place. With groups, groups follow the date direction and the chosen order applies inside each one.
function sortItems(list, sort, groupOf, timeOf, sizeOf) {
  const [field, dir] = sort.split("-");
  const sign = dir === "asc" ? 1 : -1;
  const groupSign = field === "date" ? sign : -1;
  const key = field === "size" ? sizeOf : timeOf;
  return list.sort((a, b) => {
    if (groupOf) {
      const ga = groupOf(a), gb = groupOf(b);
      if (ga !== gb) return (ga < gb ? -1 : 1) * groupSign;
    }
    return (key(a) - key(b)) * sign;
  });
}
function loadPref(name, fallback) { try { return localStorage.getItem(name) || fallback; } catch { return fallback; } }
function savePref(name, value) { try { localStorage.setItem(name, value); } catch {} }

state.phSort = loadPref("ph-sort", "date-desc");
state.phGroup = loadPref("ph-group", "month");
$("#ph-sort").value = state.phSort;
$("#ph-group").value = state.phGroup;
$("#ph-sort").onchange = (e) => { state.phSort = e.target.value; savePref("ph-sort", state.phSort); applyFilter(); };
$("#ph-group").onchange = (e) => { state.phGroup = e.target.value; savePref("ph-group", state.phGroup); applyFilter(); };
const photoGroup = (it) => state.phGroup === "none" ? "" : dateGroupKey(itemTime(it), state.phGroup);

/* library side panel: Library / Media types / Albums, like the Photos app (and iMazing) */
// item flags, as app/albums.py sends them
const F = { FAV: 1, HIDDEN: 2, TRASHED: 4, LIVE: 8, SCREENSHOT: 16, PANO: 32, SLOMO: 64, TIMELAPSE: 128, SCREENREC: 256,
  PORTRAIT: 512, CINEMATIC: 1024, BURST: 2048, ANIMATED: 4096, SELFIE: 8192, VIDEO: 16384 };
const visible = (i) => !(i.flags & (F.HIDDEN | F.TRASHED));
const hasFlag = (f) => (i) => visible(i) && (i.flags & f);
const SIDE_ICONS = {
  all: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-8 8"/>',
  local: '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M10.5 18.5h3"/>',
  fav: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  hidden: '<path d="M3 3l18 18M10.6 6.2A9.8 9.8 0 0 1 12 6c5 0 9 6 9 6a16 16 0 0 1-3 3.5M6.6 6.6C4.4 8 3 12 3 12s4 6 9 6a8.6 8.6 0 0 0 4-1"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  type: '<rect x="4" y="5" width="16" height="14" rx="3"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
  live: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="6.5" stroke-dasharray="2 2"/>',
  album: '<path d="M4 8h16v11H4zM6 5h12"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
};
const PHOTO_GROUPS = [
  [t("Library"), [
    ["all", t("All Photos"), "all", visible],
    ["local", t("On the iPhone (downloadable)"), "local", (i) => visible(i) && !i.cloud],
    ["fav", t("Favorites"), "fav", hasFlag(F.FAV)],
    ["hidden", t("Hidden"), "hidden", (i) => (i.flags & F.HIDDEN) && !(i.flags & F.TRASHED)],
    ["trash", t("Recently Deleted"), "trash", (i) => i.flags & F.TRASHED],
  ]],
  [t("Media Types"), [
    ["std", t("Standard photos"), "type", (i) => visible(i) && !(i.flags & (F.VIDEO | F.LIVE | F.SCREENSHOT | F.PANO | F.PORTRAIT | F.BURST | F.ANIMATED))],
    ["video", t("Videos"), "video", hasFlag(F.VIDEO)],
    ["selfie", t("Selfies"), "type", hasFlag(F.SELFIE)],
    ["live", t("Live Photos"), "live", hasFlag(F.LIVE)],
    ["portrait", t("Portrait"), "type", hasFlag(F.PORTRAIT)],
    ["pano", t("Panoramas"), "type", hasFlag(F.PANO)],
    ["timelapse", t("Time-lapse"), "video", hasFlag(F.TIMELAPSE)],
    ["slomo", t("Slo-mo"), "video", hasFlag(F.SLOMO)],
    ["cinematic", t("Cinematic"), "video", hasFlag(F.CINEMATIC)],
    ["burst", t("Bursts"), "type", hasFlag(F.BURST)],
    ["screenshot", t("Screenshots"), "type", hasFlag(F.SCREENSHOT)],
    ["screenrec", t("Screen Recordings"), "video", hasFlag(F.SCREENREC)],
    ["animated", t("Animated"), "type", hasFlag(F.ANIMATED)],
  ]],
];
state.phView = "all";

function groupFilter(id) {
  if (id.startsWith("album-")) {
    const set = state.albumSets?.get(Number(id.slice(6)));
    return (i) => set && set.has(i.pk) && visible(i);
  }
  for (const [, entries] of PHOTO_GROUPS) for (const [gid, , , fn] of entries) if (gid === id) return fn;
  return visible;
}
function groupTitle(id) {
  if (id.startsWith("album-")) return state.lib?.albums.find((a) => `album-${a.id}` === id)?.title || t("Album");
  for (const [, entries] of PHOTO_GROUPS) for (const [gid, title] of entries) if (gid === id) return title;
  return t("All Photos");
}

function renderPhotoSide() {
  const side = $("#ph-side");
  if (!state.items) return;
  side.innerHTML = "";
  const icon = (k) => `<svg viewBox="0 0 24 24">${SIDE_ICONS[k] || SIDE_ICONS.type}</svg>`;
  const addItem = (parent, id, title, iconKey, count, depth = 0) => {
    const b = document.createElement("button");
    b.className = "side-item" + (state.phView === id ? " active" : "");
    b.style.paddingLeft = `${10 + depth * 16}px`;
    b.innerHTML = `${icon(iconKey)}<span class="t"></span><span class="n"></span>`;
    b.querySelector(".t").textContent = title;
    b.querySelector(".n").textContent = count.toLocaleString(LOCALE);
    b.onclick = () => { state.phView = id; state.selected.clear(); renderPhotoSide(); applyFilter(); };
    parent.appendChild(b);
  };
  const section = (title) => {
    const h = document.createElement("div");
    h.className = "side-sec"; h.textContent = title;
    side.appendChild(h);
  };
  for (const [secTitle, entries] of PHOTO_GROUPS) {
    if (!state.lib && secTitle !== t("Library")) continue;
    section(secTitle);
    for (const [id, title, ic, fn] of entries) {
      if (!state.lib && id !== "all" && id !== "local") continue;
      const n = state.items.filter(fn).length;
      if (n || ["all", "local", "fav", "hidden", "trash"].includes(id)) addItem(side, id, title, ic, n);
    }
  }
  if (state.lib && state.lib.albums.length) {
    section(t("Albums"));
    const count = (a) => state.items.filter(groupFilter(`album-${a.id}`)).length;
    const walk = (parent, depth) => {
      for (const f of state.lib.folders.filter((x) => x.parent === parent)) {
        const h = document.createElement("div");
        h.className = "side-folder"; h.style.paddingLeft = `${10 + depth * 16}px`;
        h.innerHTML = `${icon("folder")}<span></span>`; h.lastChild.textContent = f.title;
        side.appendChild(h);
        walk(f.id, depth + 1);
      }
      for (const a of state.lib.albums.filter((x) => x.parent === parent)) addItem(side, `album-${a.id}`, a.title, "album", count(a), depth);
    };
    walk(null, 0);
  }
  if (!state.lib) {
    const m = document.createElement("div");
    m.className = "muted small"; m.innerHTML = `<span class="spinner"></span> ${t("Reading albums and media types…")}`;
    side.appendChild(m);
  }
}

function applyFilter() {
  if (!state.items) return;
  let list = state.items.filter(groupFilter(state.phView));
  const inGroup = list.length, cloud = list.filter((i) => i.cloud).length;
  if (state.filter !== "all") list = list.filter((i) => i.kind === state.filter);
  $("#ph-title").textContent = groupTitle(state.phView);
  const ainfo = $("#ph-album-info");
  ainfo.hidden = !cloud;
  ainfo.textContent = cloud ? t("{n} of these items are only in iCloud (Optimize Storage): they show with ☁ and the thumbnail " +
    "the iPhone keeps, but the original is not on the device and can't be downloaded over the cable.", { n: cloud.toLocaleString(LOCALE) }) : "";
  state.filtered = sortItems(list, state.phSort, state.phGroup === "none" ? null : photoGroup, itemTime, itemBytes);
  state.groupStats = new Map();
  if (state.phGroup !== "none") {
    for (const it of state.filtered) {
      const g = photoGroup(it), s = state.groupStats.get(g) || { n: 0, bytes: 0 };
      s.n++; s.bytes += itemBytes(it);
      state.groupStats.set(g, s);
    }
  }
  const local = state.filtered.filter((i) => !i.cloud);
  $("#ph-count").textContent = t("{n} items ({size}) · {local} on the iPhone ({localSize})", {
    n: inGroup.toLocaleString(LOCALE), size: fmtBytes(state.filtered.reduce((a, i) => a + itemBytes(i), 0)),
    local: (inGroup - cloud).toLocaleString(LOCALE), localSize: fmtBytes(local.reduce((a, i) => a + itemBytes(i), 0)),
  });
  $("#ph-status").textContent = state.filtered.length ? "" : t("No items.");
  $("#ph-grid").innerHTML = "";
  state.rendered = 0;
  state.lastMonth = null;
  state.lastGrid = null;
  renderMore();
  updateSelection();
}

const BATCH = 240;
function renderMore() {
  const wrap = $("#ph-grid");
  const end = Math.min(state.rendered + BATCH, state.filtered.length);
  for (let i = state.rendered; i < end; i++) {
    const it = state.filtered[i];
    const mk = photoGroup(it);
    if (mk !== state.lastMonth || !state.lastGrid) {
      state.lastMonth = mk;
      if (mk) {
        const st = state.groupStats.get(mk);
        const h = document.createElement("div");
        h.className = "month";
        h.innerHTML = `<span></span><span class="muted gstat"></span><button></button>`;
        h.firstChild.textContent = dateGroupLabel(mk, state.phGroup);
        h.children[1].textContent = t("{n} items · {size}", { n: st.n, size: fmtBytes(st.bytes) });
        h.lastChild.textContent = t("Select group");
        h.lastChild.onclick = () => selectMonth(mk);
        wrap.appendChild(h);
      }
      state.lastGrid = document.createElement("div");
      state.lastGrid.className = "grid";
      wrap.appendChild(state.lastGrid);
    }
    state.lastGrid.appendChild(makeTile(it, i));
  }
  state.rendered = end;
}

function makeTile(it, idx) {
  const tile = document.createElement("div");
  tile.className = "tile" + (state.selected.has(it.path) ? " sel" : "") + (it.cloud ? " cloud" : "");
  tile.dataset.idx = idx;
  tile.title = `${it.name} · ${fmtBytes(it.size)}${it.cloud ? " " + t("(only in iCloud)") : ""} · ${new Date(itemTime(it)).toLocaleString(LOCALE)}`;
  const img = document.createElement("img");
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  img.onload = () => img.classList.add("loaded");
  img.onerror = () => { img.remove(); tile.insertAdjacentHTML("afterbegin", `<div class="ph"></div>`); tile.querySelector(".ph").textContent = it.name; };
  img.src = `/api/devices/${state.udid}/thumb?path=${enc(it.path)}`;
  tile.appendChild(img);
  if (it.kind === "video") tile.insertAdjacentHTML("beforeend", `<span class="badge">▶ ${fmtBytes(it.size)}</span>`);
  else if (it.live || (it.flags & F.LIVE)) tile.insertAdjacentHTML("beforeend", `<span class="badge">LIVE</span>`);
  else if (/\.(heic|heif)$/i.test(it.name)) tile.insertAdjacentHTML("beforeend", `<span class="badge">HEIC</span>`);
  if (it.cloud) tile.insertAdjacentHTML("beforeend", `<span class="cloud-badge" title="${t("Only in iCloud")}">☁</span>`);
  const chk = document.createElement("span");
  chk.className = "chk";
  if (selectable(it)) tile.appendChild(chk);  // iCloud-only items need the iCloud connection
  tile.onclick = (e) => {
    const selecting = state.selected.size > 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.target === chk;
    if (selecting && selectable(it)) toggleSelect(idx, e.shiftKey);
    else if (!selecting) openLightbox(idx);
  };
  return tile;
}

new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting && state.rendered < state.filtered.length) renderMore();
}, { root: $("main"), rootMargin: "800px" }).observe($("#ph-sentinel"));

function toggleSelect(idx, range) {
  if (range && state.lastClicked != null) {
    const [a, b] = [Math.min(state.lastClicked, idx), Math.max(state.lastClicked, idx)];
    for (let i = a; i <= b; i++) if (selectable(state.filtered[i])) state.selected.add(state.filtered[i].path);
  } else {
    const p = state.filtered[idx].path;
    state.selected.has(p) ? state.selected.delete(p) : state.selected.add(p);
  }
  state.lastClicked = idx;
  updateSelection();
}

function selectMonth(mk) {
  const inMonth = state.filtered.filter((i) => photoGroup(i) === mk && selectable(i));
  const all = inMonth.every((i) => state.selected.has(i.path));
  inMonth.forEach((i) => (all ? state.selected.delete(i.path) : state.selected.add(i.path)));
  updateSelection();
}

function updateSelection() {
  $$("#ph-grid .tile").forEach((tile) => tile.classList.toggle("sel", state.selected.has(state.filtered[tile.dataset.idx].path)));
  const n = state.selected.size;
  const bytes = state.items.filter((i) => state.selected.has(i.path)).reduce((a, i) => a + itemBytes(i), 0);
  $("#ph-selinfo").textContent = n ? t("{n} selected · {size}", { n, size: fmtBytes(bytes) }) : t("Ctrl+click or click the circle to select");
  $("#ph-export").disabled = !n;
  $("#ph-delete").hidden = !n;
  $("#ph-selnone").hidden = !n;
  $("#ph-grid").classList.toggle("selecting", n > 0);
}

$$("#ph-filter button").forEach((b) => (b.onclick = () => {
  $$("#ph-filter button").forEach((x) => x.classList.toggle("active", x === b));
  state.filter = b.dataset.f;
  applyFilter();
}));
$("#ph-selall").onclick = () => { state.filtered.forEach((i) => selectable(i) && state.selected.add(i.path)); updateSelection(); };
$("#ph-selnone").onclick = () => { state.selected.clear(); updateSelection(); };
$("#ph-rescan").onclick = () => openPhotos(true);
// On the iPhone: deleted by the iPhone itself over PTP (in our test it skipped Recently Deleted).
// Only in iCloud: deleted through iCloud, which moves them to Recently Deleted on every device.
$("#ph-delete").onclick = async () => {
  const sel = state.items.filter((i) => state.selected.has(i.path));
  const local = sel.filter((i) => !i.cloud), cloud = sel.filter((i) => i.cloud && i.guid);
  const bytes = (l) => fmtBytes(l.reduce((a, i) => a + itemBytes(i), 0));
  const live = local.filter((i) => i.live).length;
  const parts = [];
  if (local.length) parts.push(t("{n} on the iPhone ({size}): the iPhone itself deletes them, as in the Photos app — " +
    "in our test, without going through Recently Deleted. With iCloud Photos on, they also disappear from iCloud.",
    { n: local.length, size: bytes(local) }) +
    (live ? " " + t("{n} are Live Photos (the photo and the video go together).", { n: live }) : ""));
  if (cloud.length) parts.push(t("{n} only in iCloud ({size}): they go to Recently Deleted on all " +
    "devices (recoverable for 30 days).", { n: cloud.length, size: bytes(cloud) }));
  if (!await ask(t("Delete {n} item(s) ({size})?", { n: sel.length, size: bytes(sel) }),
      parts.join("\n\n") + "\n\n" + t("Download first whatever you want to keep."), t("Delete"), true)) return;
  const btn = $("#ph-delete"), status = $("#ph-status");
  btn.disabled = true; btn.textContent = t("Deleting…");
  const done = [], problems = [];
  try {
    if (cloud.length) {
      status.innerHTML = `<span class="spinner"></span> ${t("Deleting {n} item(s) in iCloud…", { n: cloud.length })}`;
      const r = await api("/api/icloud/delete", { json: { guids: cloud.map((i) => i.guid), confirm: true } });
      const ok = new Set(r.deleted);
      for (const it of cloud) if (ok.has(it.guid)) { it.flags |= F.TRASHED; done.push(it.path); }
      if (r.failed.length) problems.push(t("{n} from iCloud failed ({error})", { n: r.failed.length, error: r.failed[0].error }));
    }
    if (local.length) {
      status.innerHTML = `<span class="spinner"></span> ` + t("Locating {n} item(s) on the iPhone and deleting… " +
        "(this can take up to a minute; keep the iPhone unlocked)", { n: local.length });
      const r = await api(`/api/devices/${state.udid}/photos/delete`, { json: { paths: local.map((i) => i.path), confirm: true } });
      const gone = new Set(r.deleted);
      state.items = state.items.filter((i) => !gone.has(i.path));
      state.scanItems = (state.scanItems || []).filter((i) => !gone.has(i.path));
      done.push(...r.deleted);
      const skipped = r.not_found.length + r.ambiguous.length;
      if (skipped) problems.push(t("{n} on the iPhone could not be located with certainty", { n: skipped }));
    }
  } catch (e) { problems.push(e.message); }
  finally {
    btn.disabled = false; btn.textContent = t("Delete from iPhone"); status.textContent = "";
    state.selected.clear();
    renderPhotoSide();
    applyFilter();
  }
  toast(t("{n} item(s) deleted.", { n: done.length }) + (problems.length ? " " + problems.join("; ") + "." : ""), problems.length > 0);
};

// iCloud originals go straight to the browser's downloads; what is on the iPhone uses the export dialog
function exportItems(items) {
  const cloud = items.filter((i) => i.cloud && i.guid && icloudOk());
  cloud.forEach((it, n) => setTimeout(() => {
    const a = document.createElement("a");
    a.href = `/api/icloud/${enc(it.guid)}/original?download=true`;
    document.body.appendChild(a); a.click(); a.remove();
  }, n * 800));
  if (cloud.length) toast(t("Downloading {n} original(s) from iCloud ({size}) to the browser's downloads folder…",
    { n: cloud.length, size: fmtBytes(cloud.reduce((a, i) => a + i.size, 0)) }), false);
  const local = items.filter((i) => !i.cloud).map((i) => i.path);
  if (local.length) exportDialog(local);
}
$("#ph-export").onclick = () => exportItems(state.items.filter((i) => state.selected.has(i.path)));
$("#ph-export-all").onclick = () => exportDialog(state.filtered.filter((i) => !i.cloud).map((i) => i.path));

/* lightbox */
let lbIdx = 0;
function openLightbox(idx) {
  lbIdx = idx;
  const it = state.filtered[idx];
  const body = $("#lb-body");
  $$("video", body).forEach(stopVideo);
  body.innerHTML = "";
  body.onmouseenter = body.onmouseleave = null;
  const viaICloud = it.cloud && it.guid && icloudOk();
  $("#lb-download").hidden = it.cloud && !viaICloud;
  $("#lb-download").textContent = viaICloud ? t("Download original from iCloud") : t("Download");
  $("#lb-delete").hidden = !viaICloud;
  if (viaICloud) {
    openICloudItem(body, it, idx);
    return;
  }
  if (it.cloud) {
    // the original is only in iCloud: show the biggest thumbnail the iPhone keeps
    const img = document.createElement("img");
    img.src = `/api/devices/${state.udid}/thumb?full=true&path=${enc(it.path)}`;
    img.onerror = () => { body.textContent = t("No preview on the iPhone."); };
    body.appendChild(img);
    $("#lb-name").textContent = `${it.name} · ${t("☁ only in iCloud (reduced preview — connect iCloud to see the original)")} · ` +
      new Date(itemTime(it)).toLocaleString(LOCALE);
    $("#lightbox").hidden = false;
    return;
  }
  const url = `/api/devices/${state.udid}/file?path=${enc(it.path)}`;
  if (it.kind === "video") {
    openVideo(body, idx, {
      info: `/api/devices/${state.udid}/video/info?path=${enc(it.path)}`,
      stream: (sec) => `/api/devices/${state.udid}/video?path=${enc(it.path)}&t=${sec}`,
      direct: url,
    });
  } else {
    const img = document.createElement("img");
    img.src = /\.(jpe?g|png|gif|webp)$/i.test(it.name) ? url : `/api/devices/${state.udid}/preview?path=${enc(it.path)}`;
    body.textContent = t("Loading…");
    img.onload = () => {
      body.textContent = "";
      body.appendChild(img);
      if (it.live) {
        // hovering a Live Photo plays its motion part, like the Photos app
        const v = document.createElement("video");
        v.src = `/api/devices/${state.udid}/file?path=${enc(it.live)}`;
        v.muted = true; v.playsInline = true; v.hidden = true;
        v.onended = () => { v.hidden = true; img.hidden = false; };
        body.onmouseenter = () => { if (v.readyState >= 2 || v.networkState !== 3) { img.hidden = true; v.hidden = false; v.currentTime = 0; v.play().catch(() => { v.hidden = true; img.hidden = false; }); } };
        body.onmouseleave = () => { v.pause(); v.hidden = true; img.hidden = false; };
        body.appendChild(v);
      }
    };
    img.onerror = () => { body.textContent = t("Couldn't open the image."); };
  }
  $("#lb-name").textContent = `${it.name}${it.live ? " · " + t("Live Photo (hover to play)") : ""} · ${fmtBytes(itemBytes(it))} · ${new Date(itemTime(it)).toLocaleString(LOCALE)}`;
  $("#lightbox").hidden = false;
}
// iPhone videos are HEVC, which Edge/Chrome on Windows play as audio only: the server converts them
// on the fly (video.py). That stream can't seek by itself, so the bar below restarts it at the chosen time.
const fmtTime = (s) => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  return `${h ? h + ":" + String(m).padStart(2, "0") : m}:${String(s % 60).padStart(2, "0")}`; };
// src: {info: URL of the probe, stream: (sec) => URL of the conversion from sec s, direct: URL of the file}
async function openVideo(body, idx, src) {
  body.innerHTML = `<div class="vwrap"><video autoplay controls playsinline></video>
    <div class="vbar" hidden><input type="range" min="0" step="1" value="0"><span class="vt muted"></span></div>
    <div class="vmsg muted"></div></div>`;
  const v = $("video", body), bar = $(".vbar", body), range = $("input", bar), label = $(".vt", bar), msg = $(".vmsg", body);
  msg.textContent = t("Preparing the video…");
  let info;
  try { info = await api(src.info); }
  catch { info = { direct: true, duration: 0 }; }
  if (lbIdx !== idx || $("#lightbox").hidden) return;
  if (info.direct) {  // H.264: the browser plays the file itself, with normal seeking
    msg.textContent = "";
    v.src = src.direct;
    v.onerror = () => { msg.textContent = t("This video can't be played here. Download it to watch."); };
    return;
  }
  v.controls = false;  // the browser's own bar can't seek a live conversion: ours below does
  v.onclick = () => (v.paused ? v.play() : v.pause());
  bar.hidden = false;
  range.max = Math.max(1, Math.floor(info.duration));
  let offset = 0, dragging = false;
  const show = (sec) => (label.textContent = `${fmtTime(sec)} / ${fmtTime(info.duration)}`);
  const play = (sec) => {
    offset = sec;
    msg.textContent = t("Converting the video ({codec} {w}×{h}) for playback…",
      { codec: info.codec?.toUpperCase() || "HEVC", w: info.width || "", h: info.height || "" });
    stopVideo(v);  // end the previous conversion before starting the next
    v.src = src.stream(sec);
    v.play().catch(() => {});
  };
  v.onplaying = () => { msg.textContent = t("Click the video to pause · use the bar to skip"); };
  v.onerror = () => { msg.textContent = t("Couldn't convert this video. Download it to watch."); };
  v.ontimeupdate = () => { const sec = offset + v.currentTime; if (!dragging) range.value = sec; show(sec); };
  range.oninput = () => { dragging = true; show(Number(range.value)); };
  range.onchange = () => { dragging = false; play(Number(range.value)); };
  show(0);
  play(0);
}

// Abort a video's download: just removing the element can leave its request (and the server's
// conversion) running
function stopVideo(v) {
  if (!v || !v.getAttribute("src")) return;
  v.pause();
  v.removeAttribute("src");
  v.load();
}

function closeLightbox() {
  $$("#lb-body video").forEach(stopVideo);
  $("#lightbox").hidden = true;
  $("#lb-body").innerHTML = "";
}
$("#lb-close").onclick = closeLightbox;
$("#lb-prev").onclick = () => lbIdx > 0 && openLightbox(lbIdx - 1);
$("#lb-next").onclick = () => lbIdx < state.filtered.length - 1 && openLightbox(lbIdx + 1);
$("#lb-download").onclick = () => {
  const it = state.filtered[lbIdx];
  if (it.cloud && it.guid && icloudOk()) {  // straight from iCloud to the browser's downloads
    const a = document.createElement("a");
    a.href = `/api/icloud/${enc(it.guid)}/original?download=true`;
    document.body.appendChild(a); a.click(); a.remove();
    toast(t("Downloading {name} from iCloud ({size}) to the downloads folder…", { name: it.name, size: fmtBytes(it.size) }), false);
    return;
  }
  exportDialog([it.path]);
};
$("#lb-delete").onclick = async () => {
  const it = state.filtered[lbIdx];
  if (!await ask(t("Delete {name} from iCloud?", { name: it.name }), t("It goes to \"Recently Deleted\" on all your devices " +
      "(recoverable for 30 days in the Photos app or on icloud.com)."), t("Delete from iCloud"), true)) return;
  try {
    const r = await api("/api/icloud/delete", { json: { guids: [it.guid], confirm: true } });
    if (r.failed.length) throw new Error(r.failed[0].error);
    it.flags |= F.TRASHED;  // the Photos database on the iPhone catches up when it syncs
    toast(t("{name} moved to Recently Deleted.", { name: it.name }), false);
    closeLightbox();
    renderPhotoSide();
    applyFilter();
  } catch (e) { toast(e.message); }
};

/* iCloud: originals of items that are not on the iPhone */
state.icloud = { state: "logged_out" };
const icloudOk = () => state.icloud.state === "ok";

function openICloudItem(body, it, idx) {
  const when = new Date(itemTime(it)).toLocaleString(LOCALE);
  $("#lb-name").textContent = `${it.name} · ${t("☁ iCloud original")} · ${fmtBytes(it.size)} · ${when}`;
  $("#lightbox").hidden = false;
  const g = enc(it.guid);
  if (it.kind === "video") {
    openVideo(body, idx, { info: `/api/icloud/${g}/info`, stream: (sec) => `/api/icloud/${g}/video?t=${sec}`,
      direct: `/api/icloud/${g}/playable` });
    return;
  }
  body.textContent = t("Downloading the original from iCloud…");
  const img = document.createElement("img");
  // JPEG/PNG/GIF show as they are; HEIC/DNG are converted by the server
  img.src = /\.(jpe?g|png|gif|webp)$/i.test(it.name) ? `/api/icloud/${g}/original` : `/api/icloud/${g}/preview`;
  img.onload = () => { if (lbIdx === idx) { body.textContent = ""; body.appendChild(img); } };
  img.onerror = () => { if (lbIdx === idx) body.textContent = t("Couldn't open the original in iCloud."); };
}

async function refreshICloud() {
  const was = icloudOk();
  try { state.icloud = await api("/api/icloud/status"); } catch { state.icloud = { state: "logged_out" }; }
  const b = $("#ic-btn");
  b.textContent = icloudOk() ? `☁ iCloud: ${state.icloud.apple_id}` : t("☁ Connect iCloud");
  b.classList.toggle("connected", icloudOk());
  if (was !== icloudOk() && state.view === "photos") {  // iCloud items just became (un)selectable
    if (!icloudOk()) state.items?.forEach((i) => i.cloud && state.selected.delete(i.path));
    applyFilter();
  }
}
// what can be selected (downloaded/deleted): items on the iPhone, and iCloud-only ones once connected
const selectable = (it) => !it.cloud || (!!it.guid && icloudOk());

// status line of the sign-in dialog: grey while working, red only for errors
function icMsg(text, kind = "info") {
  const el = $("#ic-err");
  el.className = "ic-msg" + (kind === "error" ? " error" : "");
  el.innerHTML = kind === "busy" ? `<span class="spinner"></span> ` : "";
  el.append(text);
}
function icShowStep() {
  $("#ic-step-login").hidden = icloudOk() || state.icloud.state === "need_2fa";
  $("#ic-step-code").hidden = state.icloud.state !== "need_2fa";
  $("#ic-step-ok").hidden = !icloudOk();
  $("#ic-account").textContent = state.icloud.apple_id || "";
  const focus = state.icloud.state === "need_2fa" ? "#ic-code" : icloudOk() ? null : ($("#ic-id").value ? "#ic-pass" : "#ic-id");
  if (focus) setTimeout(() => $(focus).focus(), 0);
}
$("#ic-btn").onclick = () => {
  icMsg("");
  if (!$("#ic-id").value && state.icloud.apple_id) $("#ic-id").value = state.icloud.apple_id;
  $("#dlg-icloud").showModal();
  icShowStep();
};
// Enter submits the current step (the dialog form would otherwise close on its first button)
$("#dlg-icloud form").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.target.tagName !== "INPUT") return;
  e.preventDefault();
  if (!$("#ic-step-code").hidden) $("#ic-verify").click();
  else if (!$("#ic-step-login").hidden) $("#ic-login").click();
});
$("#ic-eye").onclick = () => {
  const p = $("#ic-pass"), show = p.type === "password";
  p.type = show ? "text" : "password";
  $("#ic-eye").classList.toggle("on", show);
  $("#ic-eye").title = show ? t("Hide password") : t("Show password");
  p.focus();
};
$("#ic-login").onclick = async () => {
  const btn = $("#ic-login");
  if (!$("#ic-id").value.trim() || !$("#ic-pass").value) { icMsg(t("Enter the Apple ID and the password."), "error"); return; }
  btn.disabled = true; icMsg(t("Connecting to Apple…"), "busy");
  try {
    state.icloud = await api("/api/icloud/login", { json: { apple_id: $("#ic-id").value, password: $("#ic-pass").value } });
    $("#ic-pass").value = "";  // not kept anywhere
    $("#ic-pass").type = "password"; $("#ic-eye").classList.remove("on");
    icMsg(state.icloud.state === "need_2fa" ? t("Password accepted.") : "");
    icShowStep();
    if (icloudOk()) { $("#dlg-icloud").close(); toast(t("iCloud connected."), false); }
  } catch (e) { icMsg(e.message, "error"); }
  finally { btn.disabled = false; refreshICloud(); }
};
$("#ic-verify").onclick = async () => {
  const btn = $("#ic-verify");
  if (!/^\d{6}$/.test($("#ic-code").value.trim())) { icMsg(t("Enter the 6-digit code."), "error"); return; }
  btn.disabled = true; icMsg(t("Verifying the code…"), "busy");
  try {
    state.icloud = await api("/api/icloud/2fa", { json: { code: $("#ic-code").value } });
    $("#ic-code").value = "";
    $("#dlg-icloud").close();
    toast(t("iCloud connected."), false);
  } catch (e) {
    icMsg(e.message, "error");
    await refreshICloud();
    icShowStep();  // e.g. back to the password if Apple ended the attempt
  }
  finally { btn.disabled = false; refreshICloud(); }
};
$("#ic-logout").onclick = async () => {
  await api("/api/icloud/logout", { method: "POST" });
  $("#dlg-icloud").close();
  refreshICloud();
};
refreshICloud();
document.addEventListener("keydown", (e) => {
  if ($("#lightbox").hidden) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") $("#lb-prev").click();
  if (e.key === "ArrowRight") $("#lb-next").click();
});

/* export */
let exportPaths = [];
async function exportDialog(paths) {
  exportPaths = paths;
  $("#ex-count").textContent = paths.length === 1 ? t("1 item") : t("{n} items", { n: paths.length });
  if (!$("#ex-dest").value) {
    const d = await api("/api/defaults");
    const name = (current()?.name || "iPhone").replace(/[\\/:*?"<>|]/g, "_");
    $("#ex-dest").value = `${d.photos_dest}\\${name}`;
  }
  $("#dlg-export").showModal();
}
$("#ex-browse").onclick = async () => {
  const r = await api("/api/pick/folder", { method: "POST" });
  if (r.path) $("#ex-dest").value = r.path.replaceAll("/", "\\");
};
$("#dlg-export").onclose = async () => {
  if ($("#dlg-export").returnValue !== "ok") return;
  try {
    const job = await api(`/api/devices/${state.udid}/photos/download`, {
      json: { paths: exportPaths, dest: $("#ex-dest").value, convert_heic: $("#ex-heic").checked, by_month: $("#ex-month").checked, include_live: $("#ex-live").checked },
    });
    job.kind = "photos";
    state.jobs.set(job.id, job);
    renderTray();
  } catch (e) { toast(e.message); }
};

/* ---------------- transfer tray (photo exports + ipsw downloads) ---------------- */

function renderTray() {
  const tray = $("#tray");
  $$(".tjob[data-id]", tray).forEach((el) => { if (!state.jobs.has(el.dataset.id)) el.remove(); });
  for (const job of state.jobs.values()) {
    let el = $(`.tjob[data-id="${job.id}"]`, tray);
    if (!el) {
      el = document.createElement("div");
      el.className = "tjob";
      el.dataset.id = job.id;
      el.innerHTML = `<div class="t"><span class="n"></span><span class="p muted"></span></div><div class="bar"><div></div></div><div class="s"></div><div class="a"></div>`;
      tray.appendChild(el);
    }
    const pct = job.kind === "backup" ? job.percent || 0
      : job.bytes_total ? (job.bytes_done / job.bytes_total) * 100 : (job.total ? (job.done / job.total) * 100 : 0);
    $(".bar > div", el).style.width = `${Math.min(100, pct)}%`;
    $(".p", el).textContent = `${Math.floor(pct)}%`;
    const running = job.status === "running" || job.status === "verifying";
    if (job.kind === "photos") {
      const fileNo = Math.min(job.done + (running && job.current ? 1 : 0), job.total);
      $(".n", el).textContent = running ? t("Downloading {i}/{n}", { i: fileNo, n: job.total })
        : job.status === "done" ? t("{n} items downloaded", { n: job.total })
        : job.status === "cancelled" ? t("Cancelled · {i}/{n} downloaded", { i: job.done, n: job.total }) : t("Error");
      if (running) {
        const left = job.speed ? (job.bytes_total - job.bytes_done) / job.speed : 0;
        $(".s", el).textContent =
          `${job.current || ""}${job.current ? " · " + t("{done} of {total}", { done: fmtBytes(job.current_done), total: fmtBytes(job.current_size) }) : ""}\n` +
          t("Total: {done} of {total}", { done: fmtBytes(job.bytes_done), total: fmtBytes(job.bytes_total) }) +
          (job.speed ? ` · ${fmtBytes(job.speed)}/s · ${fmtEta(left)}` : "");
      } else {
        $(".s", el).textContent = (job.failed?.length ? t("{n} failed", { n: job.failed.length }) + " · " : "") + job.dest;
      }
    } else if (job.kind === "backup") {
      $(".n", el).textContent = job.title;
      $(".s", el).textContent = backupJobText(job);
    } else {
      $(".n", el).textContent = job.status === "verifying" ? t("Verifying IPSW…") : running ? t("Downloading IPSW") : job.status === "done" ? t("IPSW downloaded") : job.status === "cancelled" ? t("Cancelled") : t("Download error");
      $(".s", el).textContent = job.error || t("{done} of {total}", { done: fmtBytes(job.bytes_done), total: fmtBytes(job.bytes_total) }) + (job.speed ? ` · ${fmtBytes(job.speed)}/s` : "");
    }
    const a = $(".a", el);
    // rebuild the buttons only when the job changes state; replacing them on every poll
    // swallowed clicks that landed between two renders
    const mode = running ? "running" : job.status;
    if (a.dataset.mode === mode) continue;
    a.dataset.mode = mode;
    a.innerHTML = "";
    if (running) {
      const b = document.createElement("button"); b.className = "btn small ghost"; b.textContent = t("Cancel");
      b.onclick = async () => {
        b.disabled = true; b.textContent = t("Cancelling…");
        try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); }
        catch (e) { b.disabled = false; b.textContent = t("Cancel"); toast(e.message); }
      };
      a.appendChild(b);
    } else {
      if (job.kind === "backup" && job.status === "done" && job.action === "backup") {
        const o = document.createElement("button"); o.className = "btn small"; o.textContent = t("Open folder");
        o.onclick = () => api("/api/open-folder", { json: { path: state.backups?.root || "" } });
        a.appendChild(o);
      }
      if (job.kind === "photos" && job.status === "done") {
        const o = document.createElement("button"); o.className = "btn small"; o.textContent = t("Open folder");
        o.onclick = () => api("/api/open-folder", { json: { path: job.dest } });
        a.appendChild(o);
      }
      const c = document.createElement("button"); c.className = "btn small ghost"; c.textContent = t("Close");
      c.onclick = () => { state.jobs.delete(job.id); renderTray(); };
      a.appendChild(c);
    }
  }
  tray.hidden = tray.children.length === 0;
}

async function pollJobs() {
  for (const job of state.jobs.values()) {
    if (!["running", "verifying"].includes(job.status)) continue;
    try {
      const fresh = await api(`/api/jobs/${job.id}`);
      const wasRunning = job.status !== "done";
      Object.assign(job, fresh);
      if (job.kind === "ipsw" && wasRunning && job.status === "done" && state.view === "update") loadFirmwares();
      if (job.kind === "backup" && job.status !== "running") backupJobEnded(job);
    } catch {}
  }
  renderTray();
  if (state.view === "update") renderFwProgress();
}
setInterval(pollJobs, 800);

/* ---------------- apps ---------------- */

state.apps = [];
state.appsFor = null;

async function openApps(force = false) {
  const udid = state.udid;
  if (state.appsFor === udid && !force) { renderApps(); return; }
  state.appsFor = udid;
  $("#ap-status").textContent = t("Reading installed apps…");
  $("#ap-table tbody").innerHTML = "";
  let r;
  try {
    r = await api(`/api/devices/${udid}/apps${force ? "?refresh=true" : ""}`);
  } catch (e) {
    state.appsFor = null;
    $("#ap-status").textContent = t("Error reading the apps: {error}", { error: e.message });
    return;
  }
  if (state.udid !== udid) return;
  state.apps = r.apps;
  $("#ap-status").textContent = "";
  await loadBackups(true);
  renderApps();
  // sizes take the iPhone about a minute: fill them in when ready
  while (r.sizes === "computing" && state.appsFor === udid) {
    $("#ap-status").textContent = t("Calculating the size of each app (takes about 1 minute)…");
    await new Promise((res) => setTimeout(res, 3000));
    try { r = await api(`/api/devices/${udid}/apps`); } catch { break; }
  }
  if (state.appsFor !== udid) return;
  state.apps = r.apps;
  $("#ap-status").textContent = r.sizes === "error" ? t("Couldn't calculate the sizes.") : "";
  if (state.view === "apps") renderApps();
}

function renderApps() {
  const q = $("#ap-search").value.trim().toLowerCase();
  const [field, dir] = $("#ap-sort").value.split("-");
  const total = (a) => (a.app_size ?? 0) + (a.data_size ?? 0);
  const key = { name: (a) => a.name.toLowerCase(), total, data: (a) => a.data_size, app: (a) => a.app_size }[field];
  const list = state.apps.filter((a) => !q || a.name.toLowerCase().includes(q) || a.bundle.toLowerCase().includes(q));
  list.sort((a, b) => { const x = key(a), y = key(b); return (x < y ? -1 : x > y ? 1 : 0) * (dir === "asc" ? 1 : -1); });
  const sizesReady = state.apps.length && state.apps[0].app_size != null;
  $("#ap-count").textContent = t("{n} apps", { n: state.apps.length }) + (sizesReady ? ` · ${fmtBytes(state.apps.reduce((s, a) => s + total(a), 0))}` : "");
  const tbody = $("#ap-table tbody");
  tbody.innerHTML = "";
  for (const a of list) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="ico"><img alt="" loading="lazy"></td><td><div class="aname"></div><div class="muted small bundle"></div></td>
      <td class="muted"></td><td class="num"></td><td class="num"></td><td class="num"><b></b></td><td class="act"></td>`;
    const img = tr.querySelector("img");
    img.src = `/api/devices/${state.udid}/apps/${enc(a.bundle)}/icon`;
    img.onerror = () => img.remove();
    tr.querySelector(".aname").textContent = a.name;
    tr.querySelector(".bundle").textContent = a.bundle;
    tr.children[2].textContent = a.version || "";
    const sized = a.app_size != null;
    tr.children[3].textContent = sized ? fmtBytes(a.app_size) : "…";
    tr.children[4].textContent = sized ? fmtBytes(a.data_size) : "…";
    tr.children[5].firstChild.textContent = sized ? fmtBytes(total(a)) : "…";
    if (a.file_sharing) {
      const b = document.createElement("button");
      b.className = "btn small"; b.textContent = t("Files");
      b.onclick = () => { state.fsRoot = `app:${a.bundle}`; state.fsPath = "/"; showView("files"); };
      tr.lastChild.appendChild(b);
    }
    const bk = document.createElement("button");
    bk.className = "btn small"; bk.textContent = t("Back up");
    bk.title = t("Saves this app's data (documents, settings, chats…) to restore it later");
    bk.onclick = () => startBackup([`app:${a.bundle}`]);
    tr.lastChild.appendChild(bk);
    const snap = latestSnapshot((sn) => sn.kind === "app" && sn.bundle === a.bundle);
    if (snap) {
      const rs = document.createElement("button");
      rs.className = "btn small ghost"; rs.textContent = t("Restore…");
      rs.title = t("Backed up {date}", { date: fmtDate(snap.date) });
      rs.onclick = () => restoreSnapshot(snap);
      tr.lastChild.appendChild(rs);
    }
    const del = document.createElement("button");
    del.className = "btn small ghost danger"; del.textContent = t("Remove");
    del.onclick = () => uninstallApp(a, del);
    tr.lastChild.appendChild(del);
    tbody.appendChild(tr);
  }
}
async function uninstallApp(a, btn) {
  const body = a.data_size != null
    ? t("Deletes the app and {size} of data, like deleting it on the Home Screen. This can't be undone " +
      "(unless the data is in a backup or in the app's cloud).", { size: fmtBytes(a.data_size) })
    : t("Deletes the app and all its data, like deleting it on the Home Screen. This can't be undone " +
      "(unless the data is in a backup or in the app's cloud).");
  if (!await ask(t("Remove \"{name}\" from the iPhone?", { name: a.name }), body, t("Remove app"), true)) return;
  btn.disabled = true; btn.textContent = t("Removing…");
  try {
    await api(`/api/devices/${state.udid}/apps/${enc(a.bundle)}/uninstall`, { json: { confirm: true } });
    state.apps = state.apps.filter((x) => x.bundle !== a.bundle);
    toast(t("{name} removed.", { name: a.name }), false);
    renderApps();
  } catch (e) {
    toast(e.message);
    btn.disabled = false; btn.textContent = t("Remove");
  }
}
$("#ap-search").oninput = renderApps;
$("#ap-sort").onchange = renderApps;
$("#ap-refresh").onclick = () => openApps(true);

/* ---------------- backups ---------------- */

state.backups = null;
state.backupInfo = {};  // udid -> {will_encrypt}
const BK_ITEMS = [
  ["messages", "Messages", "💬"], ["contacts", "Contacts", "👤"], ["call_history", "Call history", "📞"],
  ["notes", "Notes", "📝"], ["calendar", "Calendar", "📅"], ["safari", "Safari bookmarks", "🔖"],
];
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" });
const latestSnapshot = (pred) => (state.backups?.snapshots || []).find((s) => s.udid === state.udid && pred(s));
const backupBusy = () => [...state.jobs.values()].some((j) => j.kind === "backup" && j.status === "running");

async function loadBackups(quiet = false) {
  try {
    state.backups = await api("/api/backups");
  } catch (e) {
    if (!quiet) $("#bk-status").textContent = t("Couldn't read the backups: {error}", { error: e.message });
  }
  // a backup started before the page was reloaded: show it in the tray again
  const job = state.backups?.job;
  if (job && job.status === "running" && !state.jobs.has(job.id)) { state.jobs.set(job.id, job); renderTray(); }
}

async function openBackups() {
  const udid = state.udid;
  $("#bk-status").textContent = t("Reading backups…");
  await loadBackups();
  if (state.udid !== udid) return;
  $("#bk-status").textContent = "";
  renderBackups();
  try { state.backupInfo[udid] = await api(`/api/devices/${udid}/backup/info`); } catch {}
  if (state.udid === udid && state.view === "backups") renderBackups();
}

function renderBackups() {
  const b = state.backups;
  if (!b) return;
  $("#bk-where").textContent = t("Saved in {path}", { path: b.root })
    + (b.free != null ? " · " + t("{size} free", { size: fmtBytes(b.free) }) : "");
  const mine = b.full.find((f) => f.udid === state.udid);
  const last = $("#bk-last");
  last.textContent = mine
    ? t("Last backup: {date} · iOS {ios} · {size}", { date: fmtDate(mine.date), ios: mine.ios || "?", size: fmtBytes(mine.size) })
      + (mine.encrypted ? " · " + t("encrypted") : "") + (mine.complete ? "" : " · " + t("didn't finish"))
    : t("This iPhone hasn't been backed up by this app yet.");
  last.classList.toggle("muted", !(mine && mine.complete));
  const busy = backupBusy();
  $("#bk-full").disabled = busy;
  $("#bk-full").textContent = mine ? t("Update backup") : t("Back up now");
  $("#bk-restore-full").disabled = busy || !b.full.some((f) => f.complete);
  const info = state.backupInfo[state.udid];
  $("#bk-encrypt").checked = !!info?.will_encrypt;
  $("#bk-encrypt").disabled = !info || busy;

  const items = $("#bk-items");
  items.innerHTML = "";
  for (const [key, name, emoji] of BK_ITEMS) {
    const row = document.createElement("div");
    row.className = "bk-item";
    row.innerHTML = `<span class="bk-emoji"></span><div class="grow"><div class="bk-name"></div><div class="muted small"></div></div>`;
    row.querySelector(".bk-emoji").textContent = emoji;
    row.querySelector(".bk-name").textContent = t(name);
    const snap = latestSnapshot((s) => s.kind === key);
    row.querySelector(".small").textContent = snap ? t("Backed up {date}", { date: fmtDate(snap.date) })
      : key === "call_history" && !info?.will_encrypt ? t("Only in encrypted backups") : t("Not backed up yet");
    const go = document.createElement("button");
    go.className = "btn small"; go.textContent = t("Back up"); go.disabled = busy;
    go.onclick = () => startBackup([key]);
    row.appendChild(go);
    if (snap) {
      const rs = document.createElement("button");
      rs.className = "btn small ghost"; rs.textContent = t("Restore…"); rs.disabled = busy;
      rs.onclick = () => restoreSnapshot(snap);
      row.appendChild(rs);
    }
    items.appendChild(row);
  }

  const tbody = $("#bk-table tbody");
  tbody.innerHTML = "";
  const rows = [
    ...b.full.map((f) => ({ ...f, label: t("Entire iPhone"), full: true })),
    ...b.snapshots.map((s) => ({ ...s, label: s.kind === "app" ? t("App: {name}", { name: s.name }) : t(s.name), full: false })),
  ];
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="muted"></td></tr>`;
    tbody.querySelector("td").textContent = t("No backups yet.");
  }
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><b></b><div class="muted small"></div></td><td></td><td></td><td class="num"></td><td class="act"></td>`;
    tr.querySelector("b").textContent = r.label;
    tr.querySelector("div").textContent = [r.encrypted ? t("encrypted") : "", r.full && !r.complete ? t("didn't finish") : "",
      !r.full && r.files != null ? t("{n} files", { n: r.files }) : ""].filter(Boolean).join(" · ");
    tr.children[1].textContent = `${(r.full ? r.name : r.device_name) || ""} · iOS ${r.ios || "?"}`;
    tr.children[2].textContent = fmtDate(r.date);
    tr.children[3].textContent = fmtBytes(r.size);
    const act = tr.lastChild;
    if (r.full ? r.complete : r.udid === state.udid) {
      const rs = document.createElement("button");
      rs.className = "btn small"; rs.textContent = t("Restore…"); rs.disabled = busy;
      rs.onclick = () => r.full ? restoreFull(r) : restoreSnapshot(r);
      act.appendChild(rs);
    }
    const del = document.createElement("button");
    del.className = "btn small ghost danger"; del.textContent = t("Delete"); del.disabled = busy;
    del.onclick = async () => {
      if (!await ask(t("Delete this backup?"), t("{what} from {date} ({size}) is deleted from this computer. The iPhone isn't touched.",
        { what: r.label, date: fmtDate(r.date), size: fmtBytes(r.size) }), t("Delete"), true)) return;
      try { await api("/api/backups/delete", { json: { path: r.path, confirm: true } }); await openBackups(); }
      catch (e) { toast(e.message); }
    };
    act.appendChild(del);
    tbody.appendChild(tr);
  }
}

// Password prompt for encrypted backups: resolves to the password, or null if cancelled
function askPassword(title, body, okLabel = t("Continue")) {
  const dlg = $("#dlg-pass");
  $("#pass-title").textContent = title;
  $("#pass-body").textContent = body;
  $("#pass-input").value = "";
  $("#pass-ok").textContent = okLabel;
  dlg.returnValue = "";
  dlg.showModal();
  $("#pass-input").focus();
  return new Promise((resolve) => {
    dlg.onclose = () => resolve(dlg.returnValue === "ok" && $("#pass-input").value ? $("#pass-input").value : null);
  });
}

// Enter submits the form with its first button (Cancel): make it mean Continue
$("#pass-input").onkeydown = (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("#dlg-pass").close("ok"); }
};

const askBackupPassword = () => askPassword(t("Backup password"),
  t("This iPhone encrypts its backups. Enter the backup password (the one chosen when encryption was turned on, " +
    "not the iPhone's passcode)."));

async function startBackup(items) {
  const udid = state.udid;
  if (!state.backupInfo[udid]) { try { state.backupInfo[udid] = await api(`/api/devices/${udid}/backup/info`); } catch {} }
  // partial backups read the (encrypted) backup's file list, which needs the password
  let password = "";
  if (items.length && state.backupInfo[udid]?.will_encrypt) {
    password = await askBackupPassword();
    if (password === null) return;
  }
  const first = !state.backups?.full.some((f) => f.udid === udid);
  try {
    const job = await api(`/api/devices/${udid}/backup`, { json: { items, password } });
    state.jobs.set(job.id, job);
    renderTray();
    if (state.view === "backups") renderBackups();
    toast(first ? t("Keep the iPhone connected and unlocked. The first backup can take a long time; the next ones only copy what changed.")
      : t("Keep the iPhone connected and unlocked until the backup finishes."), false);
  } catch (e) { toast(e.message); }
}

async function startRestore(payload, needsPassword, title, body) {
  if (!await ask(title, body, t("Restore"), true)) return;
  let password = "";
  if (needsPassword) {
    password = await askBackupPassword();
    if (password === null) return;
  }
  try {
    const job = await api(`/api/devices/${state.udid}/restore`, { json: { ...payload, password, confirm: true } });
    state.jobs.set(job.id, job);
    renderTray();
    if (state.view === "backups") renderBackups();
  } catch (e) { toast(e.message); }
}

function restoreFull(r) {
  const other = r.udid !== state.udid ? t(" (a backup of another iPhone: {name})", { name: r.name || r.udid }) : "";
  return startRestore({ source: r.udid }, r.encrypted, t("Restore the entire iPhone?"),
    t("Everything on {device} is replaced by the backup of {date}, iOS {ios}{other}. Anything newer on the iPhone is lost. " +
      "Turn off Find My first and keep the cable connected; the iPhone restarts at the end.",
      { device: current()?.name || "iPhone", date: fmtDate(r.date), ios: r.ios || "?", other }));
}

function restoreSnapshot(s) {
  const what = s.kind === "app" ? s.name : t(s.name);
  return startRestore({ snapshot: s.id }, s.encrypted || !!state.backupInfo[state.udid]?.will_encrypt,
    t("Restore {what} from {date}?", { what, date: fmtDate(s.date) }),
    t("{what} goes back to how it was on {date}; what changed in it since then is lost. Everything else stays as it is now: " +
      "the iPhone is backed up first, then restored with only this part replaced. Turn off Find My first and keep the cable " +
      "connected; the iPhone restarts at the end.", { what, date: fmtDate(s.date) }));
}

function backupJobText(job) {
  const pct = `${Math.floor(job.percent || 0)}%`;
  if (job.status === "running") {
    const phase = {
      start: t("Connecting…"), backup: t("Backing up the iPhone · {pct}", { pct }),
      snapshot: t("Saving the selected data…"), merge: t("Preparing the restore…"),
      restore: t("Restoring · {pct} · don't disconnect the iPhone", { pct }),
    }[job.phase] || "";
    return phase + (job.hint ? "\n" + job.hint : "");
  }
  if (job.status === "done") return job.action === "restore" ? t("Done. The iPhone is restarting to finish.") : t("Backup complete.");
  if (job.status === "cancelled") return t("Cancelled");
  return job.error || t("Error");
}

function backupJobEnded(job) {
  if (job.ended) return;
  job.ended = true;
  loadBackups(true).then(() => {
    if (state.view === "backups") renderBackups();
    if (state.view === "apps") renderApps();
  });
}

$("#bk-full").onclick = () => startBackup([]);
$("#bk-restore-full").onclick = () => {
  const list = (state.backups?.full || []).filter((f) => f.complete);
  const r = list.find((f) => f.udid === state.udid) || list[0];
  if (r) restoreFull(r);
};
$("#bk-open").onclick = () => state.backups && api("/api/open-folder", { json: { path: state.backups.root } });
$("#bk-change").onclick = async () => {
  const { path } = await api("/api/pick/folder", { method: "POST" });
  if (!path) return;
  try { await api("/api/backups/folder", { json: { path } }); await openBackups(); } catch (e) { toast(e.message); }
};
$("#bk-encrypt").onchange = async (e) => {
  const enable = e.target.checked;
  e.target.checked = !enable;  // until the iPhone confirms
  const pw = enable
    ? await askPassword(t("Encrypt backups"), t("Choose a password for this iPhone's backups. Keep it safe: without it, " +
        "encrypted backups can't be restored. The iPhone may ask for its passcode."), t("Turn on"))
    : await askPassword(t("Turn off encryption"), t("Enter the current backup password. The iPhone may ask for its passcode."),
        t("Turn off"));
  if (pw === null) return;
  e.target.disabled = true;
  try {
    state.backupInfo[state.udid] = await api(`/api/devices/${state.udid}/backup/encryption`, { json: { enable, password: pw } });
    toast(enable ? t("Backup encryption turned on.") : t("Backup encryption turned off."), false);
  } catch (err) { toast(err.message); }
  renderBackups();
};

/* ---------------- files ---------------- */

Object.assign(state, {
  fsRoot: "media", fsPath: "/", fsMode: "browse", fsEntries: [], fsShown: [], fsSel: new Set(), fsLimit: 500,
  fsScan: null, fsLoadedFor: null,
  fsSort: loadPref("fs-sort", "name-asc"), fsGroup: loadPref("fs-group", "none"),
});
$("#fs-sort").value = state.fsSort;
$("#fs-group").value = state.fsGroup;

const TYPE_GROUPS = [
  [t("Images"), /^(jpe?g|heic|heif|png|gif|webp|tiff?|dng|bmp)$/],
  [t("Videos"), /^(mov|mp4|m4v|3gp|avi|mkv)$/],
  [t("Audio"), /^(mp3|m4a|aac|wav|caf|aiff?|flac|m4r)$/],
  [t("Documents"), /^(pdf|docx?|xlsx?|pptx?|pages|numbers|key|txt|rtf|csv|md|epub)$/],
  [t("Archives"), /^(zip|rar|7z|gz|tar|ipa|dmg)$/],
  [t("Databases"), /^(sqlite|db|sqlite-wal|sqlite-shm|realm)$/],
];
const typeGroup = (e) => e.dir ? t("Folders") : (TYPE_GROUPS.find(([, re]) => re.test(e.ext)) || [t("Other")])[0];
const fsTime = (e) => (e.mtime || e.btime) * 1000;
function fsGroupKey(e) {
  if (state.fsGroup === "none") return "";
  if (state.fsGroup === "type") return typeGroup(e);
  return e.dir && state.fsMode === "browse" ? "0-folders" : dateGroupKey(fsTime(e), state.fsGroup);
}
function fsGroupLabel(k) {
  if (state.fsGroup === "type") return k;
  return k === "0-folders" ? t("Folders") : dateGroupLabel(k, state.fsGroup);
}

async function openFiles() {
  await fillRoots();
  const key = `${state.udid}|${state.fsRoot}|${state.fsPath}|${state.fsMode}`;
  if (state.fsLoadedFor !== key) loadFiles();
  else renderFiles();
}

async function fillRoots() {
  const sel = $("#fs-root");
  let apps = state.appsFor === state.udid ? state.apps : null;
  if (!apps) {
    try { apps = (await api(`/api/devices/${state.udid}/apps`)).apps; } catch { apps = []; }
  }
  sel.innerHTML = `<option value="media"></option>`;
  sel.options[0].textContent = t("iPhone (media: DCIM, Downloads, Books…)");
  for (const a of apps.filter((x) => x.file_sharing)) {
    const o = document.createElement("option");
    o.value = `app:${a.bundle}`; o.textContent = t("App: {name}", { name: a.name });
    sel.appendChild(o);
  }
  if (![...sel.options].some((o) => o.value === state.fsRoot)) state.fsRoot = "media";
  sel.value = state.fsRoot;
}

async function loadFiles() {
  const key = `${state.udid}|${state.fsRoot}|${state.fsPath}|${state.fsMode}`;
  state.fsLoadedFor = key;
  state.fsSel.clear();
  state.fsEntries = [];
  state.fsLimit = 500;
  if (state.fsScan) { api(`/api/fs/scans/${state.fsScan}/cancel`, { method: "POST" }).catch(() => {}); state.fsScan = null; }
  renderCrumbs();
  renderFiles();
  const status = $("#fs-status");
  try {
    if (state.fsMode === "browse") {
      status.textContent = t("Reading the folder…");
      const list = await api(`/api/devices/${state.udid}/fs/list?root=${enc(state.fsRoot)}&path=${enc(state.fsPath)}`);
      if (state.fsLoadedFor !== key) return;
      state.fsEntries = list;
      status.textContent = "";
    } else {
      const scan = await api(`/api/devices/${state.udid}/fs/scan`, { json: { root: state.fsRoot, path: state.fsPath } });
      state.fsScan = scan.id;
      while (true) {
        const st = await api(`/api/fs/scans/${scan.id}`);
        if (state.fsLoadedFor !== key) return;
        if (st.status === "error") throw new Error(st.error);
        if (st.status !== "scanning") break;
        status.innerHTML = `<span></span> <button class="btn small ghost"></button>`;
        status.firstChild.textContent = t("Searching for files… {n} files in {dirs} folders", { n: st.done, dirs: st.dirs });
        status.lastChild.textContent = t("Stop");
        status.lastChild.onclick = () => api(`/api/fs/scans/${scan.id}/cancel`, { method: "POST" });
        await new Promise((r) => setTimeout(r, 800));
      }
      const done = await api(`/api/fs/scans/${scan.id}?items=true`);
      if (state.fsLoadedFor !== key) return;
      state.fsEntries = done.items;
      state.fsScan = null;
      status.textContent = done.status === "cancelled" ? t("Search stopped: showing what was found so far.") : "";
    }
  } catch (e) {
    state.fsLoadedFor = null;
    status.textContent = t("Error: {error}", { error: e.message });
  }
  renderFiles();
}

function renderCrumbs() {
  const c = $("#fs-crumbs");
  c.innerHTML = "";
  const parts = state.fsPath.split("/").filter(Boolean);
  const add = (label, path) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => { state.fsPath = path; loadFiles(); };
    c.appendChild(b);
  };
  add($("#fs-root").selectedOptions[0]?.textContent.replace(/ \(.*/, "") || "iPhone", "/");
  parts.forEach((p, i) => { c.appendChild(document.createTextNode(" / ")); add(p, "/" + parts.slice(0, i + 1).join("/")); });
}

function renderFiles() {
  const q = $("#fs-search").value.trim().toLowerCase();
  const [field, dir] = state.fsSort.split("-");
  const sign = dir === "asc" ? 1 : -1;
  const keyOf = { name: (e) => e.name.toLowerCase(), size: (e) => e.size, date: fsTime, type: (e) => e.ext + " " + e.name.toLowerCase() }[field];
  let list = state.fsEntries.filter((e) => !q || e.name.toLowerCase().includes(q));
  list.sort((a, b) => {
    const ga = fsGroupKey(a), gb = fsGroupKey(b);
    if (ga !== gb) return state.fsGroup === "type" ? (ga < gb ? -1 : 1) : ga === "0-folders" ? -1 : gb === "0-folders" ? 1 : (ga < gb ? 1 : -1);
    if (state.fsGroup === "none" && a.dir !== b.dir) return a.dir ? -1 : 1;  // folders first
    const x = keyOf(a), y = keyOf(b);
    return (x < y ? -1 : x > y ? 1 : 0) * sign;
  });
  state.fsShown = list;
  const flat = state.fsMode === "flat";
  $("#fs-table").classList.toggle("flat", flat);
  const stats = new Map();
  for (const e of list) { const g = fsGroupKey(e), s = stats.get(g) || { n: 0, bytes: 0 }; s.n++; s.bytes += e.size; stats.set(g, s); }
  const tbody = $("#fs-table tbody");
  tbody.innerHTML = "";
  let lastGroup = null;
  for (const e of list.slice(0, state.fsLimit)) {
    const g = fsGroupKey(e);
    if (g && g !== lastGroup) {
      lastGroup = g;
      const s = stats.get(g);
      const tr = document.createElement("tr");
      tr.className = "grp";
      tr.innerHTML = `<td colspan="6"><b></b> <span class="muted"></span></td>`;
      tr.querySelector("b").textContent = fsGroupLabel(g);
      tr.querySelector("span").textContent = t("{n} items · {size}", { n: s.n, size: fmtBytes(s.bytes) });
      tbody.appendChild(tr);
    }
    const tr = document.createElement("tr");
    tr.className = e.dir ? "dir" : "";
    tr.innerHTML = `<td class="ck"><input type="checkbox"></td><td class="nm"><span class="fi"></span><a></a></td><td class="path-col muted small"></td>
      <td class="num"></td><td class="muted"></td><td class="muted"></td>`;
    const ck = tr.querySelector("input");
    ck.checked = state.fsSel.has(e.path);
    ck.onchange = () => { ck.checked ? state.fsSel.add(e.path) : state.fsSel.delete(e.path); updateFsSel(); };
    tr.querySelector(".fi").textContent = e.dir ? "📁" : "📄";
    const a = tr.querySelector("a");
    a.textContent = e.name;
    if (e.dir) {
      a.href = "#";
      a.onclick = (ev) => { ev.preventDefault(); state.fsPath = e.path; loadFiles(); };
    } else {
      a.href = fsFileUrl(e.path);
      a.target = "_blank";
      a.title = t("Open");
    }
    tr.children[2].textContent = posixDir(e.path);
    tr.children[3].textContent = e.dir ? "" : fmtBytes(e.size);
    tr.children[4].textContent = fsTime(e) ? new Date(fsTime(e)).toLocaleString(LOCALE) : "";
    tr.children[5].textContent = e.dir ? t("Folder") : (e.ext || "").toUpperCase();
    tbody.appendChild(tr);
  }
  $("#fs-more").hidden = list.length <= state.fsLimit;
  $("#fs-more").textContent = t("Show more ({n} left)", { n: list.length - state.fsLimit });
  if (!list.length && !$("#fs-status").textContent) $("#fs-status").textContent = state.fsEntries.length ? t("Nothing matches the filter.") : t("Empty folder.");
  updateFsSel();
}

const posixDir = (p) => p.slice(0, p.lastIndexOf("/")) || "/";
const fsFileUrl = (path, download = false) =>
  `/api/devices/${state.udid}/fs/file?root=${enc(state.fsRoot)}&path=${enc(path)}${download ? "&download=true" : ""}`;

function updateFsSel() {
  const sel = state.fsEntries.filter((e) => state.fsSel.has(e.path));
  const bytes = sel.reduce((s, e) => s + e.size, 0);
  $("#fs-selinfo").textContent = sel.length ? t("{n} selected · {size}", { n: sel.length, size: fmtBytes(bytes) }) : "";
  $("#fs-download").disabled = !sel.some((e) => !e.dir);
  $("#fs-delete").disabled = !sel.length;
  const shown = state.fsShown.slice(0, state.fsLimit);
  $("#fs-all").checked = shown.length > 0 && shown.every((e) => state.fsSel.has(e.path));
}

$("#fs-root").onchange = (e) => { state.fsRoot = e.target.value; state.fsPath = "/"; loadFiles(); };
$$("#fs-mode button").forEach((b) => (b.onclick = () => {
  $$("#fs-mode button").forEach((x) => x.classList.toggle("active", x === b));
  state.fsMode = b.dataset.m;
  if (state.fsMode === "flat" && state.fsSort.startsWith("name")) { state.fsSort = "size-desc"; $("#fs-sort").value = state.fsSort; }
  loadFiles();
}));
$("#fs-sort").onchange = (e) => { state.fsSort = e.target.value; savePref("fs-sort", state.fsSort); renderFiles(); };
$("#fs-group").onchange = (e) => { state.fsGroup = e.target.value; savePref("fs-group", state.fsGroup); renderFiles(); };
$("#fs-search").oninput = () => { state.fsLimit = 500; renderFiles(); };
$("#fs-more").onclick = () => { state.fsLimit += 1000; renderFiles(); };
$("#fs-refresh").onclick = () => loadFiles();
$("#fs-all").onchange = (e) => {
  state.fsShown.slice(0, state.fsLimit).forEach((x) => (e.target.checked ? state.fsSel.add(x.path) : state.fsSel.delete(x.path)));
  renderFiles();
};
$("#fs-download").onclick = () => {
  const sel = state.fsEntries.filter((e) => state.fsSel.has(e.path) && !e.dir);
  sel.forEach((e, i) => setTimeout(() => {
    const a = document.createElement("a");
    a.href = fsFileUrl(e.path, true); a.download = e.name;
    document.body.appendChild(a); a.click(); a.remove();
  }, i * 400));
};
$("#fs-delete").onclick = async () => {
  const sel = state.fsEntries.filter((e) => state.fsSel.has(e.path));
  const bytes = sel.reduce((s, e) => s + e.size, 0);
  const names = sel.slice(0, 8).map((e) => "• " + e.name + (e.dir ? " " + t("(folder and everything in it)") : "")).join("\n");
  if (!await ask(t("Delete {n} item(s) from the iPhone ({size})?", { n: sel.length, size: fmtBytes(bytes) }),
    `${t("This can't be undone.")}\n\n${names}${sel.length > 8 ? "\n…" : ""}`, t("Delete"), true)) return;
  try {
    const r = await api(`/api/devices/${state.udid}/fs/delete`, { json: { root: state.fsRoot, paths: sel.map((e) => e.path) } });
    const failed = new Set(r.failed);
    state.fsEntries = state.fsEntries.filter((e) => !state.fsSel.has(e.path) || failed.has(e.path));
    state.fsSel.clear();
    if (failed.size) toast(t("{n} item(s) couldn't be deleted.", { n: failed.size }));
    else toast(t("{n} item(s) deleted.", { n: sel.length }), false);
  } catch (e) { toast(e.message); }
  renderFiles();
};
$("#fs-mkdir").onclick = async () => {
  const name = prompt(t("New folder name:"));
  if (!name || /[\\/]/.test(name)) return;
  try {
    await api(`/api/devices/${state.udid}/fs/mkdir`, { json: { root: state.fsRoot, path: `${state.fsPath.replace(/\/$/, "")}/${name}` } });
    loadFiles();
  } catch (e) { toast(e.message); }
};
$("#fs-upload").onchange = async (e) => {
  const list = [...e.target.files];
  e.target.value = "";
  const folder = state.fsPath;
  for (const [i, f] of list.entries()) {
    toast(t("Uploading {i}/{n}: {name} ({size})…", { i: i + 1, n: list.length, name: f.name, size: fmtBytes(f.size) }), false);
    try {
      await api(`/api/devices/${state.udid}/fs/upload?root=${enc(state.fsRoot)}&path=${enc(folder)}&name=${enc(f.name)}`,
        { method: "POST", body: f, headers: { "Content-Type": "application/octet-stream" } });
    } catch (err) { toast(`${f.name}: ${err.message}`); }
  }
  if (list.length) { toast(t("{n} file(s) uploaded.", { n: list.length }), false); loadFiles(); }
};

/* ---------------- screen (live mirroring: video + touch + keyboard) ---------------- */

const scr = { gen: 0, url: null, infoTimer: null, pressed: false, keys: new Set(), chain: Promise.resolve() };

async function openScreen() {
  const gen = ++scr.gen;  // also cancels a start from an earlier visit
  $("#sc-wrap").hidden = true;
  $("#sc-save").hidden = $("#sc-stop").hidden = $("#sc-control-wrap").hidden = true;
  $("#sc-fps").textContent = "";
  showScreenSetup(t("Checking the iPhone…"), false);
  let st;
  try { st = await api(`/api/devices/${state.udid}/screen/status`); }
  catch (e) { if (gen === scr.gen) showScreenSetup(t("Error: {error}", { error: e.message }), false); return; }
  if (gen !== scr.gen) return;
  if (st.connection !== "USB") return showScreenSetup(t("Connect the iPhone with the USB cable to see the screen."), false);
  if (st.developer_mode === false) {
    return showScreenSetup(t("Turn on <b>Developer Mode</b> on the iPhone: Settings › Privacy & Security › Developer Mode. " +
      "The iPhone restarts and asks for confirmation. Then come back here."), false, true);
  }
  if (st.error) return showScreenSetup(t("Couldn't open the connection to the iPhone: {error}", { error: st.error }), false);
  if (!st.ready) {
    return showScreenSetup(t("To see the screen, the app needs to mount Apple's <b>developer image</b> on the iPhone " +
      "(a download of about 17 MB, personalized by Apple for this device). It changes nothing on the iPhone and " +
      "is removed by itself when it restarts."), !st.preparing, true);
  }
  showScreenSetup(t("Starting mirroring (a few seconds)…"), false);
  let r;
  try { r = await api(`/api/devices/${state.udid}/mirror/start`, { method: "POST" }); }
  catch (e) { if (gen === scr.gen) showScreenSetup(e.message, false); return; }
  if (gen !== scr.gen || state.view !== "screen") return;
  scr.url = r.url;
  $("#sc-setup").hidden = true;
  $("#sc-wrap").hidden = false;
  $("#sc-save").hidden = $("#sc-stop").hidden = $("#sc-control-wrap").hidden = false;
  $("#sc-img").src = `${scr.url}/mjpeg?t=${Date.now()}`;
  updateControlCursor();
  clearInterval(scr.infoTimer);
  scr.infoTimer = setInterval(async () => {
    if (gen !== scr.gen) return;
    try {
      const info = await (await fetch(`${scr.url}/info`)).json();
      $("#sc-fps").textContent = info.streaming ? t("{n} fps", { n: info.fps.toFixed(0) }) : t("reconnecting…");
    } catch { $("#sc-fps").textContent = t("no connection"); }
  }, 2000);
}

function showScreenSetup(html, canPrepare, isHtml = false) {
  $("#sc-setup").hidden = false;
  const msg = $("#sc-msg");
  if (isHtml) msg.innerHTML = html; else msg.textContent = html;
  $("#sc-prepare").hidden = !canPrepare;
}

// Leaving the view drops the MJPEG connection; the server stops the mirror after ~90 s unwatched.
function stopScreen() {
  scr.gen++;
  clearInterval(scr.infoTimer);
  releaseAllKeys();
  $("#sc-img").removeAttribute("src");
}

$("#sc-prepare").onclick = async () => {
  $("#sc-prepare").hidden = true;
  $("#sc-msg").textContent = t("Downloading and mounting the developer image… (this can take 1 to 2 minutes)");
  try {
    await api(`/api/devices/${state.udid}/screen/prepare`, { method: "POST" });
    openScreen();
  } catch (e) {
    showScreenSetup(t("Failed: {error}", { error: e.message }), true);
  }
};
$("#sc-stop").onclick = async () => {
  stopScreen();
  $("#sc-wrap").hidden = true;
  $("#sc-save").hidden = $("#sc-stop").hidden = $("#sc-control-wrap").hidden = true;
  $("#sc-fps").textContent = "";
  showScreenSetup(t("Mirroring stopped."), false);
  await api("/api/mirror/stop", { method: "POST" }).catch(() => {});
  const b = document.createElement("button");
  b.className = "btn primary"; b.textContent = t("View again"); b.onclick = openScreen;
  $("#sc-msg").append(" ", b);
};
$("#sc-save").onclick = () => window.open(`${scr.url}/frame.jpg?t=${Date.now()}`, "_blank");

/* input: everything goes to the mirror process, in order (a drag is contact… then release) */
const controlOn = () => $("#sc-control").checked && !$("#sc-wrap").hidden;
function updateControlCursor() { $("#sc-img").classList.toggle("control", $("#sc-control").checked); }
$("#sc-control").onchange = () => { updateControlCursor(); if (!controlOn()) releaseAllKeys(); };

function sendMirror(path, payload) {
  if (!scr.url) return scr.chain;
  const url = scr.url + path;
  // text/plain + no-cors: a "simple" request (no preflight); the mirror checks our Origin header
  scr.chain = scr.chain.then(() => fetch(url, {
    method: "POST", mode: "no-cors", headers: { "Content-Type": "text/plain" }, body: JSON.stringify(payload),
  }).catch(() => {}));
  return scr.chain;
}

function touchPoint(e) {
  const r = $("#sc-img").getBoundingClientRect();
  const clamp = (v) => Math.max(0, Math.min(65535, Math.round(v * 65535)));
  return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
}
let lastMove = 0;
$("#sc-img").addEventListener("pointerdown", (e) => {
  if (!controlOn() || e.button !== 0) return;
  e.preventDefault();
  $("#sc-img").setPointerCapture(e.pointerId);
  scr.pressed = true;
  sendMirror("/touch", { type: "contact", ...touchPoint(e) });
});
$("#sc-img").addEventListener("pointermove", (e) => {
  if (!scr.pressed) return;
  const now = performance.now();
  if (now - lastMove < 16) return;  // ~60 samples/s is plenty for a swipe
  lastMove = now;
  sendMirror("/touch", { type: "contact", ...touchPoint(e) });
});
const endTouch = (e) => {
  if (!scr.pressed) return;
  scr.pressed = false;
  sendMirror("/touch", { type: "release", ...touchPoint(e) });
};
$("#sc-img").addEventListener("pointerup", endTouch);
$("#sc-img").addEventListener("pointercancel", endTouch);
$("#sc-img").addEventListener("contextmenu", (e) => { if (controlOn()) e.preventDefault(); });

$$("[data-hw]").forEach((b) => (b.onclick = () => sendMirror("/button", { name: b.dataset.hw })));

// KeyboardEvent.code (physical key) -> HID keyboard usage, as pymobiledevice3's viewer does
const CODE_TO_HID = (() => {
  const m = {};
  for (let i = 0; i < 26; i++) m["Key" + String.fromCharCode(65 + i)] = 0x04 + i;
  for (let i = 1; i <= 9; i++) m["Digit" + i] = 0x1D + i;
  m.Digit0 = 0x27;
  Object.assign(m, {
    Enter: 0x28, NumpadEnter: 0x28, Escape: 0x29, Backspace: 0x2A, Tab: 0x2B, Space: 0x2C,
    Minus: 0x2D, Equal: 0x2E, BracketLeft: 0x2F, BracketRight: 0x30, Backslash: 0x31, Semicolon: 0x33,
    Quote: 0x34, Backquote: 0x35, Comma: 0x36, Period: 0x37, Slash: 0x38, CapsLock: 0x39, Delete: 0x4C,
    ArrowRight: 0x4F, ArrowLeft: 0x50, ArrowDown: 0x51, ArrowUp: 0x52,
    ShiftLeft: 0xE1, ShiftRight: 0xE5, ControlLeft: 0xE0, ControlRight: 0xE4,
    AltLeft: 0xE2, AltRight: 0xE6, MetaLeft: 0xE3, MetaRight: 0xE7,
  });
  return m;
})();
let overScreen = false;
$("#sc-img").addEventListener("pointerenter", () => (overScreen = true));
$("#sc-img").addEventListener("pointerleave", () => (overScreen = false));
const typingInField = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);

window.addEventListener("keydown", (e) => {
  if (state.view !== "screen" || !controlOn() || !overScreen || typingInField()) return;
  const u = CODE_TO_HID[e.code];
  if (u === undefined) return;
  e.preventDefault();
  if (!scr.keys.has(u)) { scr.keys.add(u); sendMirror("/key", { usages: [...scr.keys] }); }
});
window.addEventListener("keyup", (e) => {
  const u = CODE_TO_HID[e.code];
  if (u !== undefined && scr.keys.delete(u)) sendMirror("/key", { usages: [...scr.keys] });
});
window.addEventListener("blur", () => releaseAllKeys());
function releaseAllKeys() {
  if (scr.keys.size) { scr.keys.clear(); sendMirror("/key", { usages: [] }); }
}

// Text box -> key presses on a US layout (the iPhone sees an external US keyboard)
const SHIFTED = { "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
  "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";", "\"": "'", "~": "`", "<": ",", ">": ".", "?": "/" };
const PLAIN = { "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31, ";": 0x33, "'": 0x34, "`": 0x35,
  ",": 0x36, ".": 0x37, "/": 0x38, " ": 0x2C, "\n": 0x28, "\t": 0x2B };
function charToUsage(ch) {
  if (/[a-z]/.test(ch)) return [ch.charCodeAt(0) - 97 + 0x04, false];
  if (/[A-Z]/.test(ch)) return [ch.charCodeAt(0) - 65 + 0x04, true];
  if (/[1-9]/.test(ch)) return [0x1D + Number(ch), false];
  if (ch === "0") return [0x27, false];
  if (PLAIN[ch] !== undefined) return [PLAIN[ch], false];
  if (SHIFTED[ch]) return [charToUsage(SHIFTED[ch])[0], true];
  return null;
}
$("#sc-send-text").onclick = async () => {
  const text = $("#sc-text").value;
  const skipped = new Set();
  for (const ch of text) {
    const k = charToUsage(ch);
    if (!k) { skipped.add(ch); continue; }
    const [u, shift] = k;
    sendMirror("/key", { usages: shift ? [0xE1, u] : [u] });
    sendMirror("/key", { usages: [] });
  }
  await scr.chain;
  if (skipped.size) toast(t("Characters not sent (the iPhone keyboard is US English): {chars}", { chars: [...skipped].join(" ") }));
};

/* ---------------- update ---------------- */

let firmwares = [];
async function openUpdate() {
  const d = current();
  $("#up-current").textContent = `iOS ${d.ios}`;
  $("#up-ptype").textContent = d.product_type;
  loadFirmwares();
  pollUpdate();
}

async function loadFirmwares() {
  const list = $("#up-list");
  try {
    const r = await api(`/api/devices/${state.udid}/firmwares`);
    firmwares = r.firmwares;
  } catch (e) {
    list.innerHTML = `<div class="muted"></div>`;
    list.firstChild.textContent = e.message;
    return;
  }
  list.innerHTML = firmwares.length ? "" : `<div class="muted">${t("No signed versions found.")}</div>`;
  for (const fw of firmwares) {
    const row = document.createElement("div");
    row.className = "fw";
    row.dataset.url = fw.url;
    row.innerHTML = `<span class="v"></span><span class="muted b"></span><span class="tag ok">${t("signed")}</span><span class="tag st"></span><span class="spacer"></span><span class="acts"></span><div class="bar" hidden><div></div></div>`;
    $(".v", row).textContent = fw.version;
    $(".b", row).textContent = `${fw.build} · ${fmtBytes(fw.size)}${fw.released ? " · " + new Date(fw.released).toLocaleDateString(LOCALE) : ""}`;
    const st = $(".st", row);
    if (fw.is_current) { st.textContent = t("current version"); }
    else if (!fw.can_update) { st.textContent = t("older (would require a restore)"); st.classList.add("bad"); }
    else st.remove();
    const acts = $(".acts", row);
    if (fw.can_update) {
      if (fw.downloaded) {
        const u = document.createElement("button");
        u.className = "btn primary small"; u.textContent = fw.is_current ? t("Reinstall") : t("Update");
        u.onclick = () => confirmUpdate(fw.local_path, fw.version);
        acts.appendChild(u);
      } else {
        const b = document.createElement("button");
        b.className = "btn small"; b.textContent = t("Download IPSW");
        b.onclick = async () => {
          try {
            const job = await api("/api/ipsw/download", { json: { url: fw.url, size: fw.size, sha1: fw.sha1 } });
            job.kind = "ipsw";
            state.jobs.set(job.id, job);
            renderTray(); renderFwProgress();
          } catch (e) { toast(e.message); }
        };
        acts.appendChild(b);
      }
    }
    list.appendChild(row);
  }
  renderFwProgress();
}

function renderFwProgress() {
  for (const job of state.jobs.values()) {
    if (job.kind !== "ipsw") continue;
    const row = $(`.fw[data-url="${CSS.escape(job.url)}"]`);
    if (!row) continue;
    const running = job.status === "running" || job.status === "verifying";
    const bar = $(".bar", row);
    bar.hidden = !running;
    $(".bar > div", row).style.width = `${job.bytes_total ? (job.bytes_done / job.bytes_total) * 100 : 0}%`;
    const btn = $(".acts button", row);
    if (btn && running) { btn.disabled = true; btn.textContent = job.status === "verifying" ? t("Verifying…") : `${Math.floor((job.bytes_done / (job.bytes_total || 1)) * 100)}%`; }
  }
}

$("#up-browse").onclick = async () => {
  const r = await api("/api/pick/ipsw", { method: "POST" });
  if (r.path) { $("#up-path").value = r.path.replaceAll("/", "\\"); $("#up-check").click(); }
};
$("#up-check").onclick = async () => {
  const box = $("#up-inspect");
  const path = $("#up-path").value.trim().replace(/^"|"$/g, "");
  if (!path) return;
  box.innerHTML = `<div class="muted">${t("Verifying…")}</div>`;
  try {
    const r = await api(`/api/devices/${state.udid}/ipsw/inspect`, { json: { path } });
    const cur = current().ios;
    const newer = cmpVersion(r.version, cur) >= 0;
    const ok = r.compatible && r.signed !== false && newer;
    box.innerHTML = `<div class="fw"><span class="v"></span><span class="muted b"></span><span class="tag t1"></span><span class="tag t2"></span><span class="tag t3"></span><span class="spacer"></span><span class="acts"></span></div>`;
    $(".v", box).textContent = r.version;
    $(".b", box).textContent = `${r.build} · ${fmtBytes(r.size)}`;
    tag($(".t1", box), r.compatible, t("compatible"), t("not compatible with this device"));
    tag($(".t2", box), r.signed, t("signed by Apple"), r.signed === null ? t("signature not checked") : t("not signed"));
    tag($(".t3", box), newer, t("update"), t("older than the current one"));
    if (ok) {
      const u = document.createElement("button");
      u.className = "btn primary small"; u.textContent = t("Update");
      u.onclick = () => confirmUpdate(r.path, r.version);
      $(".acts", box).appendChild(u);
    }
  } catch (e) {
    box.innerHTML = `<div class="tag bad"></div>`;
    box.firstChild.textContent = e.message;
  }
};
function tag(el, good, yes, no) { el.textContent = good ? yes : no; el.classList.add(good ? "ok" : "bad"); }
function cmpVersion(a, b) {
  const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) { const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d; }
  return 0;
}

let pendingUpdate = null;
function confirmUpdate(path, version) {
  if (current()?.connection !== "USB") { toast(t("Connect the iPhone with the USB cable to update.")); return; }
  pendingUpdate = path;
  $("#cu-version").textContent = version;
  $("#cu-path").textContent = path;
  $("#dlg-update").showModal();
  runPreflight(path);
}

async function runPreflight(path) {
  const list = $("#cu-checks"), go = $("#cu-go");
  go.disabled = true;
  $("#cu-recheck").hidden = true;
  list.innerHTML = `<li class="muted">${t("Checking the IPSW and the iPhone…")}</li>`;
  let r;
  try {
    r = await api(`/api/devices/${state.udid}/update/preflight`, { json: { path } });
  } catch (e) {
    list.innerHTML = `<li class="bad"></li>`;
    list.firstChild.textContent = t("Couldn't check: {error}", { error: e.message });
    $("#cu-recheck").hidden = false;
    return;
  }
  if (pendingUpdate !== path) return;
  list.innerHTML = "";
  for (const c of r.checks) {
    const li = document.createElement("li");
    li.className = c.ok ? "ok" : "bad";
    li.innerHTML = `<span class="ic"></span><span class="t"></span><span class="d muted"></span>`;
    li.querySelector(".ic").textContent = c.ok ? "✓" : c.unknown ? "?" : "✗";
    li.querySelector(".t").textContent = c.label;
    li.querySelector(".d").textContent = c.detail || "";
    list.appendChild(li);
  }
  go.disabled = !r.ok;
  $("#cu-recheck").hidden = r.ok;
}
$("#cu-recheck").onclick = () => runPreflight(pendingUpdate);
$("#dlg-update").onclose = async () => {
  if ($("#dlg-update").returnValue !== "ok") return;
  try {
    state.updateLogLen = 0;
    $("#up-log").textContent = "";
    await api(`/api/devices/${state.udid}/update`, { json: { ipsw_path: pendingUpdate, confirm: true } });
    pollUpdate();
  } catch (e) { toast(e.message); }
};
$("#up-cancel").onclick = async () => {
  if (!await ask(t("Stop the update?"), t("Stopping can leave the iPhone in recovery mode."), t("Stop update"), true)) return;
  await api("/api/update/cancel", { method: "POST" });
};

state.updating = false;
state.updateOp = null;
let updateTimer = null;
async function pollUpdate() {
  clearTimeout(updateTimer);
  let r;
  try { r = await api(`/api/update?since=${state.updateLogLen}`); } catch { r = { status: "idle" }; }
  if (r.status === "idle") { $("#up-run").hidden = true; return; }
  $("#up-run").hidden = false;
  const log = $("#up-log");
  if (r.log.length) {
    // the last line is a progress bar that keeps repainting: drop the stale copy before appending
    const lines = log.textContent ? log.textContent.split("\n") : [];
    if (lines.length && lines[lines.length - 1].startsWith("⟳ ") && r.log[0].startsWith("⟳ ")) lines.pop();
    log.textContent = lines.concat(r.log).join("\n");
    log.scrollTop = log.scrollHeight;
    state.updateLogLen = r.log_len - (r.log[r.log.length - 1].startsWith("⟳ ") ? 1 : 0);
  }
  // the iPhone reports its own progress: "'Progress': 10, 'Operation': 'RESTORE_IMAGE'"
  for (const line of r.log) {
    const m = PROGRESS_RE.exec(line);
    const s = m ? null : /Sending (\S+) \(\d+ bytes\)/.exec(line);
    if (m) state.updateOp = { pct: +m[1], op: m[2] };
    else if (s) state.updateOp = { pct: -1, op: "SENDING", what: s[1] };
  }
  const op = state.updateOp;
  const pct = r.status === "done" ? 100 : op && op.pct >= 0 ? op.pct : 0;
  $("#up-bar").style.width = `${pct}%`;
  $("#up-progress-text").textContent =
    r.status === "running" ? (op ? `${opLabel(op)}${op.pct >= 0 ? ` — ${op.pct}%` : "…"}` : t("Preparing…")) :
    r.status === "done" ? t("Update complete! The iPhone will finish setting up and restart.") :
    t("The update failed (code {code}). See the log below.", { code: r.exit_code ?? "?" });
  $("#up-cancel").hidden = r.status !== "running";
  const wasUpdating = state.updating;
  state.updating = r.status === "running";
  if (state.updating !== wasUpdating) showView();
  if (r.status === "running") updateTimer = setTimeout(pollUpdate, 1000);
}

const PROGRESS_RE = /'Progress': (-?\d+), 'Operation': '([A-Z_]+)'/;
const OP_LABELS = {
  SENDING: t("Sending to the iPhone"),
  RESTORE_IMAGE: t("Copying the new iOS to the iPhone"),
  VERIFY_RESTORE: t("Verifying the copied system"),
  FLASH_FIRMWARE: t("Writing firmware"),
  UPDATE_BASEBAND: t("Updating the modem"),
  REQUESTING_FUD_DATA: t("Sending component firmware"),
  CREATE_FILESYSTEM: t("Preparing the system volume"),
  RESIZING_MAIN_FS_PARTITION: t("Preparing storage"),
  WAIT_FOR_NAND: t("Preparing storage"),
  UPDATE_NAND_FIRMWARE: t("Preparing storage"),
  MOUNT_FILESYSTEMS: t("Mounting volumes"),
  UNMOUNT_FILESYSTEMS: t("Unmounting volumes"),
  CHECK_FILESYSTEMS: t("Checking volumes"),
  CHECK_LOGS: t("Checking"),
};
function opLabel(op) {
  if (op.op === "SENDING") return t("Sending {what} to the iPhone", { what: op.what });
  if (OP_LABELS[op.op]) return OP_LABELS[op.op];
  if (op.op.startsWith("UPDATE_")) return t("Updating component ({name})", { name: op.op.slice(7).toLowerCase() });
  return op.op.toLowerCase().replaceAll("_", " ");
}

/* ---------------- boot ---------------- */

$("#btn-refresh").onclick = () => refreshDevices(true);
refreshDevices(true);
pollUpdate();  // an update may already be running (e.g. after reloading the page)
setInterval(() => { if (!document.hidden) refreshDevices(false); }, 4000);
