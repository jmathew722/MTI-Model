/* DWG-native frontend logic. Polls the single-worker job queue; draws the EXACT
   extracted geometry on a 2D canvas (proof the extraction landed) and the built
   STL in a Three.js viewer; fills the inspection report + title block. */
"use strict";

const STAGES = ["queued", "importing", "extracting", "mapping", "building", "verifying", "done"];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
let pollTimer = null, currentJob = null, geometryDrawn = false, part3d = null;

/* ---- theme ------------------------------------------------------------- */
const root = document.documentElement;
(function initTheme() {
  const saved = localStorage.getItem("dwg-theme");
  if (saved) root.setAttribute("data-theme", saved);
  else if (matchMedia("(prefers-color-scheme: dark)").matches) root.setAttribute("data-theme", "dark");
})();
document.getElementById("themeToggle").onclick = () => {
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  localStorage.setItem("dwg-theme", next);
  if (currentJob && currentJob.artifacts && currentJob.artifacts.raw_extraction) drawGeometry(lastGeometry);
};

/* ---- tabs -------------------------------------------------------------- */
document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.setAttribute("aria-selected", "false"));
    t.setAttribute("aria-selected", "true");
    document.querySelectorAll(".panel").forEach((p) => (p.dataset.active = "false"));
    document.getElementById("panel-" + t.dataset.tab).dataset.active = "true";
  };
});

/* ---- samples ----------------------------------------------------------- */
async function loadSamples() {
  const sel = document.getElementById("sample");
  try {
    const r = await fetch("/api/dwg/samples");
    const d = await r.json();
    sel.innerHTML = '<option value="">— select a DWG —</option>';
    (d.samples || []).forEach((s) => {
      const o = document.createElement("option");
      o.value = s.path; o.textContent = s.name; sel.appendChild(o);
    });
  } catch (e) { /* offline / no samples */ }
}
document.getElementById("refreshBtn").onclick = loadSamples;
document.getElementById("sample").onchange = (e) => {
  document.getElementById("runBtn").disabled = !e.target.value;
};

/* ---- run --------------------------------------------------------------- */
document.getElementById("runBtn").onclick = async () => {
  const path = document.getElementById("sample").value;
  if (!path) return;
  resetRun();
  const fd = new FormData();
  fd.append("server_path", path);
  const r = await fetch("/api/dwg/jobs", { method: "POST", body: fd });
  const d = await r.json();
  if (!r.ok) { setDetail("submit failed: " + (d.detail || r.status), true); return; }
  pollJob(d.job_id);
};

function resetRun() {
  geometryDrawn = false; lastGeometry = null;
  document.getElementById("drawEmpty").style.display = "grid";
  document.getElementById("partEmpty").style.display = "grid";
  renderStages("queued", false);
}

function pollJob(id) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const r = await fetch("/api/dwg/jobs/" + id);
    if (!r.ok) return;
    const job = await r.json(); currentJob = job;
    renderStages(job.status, job.status === "failed");
    setDetail(job.stage_detail || job.status + (job.error ? " — " + job.error : ""),
              job.status === "failed");
    // draw the exact geometry as soon as extraction produced it
    if (!geometryDrawn && ["extracting","mapping","building","verifying","done","failed"].includes(job.status)) {
      const g = await (await fetch("/api/dwg/jobs/" + id + "/geometry")).json();
      if (g && g.views && g.views.length) { lastGeometry = g; drawGeometry(g); geometryDrawn = true; }
    }
    if (job.status === "done" || job.status === "failed") {
      clearInterval(pollTimer); pollTimer = null;
      finishJob(job);
    }
  }, 700);
}

function setDetail(txt, err) {
  const el = document.getElementById("jobDetail");
  el.innerHTML = err ? '<span class="err">' + txt + "</span>" : txt;
}

