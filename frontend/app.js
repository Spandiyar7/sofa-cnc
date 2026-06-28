// Логика главного экрана: загрузка фото -> анализ -> параметры -> генерация.

const MATERIAL_THICKNESS = {
  "Фанера берёзовая": 18,
  "Фанера хвойная": 18,
  "Брус сосновый": 40,
  "ДСП 16мм": 16,
  "ДСП 22мм": 22,
  "MDF 16мм": 16,
};

let selectedFile = null;
let lastResult = null;

const $ = (id) => document.getElementById(id);

function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast"), 3200);
}

// ---------- Блок А: загрузка фото ----------
const dropzone = $("dropzone");
const fileInput = $("fileInput");

dropzone.addEventListener("click", () => fileInput.click());
["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
  })
);
dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", (e) => {
  if (e.target.files.length) setFile(e.target.files[0]);
});

function setFile(file) {
  if (!file.type.startsWith("image/")) {
    toast("Это не изображение", true);
    return;
  }
  selectedFile = file;
  const url = URL.createObjectURL(file);
  dropzone.innerHTML = `<img src="${url}" alt="превью"><p class="muted">${file.name}</p>`;
  $("analyzeBtn").disabled = false;
}

// ---------- Блок А: анализ ----------
$("analyzeBtn").addEventListener("click", async () => {
  if (!selectedFile) return;
  const btn = $("analyzeBtn");
  btn.disabled = true;
  $("analyzeStatus").innerHTML = '<span class="spinner"></span>Анализ через Claude Vision…';
  try {
    const fd = new FormData();
    fd.append("file", selectedFile);
    const res = await fetch("/api/analyze-photo", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    const cfg = await res.json();
    applyConfig(cfg);
    $("analyzeStatus").textContent = "Готово. Проверьте и при необходимости поправьте.";
  } catch (err) {
    toast("Ошибка анализа: " + err.message, true);
    $("analyzeStatus").textContent = "Не удалось — заполните тип вручную.";
    $("aiCard").classList.remove("hidden");
  } finally {
    btn.disabled = false;
  }
});

function applyConfig(cfg) {
  $("aiCard").classList.remove("hidden");
  $("f_type").value = cfg.type || "straight";
  $("f_armrests").value = cfg.armrests || "both";
  $("f_backrest").value = cfg.backrest || "straight";
  $("f_chaise").value = cfg.chaise ? "true" : "false";
  $("f_sections").value = cfg.sections || 1;
  $("f_legs").value = cfg.legs_visible ? "true" : "false";

  const badge = $("srcBadge");
  if (cfg._source === "manual") {
    badge.textContent = "Ручной режим";
    badge.className = "badge manual";
  } else {
    badge.textContent = "AI";
    badge.className = "badge";
  }
  $("aiNote").textContent = cfg._note || "";
  updateTypeFields();
}

// ---------- зависимые поля по типу ----------
function updateTypeFields() {
  const type = $("f_type").value;
  $("length2Wrap").style.display = (type === "corner_l" || type === "corner_u") ? "" : "none";
  $("length3Wrap").style.display = (type === "corner_u") ? "" : "none";
}
$("f_type").addEventListener("change", updateTypeFields);
updateTypeFields();

// ---------- материал -> толщина ----------
$("f_material").addEventListener("change", () => {
  const t = MATERIAL_THICKNESS[$("f_material").value];
  if (t) $("f_thickness").value = t;
});

// ---------- сбор запроса ----------
function buildRequest() {
  return {
    sofa_type: $("f_type").value,
    armrests: $("f_armrests").value,
    backrest: $("f_backrest").value,
    chaise: $("f_chaise").value === "true",
    sections: parseInt($("f_sections").value) || 1,
    legs: $("f_legs").value === "true",
    length: parseFloat($("f_length").value),
    length2: parseFloat($("f_length2").value) || null,
    length3: parseFloat($("f_length3").value) || null,
    depth: parseFloat($("f_depth").value),
    height_back: parseFloat($("f_height_back").value),
    height_seat: parseFloat($("f_height_seat").value),
    material: $("f_material").value,
    thickness: parseFloat($("f_thickness").value),
    joint: $("f_joint").value,
    gcode: $("f_gcode").checked,
    tool_diameter: parseFloat($("f_tool").value),
    spindle_rpm: parseInt($("f_rpm").value) || 18000,
    feed_xy: parseFloat($("f_feed_xy").value),
    feed_z: parseFloat($("f_feed_z").value),
    pass_depth: parseFloat($("f_pass").value),
    tab_height: parseFloat($("f_tab_h").value),
    tab_width: parseFloat($("f_tab_w").value),
  };
}

// показывать/скрывать поля ЧПУ по чекбоксу
$("f_gcode").addEventListener("change", () => {
  $("camFields").style.opacity = $("f_gcode").checked ? "1" : "0.4";
  $("camFields").style.pointerEvents = $("f_gcode").checked ? "" : "none";
});

// ---------- Блок Г: генерация ----------
$("generateBtn").addEventListener("click", async () => {
  const btn = $("generateBtn");
  btn.disabled = true;
  $("genStatus").innerHTML = '<span class="spinner"></span>Генерация чертежей…';
  try {
    const req = buildRequest();
    const res = await fetch("/api/generate-drawings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || res.statusText);
    }
    lastResult = await res.json();
    lastResult._request = req;
    renderResult(lastResult);
    $("genStatus").textContent = "Готово.";
    $("resultCard").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    toast("Ошибка генерации: " + err.message, true);
    $("genStatus").textContent = "Ошибка: " + err.message;
  } finally {
    btn.disabled = false;
  }
});

function renderResult(r) {
  $("resultCard").classList.remove("hidden");
  $("r_unique").textContent = r.unique_parts;
  $("r_total").textContent = r.parts_count;
  $("r_sheets").textContent = r.sheets;
  $("r_util").textContent = r.utilization + "%";

  const cam = r.cam_totals || { holes: 0, cut_len_m: 0, est_min: 0 };
  $("r_holes").textContent = cam.holes || 0;
  $("r_cut").textContent = (cam.cut_len_m || 0) + " м";
  $("r_time").textContent = cam.est_min || 0;
  document.querySelectorAll(".stat.cam").forEach((e) => {
    e.style.display = r.gcode_enabled ? "" : "none";
  });

  $("oversizeWarn").textContent = (r.oversize && r.oversize.length)
    ? "⚠ Не поместились на лист: " + r.oversize.join(", ")
    : "";

  $("solidNote").textContent = (r.solid_parts && r.solid_parts.length)
    ? "🪵 Из массива/бруса (не идёт на листовой раскрой, делается отдельно): " +
      r.solid_parts.map((s) => `${s.name} ×${s.qty} (${s.size})`).join(", ")
    : "";

  const tbody = $("partsTable").querySelector("tbody");
  tbody.innerHTML = "";
  r.parts.forEach((p) => {
    const tr = document.createElement("tr");
    const stockBadge = p.stock === "лист"
      ? '<span class="badge">лист</span>'
      : '<span class="badge manual">массив/брус</span>';
    tr.innerHTML = `<td>${p.block}</td><td>${p.name}</td><td>${p.qty}</td>` +
      `<td>${p.length} × ${p.width} × ${p.thickness}</td><td>${p.material}</td><td>${stockBadge}</td>`;
    tbody.appendChild(tr);
  });

  const prev = $("previews");
  prev.innerHTML = "";
  r.previews.forEach((p) => {
    const div = document.createElement("div");
    div.className = "preview-item";
    div.innerHTML = p.svg + `<div class="cap">${p.name}</div>`;
    prev.appendChild(div);
  });

  $("cutmap").innerHTML = r.cutmap_svg;

  // G-code: симуляция траекторий по листам
  const gs = $("gcodeSection");
  const bp = $("backplots");
  if (r.gcode_enabled && r.gcode_sheets && r.gcode_sheets.length) {
    gs.style.display = "";
    bp.innerHTML = "";
    r.gcode_sheets.forEach((s) => {
      const div = document.createElement("div");
      div.className = "preview-item";
      div.innerHTML = s.svg +
        `<div class="cap">Лист ${s.sheet} · ${s.stats.holes} отв · ` +
        `${s.stats.cut_len_m} м реза · ~${s.stats.est_min} мин</div>`;
      bp.appendChild(div);
    });
  } else {
    gs.style.display = "none";
  }
}

// ---------- скачать ZIP ----------
$("downloadBtn").addEventListener("click", () => {
  if (!lastResult) return;
  window.location = "/api/download/" + lastResult.download_id;
});

// ---------- сохранить заказ ----------
$("saveBtn").addEventListener("click", async () => {
  if (!lastResult) return;
  try {
    const res = await fetch("/api/save-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request: lastResult._request,
        config: {
          type: $("f_type").value,
          armrests: $("f_armrests").value,
          backrest: $("f_backrest").value,
        },
        sheets: lastResult.sheets,
        parts_count: lastResult.parts_count,
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    toast("Заказ #" + data.id + " сохранён в историю");
  } catch (err) {
    toast("Не удалось сохранить: " + err.message, true);
  }
});

// ---------- дублирование заказа (?duplicate=<id>) ----------
(async function maybeDuplicate() {
  const params = new URLSearchParams(location.search);
  const dupId = params.get("duplicate");
  if (!dupId) return;
  try {
    const res = await fetch("/api/orders/" + dupId);
    if (!res.ok) return;
    const o = await res.json();
    if (o.config) applyConfig({ ...o.config, _source: "manual" });
    const p = o.params || {};
    const set = (id, v) => { if (v != null) $(id).value = v; };
    set("f_type", p.sofa_type);
    set("f_length", p.length);
    set("f_length2", p.length2);
    set("f_length3", p.length3);
    set("f_depth", p.depth);
    set("f_height_back", p.height_back);
    set("f_height_seat", p.height_seat);
    set("f_material", p.material);
    set("f_thickness", p.thickness);
    set("f_joint", p.joint);
    updateTypeFields();
    toast("Параметры заказа #" + dupId + " подставлены");
  } catch (e) { /* игнорируем */ }
})();
