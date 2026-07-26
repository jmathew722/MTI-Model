/* DWG-native frontend logic. Polls the single-worker job queue; shows the
   SolidWorks PDF (or exact-geometry canvas), the built STL, the inspection
   report, the title block, and the extracted-dimensions tables.
   ASCII-only source (glyphs via HTML entities) so a stray quote/char can never
   break the parse again. All wiring runs after DOMContentLoaded. */
"use strict";

(function () {
  const STAGES = ["queued", "importing", "extracting", "mapping", "building", "verifying", "done"];
  const $ = (id) => document.getElementById(id);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const root = document.documentElement;

  let pollTimer = null, currentJob = null, geometryDrawn = false, pdfShown = false;
  let part3d = null, lastGeometry = null;

  /* ---- theme ---------------------------------------------------------- */
  (function initTheme() {
    const saved = localStorage.getItem("dwg-theme");
    if (saved) root.setAttribute("data-theme", saved);
    else if (matchMedia("(prefers-color-scheme: dark)").matches) root.setAttribute("data-theme", "dark");
  })();
  $("themeToggle").onclick = () => {
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    localStorage.setItem("dwg-theme", next);
    if (!pdfShown && lastGeometry) drawGeometry(lastGeometry);
  };

  /* ---- tabs ----------------------------------------------------------- */
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.setAttribute("aria-selected", "false"));
      t.setAttribute("aria-selected", "true");
      document.querySelectorAll(".panel").forEach((p) => (p.dataset.active = "false"));
      $("panel-" + t.dataset.tab).dataset.active = "true";
    };
  });

  /* ---- samples + file input ------------------------------------------ */
  async function loadSamples() {
    const sel = $("sample");
    try {
      const d = await (await fetch("/api/dwg/samples")).json();
      sel.innerHTML = '<option value="">-- select a DWG --</option>';
      (d.samples || []).forEach((s) => {
        const o = document.createElement("option");
        o.value = s.path; o.textContent = s.name; sel.appendChild(o);
      });
    } catch (e) { /* offline / no samples */ }
  }
  $("refreshBtn").onclick = loadSamples;

  function syncRunEnabled() {
    const hasFile = $("fileInput").files.length > 0;
    const hasSample = !!$("sample").value;
    $("runBtn").disabled = !(hasFile || hasSample);
  }
  $("sample").onchange = () => {
    if ($("sample").value) $("fileInput").value = "";
    syncRunEnabled();
  };
  $("fileInput").onchange = () => {
    if ($("fileInput").files.length) $("sample").value = "";
    syncRunEnabled();
  };

  /* ---- run: local upload OR server sample ----------------------------- */
  $("runBtn").onclick = async () => {
    const file = $("fileInput").files[0];
    const path = $("sample").value;
    if (!file && !path) return;
    resetRun();
    const fd = new FormData();
    if (file) { fd.append("file", file); setDetail("uploading " + file.name + " ..."); }
    else fd.append("server_path", path);
    try {
      const r = await fetch("/api/dwg/jobs", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) { setDetail("submit failed: " + (d.detail || r.status), true); return; }
      pollJob(d.job_id);
    } catch (e) {
      setDetail("submit error: " + e.message, true);
    }
  };

  function resetRun() {
    geometryDrawn = false; lastGeometry = null; pdfShown = false;
    $("drawEmpty").style.display = "grid";
    $("pdfView").style.display = "none";
    $("drawingCanvas").style.display = "none";
    $("partEmpty").style.display = "grid";
    renderStages("queued", false);
  }

  function pollJob(id) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      let job;
      try { job = await (await fetch("/api/dwg/jobs/" + id)).json(); }
      catch (e) { return; }
      currentJob = job;
      renderStages(job.status, job.status === "failed");
      setDetail(job.stage_detail || (job.status + (job.error ? " - " + job.error : "")),
                job.status === "failed");
      if (!pdfShown && job.artifacts && job.artifacts.sheet_pdf) {
        const f = $("pdfView");
        f.src = "/api/dwg/jobs/" + id + "/sheet.pdf";
        f.style.display = "block";
        $("drawEmpty").style.display = "none";
        $("drawingCanvas").style.display = "none";
        $("drawStat").textContent = "PDF from SolidWorks";
        pdfShown = true;
      }
      if (!geometryDrawn && ["extracting", "mapping", "building", "verifying", "done", "failed"].includes(job.status)) {
        try {
          const g = await (await fetch("/api/dwg/jobs/" + id + "/geometry")).json();
          if (g && g.views && g.views.length) {
            lastGeometry = g;
            if (!pdfShown) { $("drawingCanvas").style.display = "block"; drawGeometry(g); }
            geometryDrawn = true;
          }
        } catch (e) { /* keep polling */ }
      }
      if (job.status === "done" || job.status === "failed") {
        clearInterval(pollTimer); pollTimer = null;
        finishJob(job);
      }
    }, 700);
  }

  function setDetail(txt, err) {
    $("jobDetail").innerHTML = err ? '<span class="err">' + escapeHtml(txt) + "</span>" : escapeHtml(txt);
  }

  /* ---- stage tracker -------------------------------------------------- */
  function renderStages(status, failed) {
    const idx = STAGES.indexOf(status === "failed" ? "verifying" : status);
    const host = $("stages"); host.innerHTML = "";
    STAGES.forEach((s, i) => {
      const node = document.createElement("div"); node.className = "stage-node";
      if (i < idx || status === "done") node.classList.add("done");
      if (i === idx && status !== "done") node.classList.add(failed ? "failed" : "active");
      node.innerHTML = '<span class="dot"></span><span class="lbl">' + s + "</span>";
      host.appendChild(node);
      if (i < STAGES.length - 1) {
        const l = document.createElement("span"); l.className = "leader"; host.appendChild(l);
      }
    });
  }

  /* ---- 2D geometry canvas (fallback when no PDF) ---------------------- */
  function drawGeometry(data) {
    if (!data) return;
    const canvas = $("drawingCanvas");
    const wrap = canvas.parentElement;
    const W = canvas.width = wrap.clientWidth, H = canvas.height = 360;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    $("drawEmpty").style.display = "none";

    const geo = [], txt = [];
    data.views.forEach((v) => {
      (v.geometry || []).forEach((g) => geo.push(g));
      (v.text_tokens || []).forEach((t) => txt.push(t));
    });
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    const acc = (x, y) => { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); };
    geo.forEach((g) => {
      if (g.start_2d_m) acc(g.start_2d_m[0], g.start_2d_m[1]);
      if (g.end_2d_m) acc(g.end_2d_m[0], g.end_2d_m[1]);
      if (g.center_2d_m) {
        const rr = g.radius_m || 0;
        acc(g.center_2d_m[0] - rr, g.center_2d_m[1] - rr);
        acc(g.center_2d_m[0] + rr, g.center_2d_m[1] + rr);
      }
    });
    if (minX > maxX) { $("drawEmpty").style.display = "grid"; return; }
    const pad = 24, s = Math.min((W - 2 * pad) / ((maxX - minX) || 1), (H - 2 * pad) / ((maxY - minY) || 1));
    const tx = (x) => pad + (x - minX) * s, ty = (y) => H - (pad + (y - minY) * s);

    const ink = css("--ink") || "#222", cyan = css("--cyan") || "#1f6f8b", muted = css("--ink-3") || "#789";
    ctx.lineWidth = 1; ctx.strokeStyle = ink;
    geo.forEach((g) => {
      if (g.type === "line" && g.start_2d_m && g.end_2d_m) {
        ctx.beginPath(); ctx.moveTo(tx(g.start_2d_m[0]), ty(g.start_2d_m[1]));
        ctx.lineTo(tx(g.end_2d_m[0]), ty(g.end_2d_m[1])); ctx.stroke();
      } else if ((g.type === "circle" || g.type === "arc") && g.center_2d_m && g.radius_m) {
        ctx.beginPath(); ctx.strokeStyle = g.type === "circle" ? cyan : ink;
        ctx.arc(tx(g.center_2d_m[0]), ty(g.center_2d_m[1]), Math.max(1, g.radius_m * s), 0, 2 * Math.PI);
        ctx.stroke(); ctx.strokeStyle = ink;
      }
    });
    ctx.fillStyle = muted;
    txt.forEach((t) => {
      if (!t.position_2d_m) return;
      ctx.beginPath(); ctx.arc(tx(t.position_2d_m[0]), ty(t.position_2d_m[1]), 1.6, 0, 2 * Math.PI); ctx.fill();
    });
    $("drawStat").textContent = geo.length + " ent / " + txt.length + " text";
  }

  /* ---- finish: report + title block + 3D ------------------------------ */
  function finishJob(job) {
    const res = job.result || {};
    const v = res.verification || {};
    $("tbPart").textContent = res.part || (job.dwg_path || "").split(/[\\/]/).pop();
    $("tbHoles").textContent = (res.hole_count == null ? "-" : res.hole_count);
    $("tbProfile").textContent = res.profile_found ? "closed loop" : "none";
    $("tbUnits").textContent = (lastGeometry && lastGeometry.units_detected) || "inch";

    const ready = job.status === "done" && v.passed;
    const tbs = $("tbStamp");
    tbs.className = "tb-stamp " + (ready ? "ready" : (job.status === "failed" || v.passed === false ? "notready" : "pending"));
    tbs.textContent = ready ? "READY" : (job.status === "failed" ? "NOT READY" : "pending");

    const stamp = $("stamp");
    if (ready) { stamp.className = "stamp"; stamp.innerHTML = '<span class="big">PASS</span><span class="small">all gates</span>'; }
    else { stamp.className = "stamp fail"; stamp.innerHTML = '<span class="big">FAIL</span><span class="small">' + escapeHtml(job.failed_check || "blocked") + '</span>'; }
    $("inspSub").textContent = (res.part || "") + " - " + (v.checks ? v.checks.length : 0) + " gates - " +
      (job.status === "failed" ? "failing: " + (job.failed_check || "conflict") : "verified");

    renderGates(v.checks || []);
    renderRoundtrip(v.checks || []);
    renderCorrections(res.ocr_correction || {});
    renderConflicts(res.conflicts || []);
    renderArtifacts(job);
    loadDimensions(job.id);
    if (job.artifacts && job.artifacts.stl) loadSTL("/api/dwg/jobs/" + job.id + "/artifact/stl");
  }

  function renderGates(checks) {
    const host = $("gates"); host.innerHTML = "";
    if (!checks.length) { host.innerHTML = '<div class="empty-note">No gates (build did not complete).</div>'; return; }
    checks.forEach((c) => {
      const el = document.createElement("div"); el.className = "gate " + (c.status === "PASS" ? "pass" : "fail");
      el.innerHTML = '<div class="g-name">' + escapeHtml(c.check.replace(/_/g, " ")) + "</div>" +
        '<div class="g-status">' + (c.status === "PASS" ? "&#10003; PASS" : "&#10007; FAIL") + "</div>" +
        '<div class="g-detail">' + escapeHtml(c.detail || "") + "</div>";
      host.appendChild(el);
    });
  }

  const fmt = (v) => Array.isArray(v) ? v.map((x) => (+x).toFixed(4)).join(", ") : (v == null ? "-" : v);
  function renderRoundtrip(checks) {
    const tb = document.querySelector("#roundtrip tbody");
    const rt = checks.find((c) => c.check === "dimension_roundtrip");
    if (!rt) { tb.innerHTML = '<tr><td colspan="4" class="empty-note">-</td></tr>'; return; }
    tb.innerHTML = "<tr><td>base L/W/T</td><td>" + fmt(rt.expected) + "</td><td>" + fmt(rt.measured) +
      '</td><td class="' + (rt.status === "PASS" ? "confirmed" : "corrected") + '">' + rt.status + "</td></tr>";
  }

  function renderCorrections(corr) {
    const tb = document.querySelector("#corrections tbody");
    const rows = [];
    (corr.confirmations || []).forEach((c) => rows.push([c.vision_field, c.vision_value, c.dwg_value, "0", "confirmed"]));
    (corr.corrections || []).forEach((c) => rows.push([c.vision_field, c.vision_value, c.dwg_value, c.delta, "corrected"]));
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="5" class="empty-note">' +
        escapeHtml(corr.note || "Standalone run - DWG values are authoritative.") + "</td></tr>";
      return;
    }
    tb.innerHTML = rows.map((r) =>
      "<tr><td>" + escapeHtml(String(r[0])) + "</td><td>" + r[1] + "</td><td>" + r[2] + "</td><td>" + r[3] +
      '</td><td><span class="tag ' + (r[4] === "corrected" ? "red" : "cyan") + '">' + r[4] + "</span></td></tr>").join("");
  }

  function renderConflicts(conflicts) {
    const host = $("conflicts");
    if (!conflicts.length) { host.innerHTML = '<div class="empty-note">No conflicts.</div>'; return; }
    host.innerHTML = conflicts.map((c) =>
      '<div class="job-detail"><span class="tag ' + (c.blocking ? "red" : "amber") + '">' +
      escapeHtml(c.severity || "") + (c.blocking ? " - BLOCKING" : "") + "</span> " +
      escapeHtml(c.detail || "") + "</div>").join("");
  }

  function renderArtifacts(job) {
    const host = $("artifacts"); const a = job.artifacts || {};
    const names = [["raw_extraction", "raw_extraction.json"], ["build_plan", "build_plan.json"],
      ["ocr_correction", "ocr_correction.json"], ["verification", "verification_report.json"],
      ["sheet_pdf", "drawing.pdf"], ["macro_vba", "build.vba"], ["stl", "model.STL"], ["sldprt", "model.SLDPRT"]];
    const links = names.filter((n) => a[n[0]]).map((n) =>
      '<a href="/api/dwg/jobs/' + job.id + "/artifact/" + n[0] + '">' + n[1] + "</a>");
    host.innerHTML = links.length ? links.join("") : '<span class="empty-note">-</span>';
  }

  /* ---- extracted-dimensions section ----------------------------------- */
  async function loadDimensions(id) {
    let d;
    try { d = await (await fetch("/api/dwg/jobs/" + id + "/dimensions")).json(); }
    catch (e) { return; }
    $("dimsCount").textContent = "- " + (d.n_geometry || 0) + " geometry - " + (d.n_extracted || 0) + " text";

    const g = d.geometry_dimensions || [];
    document.querySelector("#geomDims tbody").innerHTML = g.length ? g.map((r) => {
      const val = Array.isArray(r.value)
        ? "(" + r.value.map((x) => (+x).toFixed(3)).join(", ") + ")"
        : (r.value == null ? "-" : (+r.value).toFixed(3));
      return "<tr><td>" + escapeHtml(String(r.feature || "")) + "</td><td>" + escapeHtml(String(r.dimension)) +
        '</td><td class="confirmed">' + val + " " + escapeHtml(String(r.units || "")) +
        '</td><td style="color:var(--ink-3)">' + escapeHtml(String(r.provenance || "")) + "</td></tr>";
    }).join("") : '<tr><td colspan="4" class="empty-note">none</td></tr>';

    const t = d.extracted_text || [];
    document.querySelector("#textDims tbody").innerHTML = t.length ? t.map((r) => {
      const tags = [r.typ ? "TYP" : "", r.thru ? "THRU" : "", r.deep ? "DP" : "",
        r.count ? r.count + "x" : ""].filter(Boolean).join(" ");
      const pos = "(" + r.x_in + ", " + r.y_in + ")";
      return "<tr><td>" + escapeHtml(String(r.text || "")) + "</td><td>" +
        (r.value == null ? "-" : r.value) + '</td><td style="color:var(--ink-3)">' +
        escapeHtml(String(r.kind)) + (tags ? " " + tags : "") +
        '</td><td class="mono">' + pos + "</td></tr>";
    }).join("") : '<tr><td colspan="4" class="empty-note">none</td></tr>';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  /* ---- Three.js STL viewer -------------------------------------------- */
  function loadSTL(url) {
    const host = $("partView");
    $("partEmpty").style.display = "none";
    host.innerHTML = ""; const W = host.clientWidth || 400, H = 360;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, W / H, 0.001, 100);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(W, H); renderer.setPixelRatio(devicePixelRatio || 1); host.appendChild(renderer.domElement);
    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dir = new THREE.DirectionalLight(0xffffff, 0.7); dir.position.set(1, 1, 1); scene.add(dir);
    const controls = new THREE.OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
    new THREE.STLLoader().load(url, (geom) => {
      geom.computeBoundingBox(); const bb = geom.boundingBox, c = new THREE.Vector3();
      bb.getCenter(c); geom.translate(-c.x, -c.y, -c.z);
      const size = bb.getSize(new THREE.Vector3()); const m = Math.max(size.x, size.y, size.z) || 1;
      const col = css("--cyan") || "#1f6f8b";
      const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ color: new THREE.Color(col), metalness: 0.1, roughness: 0.7 }));
      scene.add(mesh);
      camera.position.set(m * 1.6, m * 1.4, m * 2.0); camera.lookAt(0, 0, 0);
      part3d = { renderer, scene, camera, controls };
      (function anim() { if (!part3d) return; requestAnimationFrame(anim); controls.update(); renderer.render(scene, camera); })();
      $("partStat").textContent = "STL loaded";
    }, undefined, () => {
      $("partEmpty").style.display = "grid"; $("partEmpty").textContent = "STL unavailable";
    });
  }

  /* ---- init ----------------------------------------------------------- */
  loadSamples();
  renderStages("queued", false);
})();