/* ---- stage tracker (dimensioned traveler) ------------------------------ */
function renderStages(status, failed) {
  const idx = STAGES.indexOf(status === "failed" ? "verifying" : status);
  const host = document.getElementById("stages"); host.innerHTML = "";
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

/* ---- 2D drawing canvas: draw EXACT extracted geometry ------------------ */
let lastGeometry = null;
function drawGeometry(data) {
  if (!data) return;
  const canvas = document.getElementById("drawingCanvas");
  const wrap = canvas.parentElement;
  const W = canvas.width = wrap.clientWidth, H = canvas.height = 360;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, W, H);
  document.getElementById("drawEmpty").style.display = "none";

  const geo = [], txt = [];
  data.views.forEach((v) => { (v.geometry||[]).forEach(g=>geo.push(g)); (v.text_tokens||[]).forEach(t=>txt.push(t)); });
  // bounds
  let minX=1e9,minY=1e9,maxX=-1e9,maxY=-1e9;
  const acc=(x,y)=>{minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);};
  geo.forEach(g=>{ if(g.start_2d_m)acc(...g.start_2d_m); if(g.end_2d_m)acc(...g.end_2d_m);
    if(g.center_2d_m){acc(g.center_2d_m[0]-(g.radius_m||0),g.center_2d_m[1]-(g.radius_m||0));
      acc(g.center_2d_m[0]+(g.radius_m||0),g.center_2d_m[1]+(g.radius_m||0));} });
  if(minX>maxX){document.getElementById("drawEmpty").style.display="grid";return;}
  const pad=24, sx=(W-2*pad)/((maxX-minX)||1), sy=(H-2*pad)/((maxY-minY)||1), s=Math.min(sx,sy);
  const tx=(x)=>pad+(x-minX)*s, ty=(y)=>H-(pad+(y-minY)*s);   // flip Y (drawing +Y up)

  const ink=css("--ink")||"#222", cyan=css("--cyan")||"#1f6f8b", muted=css("--ink-3")||"#789";
  ctx.lineWidth=1; ctx.strokeStyle=ink; ctx.fillStyle=cyan;
  geo.forEach(g=>{
    if(g.type==="line"&&g.start_2d_m&&g.end_2d_m){
      ctx.beginPath();ctx.moveTo(tx(g.start_2d_m[0]),ty(g.start_2d_m[1]));
      ctx.lineTo(tx(g.end_2d_m[0]),ty(g.end_2d_m[1]));ctx.stroke();
    } else if((g.type==="circle"||g.type==="arc")&&g.center_2d_m&&g.radius_m){
      ctx.beginPath();ctx.strokeStyle=g.type==="circle"?cyan:ink;
      ctx.arc(tx(g.center_2d_m[0]),ty(g.center_2d_m[1]),Math.max(1,g.radius_m*s),0,2*Math.PI);
      ctx.stroke();ctx.strokeStyle=ink;
    }
  });
  // text token anchors (the MTEXT the numbers come from)
  ctx.fillStyle=muted;
  txt.forEach(t=>{ if(!t.position_2d_m)return; ctx.beginPath();
    ctx.arc(tx(t.position_2d_m[0]),ty(t.position_2d_m[1]),1.6,0,2*Math.PI);ctx.fill();});
  document.getElementById("drawStat").textContent =
    geo.length+" ent · "+txt.length+" text";
}

/* ---- finish: report + 3D + title block --------------------------------- */
function finishJob(job) {
  const res = job.result || {};
  const v = res.verification || {};
  // title block
  document.getElementById("tbPart").textContent = res.part || job.dwg_path.split(/[\\/]/).pop();
  document.getElementById("tbHoles").textContent = (res.hole_count ?? "—");
  document.getElementById("tbProfile").textContent = res.profile_found ? "closed loop" : "none";
  const units = (lastGeometry && lastGeometry.units_detected) || "inch";
  document.getElementById("tbUnits").textContent = units;
  const ready = job.status === "done" && v.passed;
  const tbs = document.getElementById("tbStamp");
  tbs.className = "tb-stamp " + (ready ? "ready" : (job.status==="failed"||v.passed===false ? "notready":"pending"));
  tbs.textContent = ready ? "✓ READY" : (job.status==="failed" ? "✗ NOT READY" : "— pending —");

  // inspection stamp
  const stamp = document.getElementById("stamp");
  if (ready) { stamp.className="stamp"; stamp.innerHTML='<span class="big">PASS</span><span class="small">all gates</span>'; }
  else { stamp.className="stamp fail"; stamp.innerHTML='<span class="big">FAIL</span><span class="small">'+(job.failed_check||"blocked")+'</span>'; }
  document.getElementById("inspSub").textContent =
    (res.part||"") + " · " + (v.checks?v.checks.length:0) + " gates · " +
    (job.status==="failed" ? "failing: "+(job.failed_check||"conflict") : "verified");

  renderGates(v.checks || []);
  renderRoundtrip(v.checks || []);
  renderCorrections(res.ocr_correction || {});
  renderConflicts(res.conflicts || []);
  renderArtifacts(job);

  // 3D part
  if (job.artifacts && job.artifacts.stl) loadSTL("/api/dwg/jobs/" + job.id + "/artifact/stl");
}

function renderGates(checks) {
  const host = document.getElementById("gates"); host.innerHTML = "";
  if (!checks.length) { host.innerHTML='<div class="empty-note">No gates (build did not complete).</div>'; return; }
  checks.forEach(c=>{
    const el=document.createElement("div"); el.className="gate "+(c.status==="PASS"?"pass":"fail");
    el.innerHTML='<div class="g-name">'+c.check.replace(/_/g," ")+'</div>'+
      '<div class="g-status">'+(c.status==="PASS"?"✓ PASS":"✗ FAIL")+'</div>'+
      '<div class="g-detail">'+(c.detail||"")+'</div>';
    host.appendChild(el);
  });
}

function renderRoundtrip(checks) {
  const tb=document.querySelector("#roundtrip tbody");
  const rt=checks.find(c=>c.check==="dimension_roundtrip");
  if(!rt){tb.innerHTML='<tr><td colspan="4" class="empty-note">—</td></tr>';return;}
  tb.innerHTML='<tr><td>base L/W/T</td><td>'+fmt(rt.expected)+'</td><td>'+fmt(rt.measured)+
    '</td><td class="'+(rt.status==="PASS"?"confirmed":"corrected")+'">'+rt.status+'</td></tr>';
}
const fmt=(v)=>Array.isArray(v)?v.map(x=>(+x).toFixed(4)).join(", "):(v==null?"—":v);

function renderCorrections(corr) {
  const tb=document.querySelector("#corrections tbody");
  const rows=[]; (corr.confirmations||[]).forEach(c=>rows.push([c.vision_field,c.vision_value,c.dwg_value,"0","confirmed"]));
  (corr.corrections||[]).forEach(c=>rows.push([c.vision_field,c.vision_value,c.dwg_value,c.delta,"corrected"]));
  if(!rows.length){ tb.innerHTML='<tr><td colspan="5" class="empty-note">'+
    (corr.note||"Standalone run — DWG values are authoritative.")+'</td></tr>'; return; }
  tb.innerHTML=rows.map(r=>'<tr><td>'+r[0]+'</td><td>'+r[1]+'</td><td>'+r[2]+'</td><td>'+r[3]+
    '</td><td class="'+(r[4]==="corrected"?"corrected":"confirmed")+'">'+
    '<span class="tag '+(r[4]==="corrected"?"red":"cyan")+'">'+r[4]+'</span></td></tr>').join("");
}

function renderConflicts(conflicts) {
  const host=document.getElementById("conflicts");
  if(!conflicts.length){host.innerHTML='<div class="empty-note">No conflicts.</div>';return;}
  host.innerHTML=conflicts.map(c=>'<div class="job-detail"><span class="tag '+
    (c.blocking?"red":"amber")+'">'+(c.severity||"")+(c.blocking?" · BLOCKING":"")+'</span> '+
    c.detail+'</div>').join("");
}

function renderArtifacts(job) {
  const host=document.getElementById("artifacts"); const a=job.artifacts||{};
  const names=[["raw_extraction","raw_extraction.json"],["build_plan","build_plan.json"],
    ["ocr_correction","ocr_correction.json"],["verification","verification_report.json"],
    ["macro_vba","build.vba"],["stl",".STL"],["sldprt",".SLDPRT"]];
  const links=names.filter(n=>a[n[0]]).map(n=>'<a href="/api/dwg/jobs/'+job.id+'/artifact/'+n[0]+'">'+n[1]+'</a>');
  host.innerHTML=links.length?links.join(""):'<span class="empty-note">—</span>';
}

/* ---- Three.js STL viewer ---------------------------------------------- */
function loadSTL(url) {
  const host=document.getElementById("partView");
  document.getElementById("partEmpty").style.display="none";
  host.innerHTML=""; const W=host.clientWidth||400, H=360;
  const scene=new THREE.Scene();
  const camera=new THREE.PerspectiveCamera(45,W/H,0.001,100);
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
  renderer.setSize(W,H); renderer.setPixelRatio(devicePixelRatio||1); host.appendChild(renderer.domElement);
  scene.add(new THREE.AmbientLight(0xffffff,0.7));
  const dir=new THREE.DirectionalLight(0xffffff,0.7); dir.position.set(1,1,1); scene.add(dir);
  const controls=new THREE.OrbitControls(camera,renderer.domElement); controls.enableDamping=true;
  new THREE.STLLoader().load(url,(geom)=>{
    geom.computeBoundingBox(); const bb=geom.boundingBox, c=new THREE.Vector3();
    bb.getCenter(c); geom.translate(-c.x,-c.y,-c.z);
    const size=bb.getSize(new THREE.Vector3()); const m=Math.max(size.x,size.y,size.z)||1;
    const col=getComputedStyle(document.documentElement).getPropertyValue("--cyan").trim()||"#1f6f8b";
    const mat=new THREE.MeshStandardMaterial({color:new THREE.Color(col),metalness:0.1,roughness:0.7});
    const mesh=new THREE.Mesh(geom,mat); scene.add(mesh);
    camera.position.set(m*1.6,m*1.4,m*2.0); camera.lookAt(0,0,0);
    part3d={renderer,scene,camera,controls};
    (function anim(){ if(!part3d)return; requestAnimationFrame(anim); controls.update(); renderer.render(scene,camera); })();
    document.getElementById("partStat").textContent=".STL loaded";
  }, undefined, ()=>{ document.getElementById("partEmpty").style.display="grid";
    document.getElementById("partEmpty").textContent="STL unavailable"; });
}

loadSamples();
renderStages("queued", false);
