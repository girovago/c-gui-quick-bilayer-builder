<script>
  import { onMount } from "svelte";
  import { LIPID_CATALOG, LIPID_CATEGORIES } from "./lipidCatalog.js";

  const { onCreate, onSubmit, buildLog, buildMetrics, onLogClear, localBusy: propLocalBusy, buildProgress = null, onPick = null, onToast = null, onSpecChange = null } = $props();

  const COMMON = new Set(["POPC","CHOL","CHL1","POPE","DPPC","DSPC","DOPS","DOTAP","SM","PSM","DOPC","DOPE","DMPC","DLPC","DPPS","POPS","POPA","POPG","POPI"]);

  const STEP_LIVE = {
    prepare: "Preparazione della struttura…",
    pdb2gmx: "Sto generando la topologia CHARMM36…",
    editconf: "Sto impostando il box…",
    solvate: "Sto solvatando la membrana…",
    genion: "Sto aggiungendo gli ioni…",
    grompp: "Sto preparando l'input MD (grompp)…",
    metrics: "Sto calcolando le metriche…",
    complete: "Completato",
  };
  function liveLabel(id) { return STEP_LIVE[id] ?? id; }
  const TYPE_COLORS = { lipid: "#3b82f6", protein: "#ef4444", dna: "#10b981", rna: "#8b5cf6", "small-mol": "#f59e0b" };

  const CAT_COLORS = { PC: "#3b82f6", PE: "#10b981", PS: "#ec4899", PA: "#f59e0b", PG: "#8b5cf6", "PI / Fosfoinositidi": "#06b6d4", Cardiolipine: "#dc2626", Steroli: "#ef4444", Sfingolipidi: "#14b8a6", Ceramidi: "#a855f7", "Acidi grassi": "#84cc16", Detergenti: "#78716c", Altri: "#a1a1aa" };
  const STEPS = ["Tipo", "Composizione", "Ambiente", "Riepilogo"];

  let step = $state(1), maxVisited = $state(1), buildType = $state("lipid");
  let symmetric = $state(true), upperCounts = $state({}), lowerCounts = $state({}), activeSide = $state("upper");
  let search = $state(""), catFilter = $state("");
  let membraneFiles = null, receptorFiles = null;
  let embedErr = $state(""), embedBusy = $state(false);
  let existingPdbs = $state([]);
  let selectedExistingMembrane = $state("");
  let selectedExistingReceptor = $state("");
  let proteinPdbId = $state(""), proteinPdbFile = null, dnaSequence = $state(""), rnaSequence = $state(""), smallMolFile = null;
  let boxX = $state(12), boxY = $state(12), boxZ = $state(12), centerStructure = $state(true), solvent = $state("water"), ions = $state("KCl");

  async function loadPdbs() {
    try {
      const res = await fetch("/api/pdbs");
      if (res.ok) existingPdbs = await res.json();
    } catch { /* silenzioso */ }
  }

  onMount(() => {
    loadPdbs();
  });

  const PRESETS = { plasma: { label: "Plasma (CHOL 35%)", upper: { POPC: 14, POPE: 6, PSM: 8, CHOL: 20, POPS: 4, POPI: 2, DOPS: 2 }, lower: { POPC: 14, POPE: 6, PSM: 8, CHOL: 20, POPS: 4, POPI: 2, DOPS: 2 } }, er: { label: "ER (PE alto)", upper: { POPC: 22, POPE: 18, CHOL: 3, POPI: 6, POPS: 3 }, lower: { POPC: 22, POPE: 18, CHOL: 3, POPI: 6, POPS: 3 } }, mito: { label: "Mitocondri (TOCL)", upper: { POPC: 18, POPE: 24, TOCL1: 4, POPI: 3, POPA: 3 }, lower: { POPC: 18, POPE: 24, TOCL1: 4, POPI: 3, POPA: 3 } }, popc: { label: "POPC puro", upper: { POPC: 64 }, lower: { POPC: 64 } } };
  const chargeColor = $derived(buildMetrics ? (buildMetrics.net_lipid_charge_e !== 0 ? "text-red-600" : "text-zinc-600") : "text-zinc-600");

  function applyPreset(k) { const p = PRESETS[k]; if (!p) return; symmetric = true; upperCounts = { ...p.upper }; lowerCounts = { ...p.lower }; }
  function onSymChange() { if (!symmetric && totalCounts(lowerCounts) === 0) lowerCounts = { ...upperCounts }; }
  function goto(n) { if (n <= maxVisited) step = n; }
  function next() { if (stepValid()) { step = Math.min(step + 1, 4); maxVisited = Math.max(maxVisited, step); } }
  function back() { step = Math.max(step - 1, 1); }
  function counts() { return symmetric ? upperCounts : lowerCounts; }
  function setCounts(n) { if (symmetric) upperCounts = n; else lowerCounts = n; }
  function countOf(id) { return counts()[id] || 0; }
  function setQty(id, n) {
    const clean = Math.max(0, Math.min(4096, Math.round(n || 0)));
    const next = { ...counts() };
    if (clean <= 0) { delete next[id]; setCounts(next); return; }
    next[id] = clean;
    setCounts(next);
  }
  // il campo quantità VUOTO non elimina il lipide (richiesta Giorgio 12/11):
  // si cancella per riscrivere; su blur il numero ripristina la quantità
  function qtyInput(id, e) {
    if (e.target.value === "") return; // niente setQty: la riga resta
    setQty(id, +e.target.value);
  }
  function qtyBlur(id, e) {
    if (e.target.value === "") e.target.value = countOf(id);
  }
  function add(id) { setQty(id, countOf(id) + 1); }
  function removeOne(id) { setQty(id, countOf(id) - 1); }
  function remove(id) { const n = { ...counts() }; delete n[id]; setCounts(n); }
  function pickedList(side) { const c = side === "lower" && !symmetric ? lowerCounts : upperCounts; return Object.entries(c).filter(([, v]) => v > 0); }
  function pick(id) { if (countOf(id) === 0) add(id); }

  async function embedReceptor() {
    embedErr = ""; embedBusy = true;
    try {
      let mName, rName;
      const mf = membraneFiles?.[0], rf = receptorFiles?.[0];
      if (mf && rf) {
        // 1) Salva entrambi i file sul server
        async function upload(file) {
          const text = await file.text();
          const res = await fetch("/api/upload-pdb", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ file_name: file.name, content: text }),
          });
          if (!res.ok) {
            const d = await res.json().catch(() => ({}));
            throw new Error(d.detail || `upload fallito ${res.status}`);
          }
          const d = await res.json();
          return d.file_name;
        }
        mName = await upload(mf);
        rName = await upload(rf);
      } else if (selectedExistingMembrane && selectedExistingReceptor) {
        mName = selectedExistingMembrane;
        rName = selectedExistingReceptor;
      } else {
        embedErr = "Scegli due file (upload o tendina PDB esistenti)";
        return;
      }

      // 2) Chiama embed-in-membrane
      const res = await fetch("/api/embed-in-membrane", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          membrane_pdb_file_name: mName,
          receptor_pdb_file_name: rName,
          chain: "R",
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        embedErr = d.detail || `HTTP ${res.status}`;
        return;
      }
      const d = await res.json();
      // feedback reale + selezione del job (flash?.() era dead code: flash
      // non esiste nel Launcher). onPick/onToast arrivano da App, come per BatchPanel
      embedErr = "";
      onToast?.("ok", `Recettore inserito: job ${d.jobid}`);
      onPick?.(d.jobid ?? d.job?.jobid);
    } catch (e) {
      embedErr = e.message;
    } finally {
      embedBusy = false;
    }
  }
  function totalCounts(c) { return Object.values(c).reduce((s, v) => s + v, 0); }

  // SpecSketch (colonna centrale) legge form di App: senza questo sync lo
  // schema mostrava sempre il blank default (POPC=64) anche se il wizard
  // componeva altro — falso proprio mentre dichiara "live dalla spec"
  $effect(() => {
    if (!onSpecChange) return;
    const u = specString(upperCounts) || null;
    const l = symmetric ? u : (specString(lowerCounts) || null);
    onSpecChange({ upper: u, lower: l });
  });
  const ALIAS_LIPID = { CHOL: "CHL1" };
  function specString(c) {
    return Object.entries(c)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${ALIAS_LIPID[k] ?? k}=${v}`)
      .join(";");
  }
  function filtered() { const q = search.trim().toUpperCase(); let list = LIPID_CATALOG; if (catFilter) list = list.filter(l => l.cat === catFilter); if (q) list = list.filter(l => l.id.includes(q)); else if (!catFilter) list = list.filter(l => COMMON.has(l.id)); return list.slice(0, 90); }
  function filteredTotal() { const q = search.trim().toUpperCase(); let list = LIPID_CATALOG; if (catFilter) list = list.filter(l => l.cat === catFilter); if (q) list = list.filter(l => l.id.includes(q)); else if (!catFilter) list = list.filter(l => COMMON.has(l.id)); return list.length; }
  const hasSearchOrFilter = $derived(!!search.trim() || !!catFilter);

  function stepValid() {
    if (step === 2) {
      if (buildType === "lipid") { if (totalCounts(upperCounts) === 0) return false; if (!symmetric && totalCounts(lowerCounts) === 0) return false; return true; }
      if (buildType === "protein") return proteinPdbId.trim().length === 4 || !!proteinPdbFile;
      if (buildType === "dna") return dnaSequence.trim().length > 0;
      if (buildType === "rna") return rnaSequence.trim().length > 0;
      if (buildType === "small-mol") return !!smallMolFile;
    }
    return true;
  }
  function params() { return { boxX, boxY, boxZ, centerStructure, layout: "fluid", solvent, ions, buildType, upper: specString(upperCounts) || null, lower: symmetric ? specString(upperCounts) : specString(lowerCounts), proteinPdbId, proteinPdbFileName: proteinPdbFile?.name || null, dnaSequence, rnaSequence, smallMolFileName: smallMolFile?.name || null }; }
  function saveJson() { const d = { buildType, upper: upperCounts, lower: symmetric ? upperCounts : lowerCounts, symmetric, boxX, boxY, boxZ, centerStructure, solvent, ions, proteinPdbId, dnaSequence, rnaSequence, created: new Date().toISOString() }; const b = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }); const u = URL.createObjectURL(b); const a = document.createElement("a"); a.href = u; a.download = `cgui_composizione_${new Date().toISOString().slice(0,16)}.json`; a.click(); URL.revokeObjectURL(u); }
  function loadJson(e) { const f = e.target.files?.[0]; if (!f) return; const r = new FileReader(); r.onload = (ev) => { try { const d = JSON.parse(String(ev.target.result)); buildType = d.buildType ?? buildType; upperCounts = d.upper ?? {}; lowerCounts = d.lower ?? {}; symmetric = d.symmetric ?? true; boxX = d.boxX ?? boxX; boxY = d.boxY ?? boxY; boxZ = d.boxZ ?? boxZ; centerStructure = d.centerStructure ?? centerStructure; solvent = d.solvent ?? solvent; ions = d.ions ?? ions; proteinPdbId = d.proteinPdbId ?? ""; dnaSequence = d.dnaSequence ?? ""; rnaSequence = d.rnaSequence ?? ""; step = 4; maxVisited = 4; } catch { window.alert("JSON non valido"); } }; r.readAsText(f); }
  function create() { if (!stepValid()) return; const p = params(); if (buildType === "lipid") { if (onCreate) onCreate(p.upper || "POPC=64", p); } else { if (onSubmit) onSubmit(p.upper || "POPC=64", p); } }
  function submit() { if (!stepValid()) return; const p = params(); if (onSubmit) onSubmit(p.upper || "POPC=64", p); }
</script>
<div class="flex flex-col gap-4">
  {#if buildProgress}
    <div
      class="rounded-lg border p-3 mb-4 flex flex-col gap-2
             {buildProgress.error ? 'border-red-200 bg-red-50' : 'border-cyan-200 bg-cyan-50'}"
    >
      <div class="flex items-center justify-between mb-1">
        <span class="text-xs font-semibold text-zinc-700">Avanzamento build</span>
        {#if buildProgress.done}
          <span class="text-xs font-bold text-emerald-700">Completato in {buildProgress.elapsed_s}s</span>
        {:else if buildProgress.error}
          <span class="text-xs font-bold text-red-700">Errore</span>
        {:else}
          <span class="text-xs font-bold text-cyan-700">in corso · {buildProgress.elapsed_s}s</span>
        {/if}
      </div>
      <ul class="space-y-1.5 text-xs">
        {#each buildProgress.steps as s (s.id)}
          <li class="flex items-center gap-2">
            {#if s.status === 'done'}
              <span class="text-emerald-600 font-bold">✓</span>
            {:else if s.status === 'active'}
              <span class="text-cyan-600 font-bold animate-pulse">●</span>
            {:else if s.status === 'error'}
              <span class="text-red-600 font-bold">✕</span>
            {:else}
              <span class="text-zinc-300">○</span>
            {/if}
            <span class="{s.status === 'active' ? 'font-semibold text-cyan-900' : 'text-zinc-600'}">
              {liveLabel(s.id)}
            </span>
            {#if s.status === 'active'}
              <span class="ml-auto text-[10px] text-cyan-600">attivo</span>
            {/if}
            {#if s.status === 'error'}
              <span class="ml-auto text-[10px] text-red-600">{buildProgress.error}</span>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}
  <!-- Stepper -->
  <div class="flex items-center gap-1 overflow-x-auto rounded-xl border border-zinc-200 bg-white p-2 shadow-sm">
    {#each STEPS as label, i}
      {#if i > 0}<div class="h-px w-4 shrink-0 bg-zinc-200"></div>{/if}
      <button type="button" disabled={i + 1 > maxVisited && i + 1 !== step} class="flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition-all disabled:cursor-not-allowed {step === i + 1 ? 'bg-cyan-500 text-white shadow-md shadow-cyan-200' : i + 1 <= maxVisited ? 'text-zinc-600 hover:bg-zinc-50' : 'text-zinc-400 hover:bg-zinc-50'}" onclick={() => goto(i + 1)} aria-current={step === i + 1 ? "step" : undefined}>
        <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs {step === i + 1 ? 'bg-white text-cyan-500' : i + 1 <= maxVisited ? 'bg-zinc-200 text-zinc-700' : 'bg-zinc-100 text-zinc-400'}">{i + 1}</span><span class="whitespace-nowrap font-medium text-[11px] sm:text-xs">{label}</span>
      </button>
    {/each}
  </div>
  <!-- STEP 1 -->
  {#if step === 1}
    <div class="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div class="mb-6"><h2 class="text-lg font-semibold text-zinc-900">Che tipo di sistema costruisci?</h2><p class="text-sm text-zinc-500">Scegli il tipo di struttura da generare</p></div>
      <div class="grid grid-cols-2 gap-4">
        {#each [{ id: "lipid", icon: "🧪", title: "Bilayer lipidico", sub: "Composizione due foglietti" }, { id: "protein", icon: "🧫", title: "Proteina", sub: "PDB ID o file — embed nella membrana (opzione 1)" }, { id: "dna", icon: "🧬", title: "DNA", sub: "Sequenza nucleotidi" }, { id: "rna", icon: "🧬", title: "RNA", sub: "Sequenza nucleotidi" }, { id: "small-mol", icon: "💊", title: "Small molecule", sub: "PDB / SDF" }] as t}
          <button type="button" class="flex flex-col items-start rounded-xl border p-5 text-left transition-all {buildType === t.id ? 'border-cyan-500 bg-cyan-50/80 ring-2 ring-cyan-200 shadow-md shadow-cyan-100' : 'border-zinc-200 bg-white hover:border-zinc-300 hover:shadow-sm'}" onclick={() => (buildType = t.id)} aria-pressed={buildType === t.id}>
            <span class="text-3xl mb-2">{t.icon}</span><span class="text-base font-semibold text-zinc-900">{t.title}</span><span class="text-xs text-zinc-500 mt-1">{t.sub}</span>
          </button>
        {/each}
      </div>
      <div class="mt-6 flex justify-end"><button type="button" class="rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={next}>Avanti →</button></div>
    </div>
  {/if}

  <!-- STEP 2 -->
  {#if step === 2}
    <div class="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div class="mb-6"><h2 class="text-lg font-semibold text-zinc-900">Composizione</h2><p class="text-sm text-zinc-500">Scegli i lipidi per ogni foglietto</p></div>
      {#if buildType !== "lipid"}
        <div class="mb-5 rounded-lg bg-amber-50 border border-amber-200 p-4 text-xs text-amber-800"><span class="font-semibold">ℹ️ Builder locale:</span> testato per lipidi. Per proteine/DNA/RNA/small molecules <b>verrà inviato automaticamente a CHARMM-GUI</b> al click su "Crea struttura".</div>
      {/if}
      {#if buildType === "lipid"}
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <span class="text-xs font-semibold text-zinc-600 shrink-0">Preset:</span>
          {#each Object.entries(PRESETS) as [k, p] (k)}
            <button type="button" class="rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-xs text-zinc-700 hover:border-cyan-400 hover:bg-cyan-50" onclick={() => applyPreset(k)} title={p.label}>{p.label}</button>
          {/each}
        </div>
        <div class="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 bg-zinc-50 px-4 py-2.5">
          <label class="flex cursor-pointer items-center gap-2 text-xs font-medium text-zinc-700">
            <input type="checkbox" bind:checked={symmetric} onchange={onSymChange} class="accent-cyan-500" /> Foglietti identici
          </label>
          {#if !symmetric}
            <div class="flex rounded-lg border border-zinc-300 bg-white p-0.5" role="tablist" aria-label="Foglio attivo">
              <button type="button" role="tab" aria-selected={activeSide === "upper"} class="rounded-md px-3 py-1 text-xs font-semibold transition-colors {activeSide === 'upper' ? 'bg-cyan-500 text-white shadow-sm' : 'text-zinc-600 hover:bg-zinc-100'}" onclick={() => (activeSide = "upper")}>Foglio superiore</button>
              <button type="button" role="tab" aria-selected={activeSide === "lower"} class="rounded-md px-3 py-1 text-xs font-semibold transition-colors {activeSide === 'lower' ? 'bg-cyan-500 text-white shadow-sm' : 'text-zinc-600 hover:bg-zinc-100'}" onclick={() => (activeSide = "lower")}>Foglio inferiore</button>
            </div>
            <span class="text-[10px] text-zinc-400">le quantità vanno nel foglio selezionato</span>
          {/if}
          <span class="ml-auto text-xs font-medium text-zinc-500">{totalCounts(counts())} lipidi</span>
        </div>
        <div class="grid grid-cols-1 gap-4">
          <div class="rounded-xl border border-zinc-200 p-3">
            <div class="mb-2 flex items-center gap-2">
              <h3 class="shrink-0 text-sm font-semibold text-zinc-900">Catalogo</h3>
              <input type="search" bind:value={search} placeholder="Cerca tra 411 lipidi…" class="min-w-0 flex-1 rounded-lg border border-zinc-300 px-3 py-1.5 text-xs text-zinc-800 placeholder-zinc-400 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500" />
            </div>
            <div class="max-h-80 space-y-0.5 overflow-y-auto">
              {#each filtered() as l (l.id)}
                <button type="button" class="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left transition-colors {countOf(l.id) > 0 ? 'bg-cyan-50/70 hover:bg-cyan-100/70' : 'hover:bg-zinc-50'}" onclick={() => pick(l.id)}>
                  <span class="flex items-center gap-2"><span class="h-2 w-2 rounded-full" style="background-color: {CAT_COLORS[l.cat] || '#a1a1aa'}"></span><span class="text-xs font-semibold text-zinc-900">{l.id}</span><span class="text-[10px] text-zinc-400">{l.cat}</span></span>
                  <span class="text-[11px] font-bold {countOf(l.id) > 0 ? 'text-cyan-700' : 'text-zinc-300'}">{countOf(l.id) > 0 ? `×${countOf(l.id)}` : "+"}</span>
                </button>
              {/each}
            </div>
            <p class="mt-1.5 text-right text-[10px] text-zinc-400">{filtered().length} elencati · clicca per aggiungere{#if !search.trim() && !catFilter}, usa la ricerca per tutti i 411{/if}</p>
          </div>
          <div class="rounded-xl border border-zinc-200 p-3">
            <h3 class="mb-2 text-sm font-semibold text-zinc-900">Foglio {activeSide === "upper" ? "superiore" : "inferiore"}</h3>
            {#if pickedList(activeSide).length > 0}
              <div class="max-h-80 space-y-1 overflow-y-auto pr-1">
                {#each pickedList(activeSide) as [id, n] (id)}
                  <div class="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5">
                    <span class="h-2 w-2 rounded-full shrink-0" style="background-color: {CAT_COLORS[LIPID_CATALOG.find(l => l.id === id)?.cat] || '#a1a1aa'}"></span>
                    <span class="w-16 shrink-0 text-xs font-semibold text-zinc-900">{id}</span>
                    <input type="number" min="1" max="4096" value={n} oninput={(e) => qtyInput(id, e)} onblur={(e) => qtyBlur(id, e)} aria-label="Quantità {id}" class="w-16 rounded-md border border-zinc-300 px-1.5 py-1 text-center text-xs font-bold tabular-nums text-zinc-900 focus:border-cyan-500 focus:outline-none" />
                    <span class="hidden text-[10px] text-zinc-400 xl:inline">qty</span>
                    <div class="ml-auto flex items-center gap-1">
                      <button type="button" onclick={() => removeOne(id)} class="h-6 w-6 rounded-full bg-zinc-100 text-sm font-bold text-zinc-600 hover:bg-zinc-200" aria-label="-1 {id}">−</button>
                      <button type="button" onclick={() => add(id)} class="h-6 w-6 rounded-full bg-cyan-500 text-sm font-bold text-white hover:bg-cyan-600" aria-label="+1 {id}">+</button>
                      <button type="button" onclick={() => remove(id)} class="ml-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-red-200 bg-red-50 text-xs font-bold text-red-600 hover:border-red-300 hover:bg-red-100" aria-label="Rimuovi {id}" title="Rimuovi {id} dalla lista">✕</button>
                    </div>
                  </div>
                {/each}
              </div>
            {:else}
              <div class="rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-5 text-center">
                <p class="text-xs text-zinc-500">Clicca un lipide nel catalogo, poi digita la quantità qui</p>
              </div>
            {/if}
          </div>
        </div>

        {#if buildType === "lipid"}
          <div class="mt-6 rounded-lg bg-indigo-50 border border-indigo-200 p-4">
            <div class="flex items-center gap-2 mb-2"><span class="font-semibold text-indigo-800 text-xs">🎯 Opzione 2 — Inserisci recettore in membrana esistente</span></div>
            <p class="text-xs text-indigo-700 mb-3">Carica il PDB della membrana già generata e il PDB del recettore per inserirlo nella membrana.</p>
            {#if existingPdbs.length > 0}
              <div class="grid grid-cols-1 gap-3 md:grid-cols-2 mb-3">
                <div><label class="block text-[10px] text-indigo-600 mb-1">Membrana esistente</label><select bind:value={selectedExistingMembrane} class="w-full rounded border border-zinc-300 px-2 py-1.5 text-xs"><option value="">— scegli —</option>{#each existingPdbs as p (p.path)}<option value={p.path}>{p.name}</option>{/each}</select></div>
                <div><label class="block text-[10px] text-indigo-600 mb-1">Recettore esistente</label><select bind:value={selectedExistingReceptor} class="w-full rounded border border-zinc-300 px-2 py-1.5 text-xs"><option value="">— scegli —</option>{#each existingPdbs as p (p.path)}<option value={p.path}>{p.name}</option>{/each}</select></div>
              </div>
              <p class="text-[10px] text-indigo-500 mb-3">o carica file nuovi:</p>
            {/if}
            <div class="grid grid-cols-2 gap-3 mb-3">
              <div><label class="block text-[10px] text-indigo-600 mb-1">Membrana (PDB)</label><input type="file" accept=".pdb" class="w-full rounded border border-zinc-300 px-2 py-1.5 text-xs" bind:files={membraneFiles} /></div>
              <div><label class="block text-[10px] text-indigo-600 mb-1">Recettore (PDB)</label><input type="file" accept=".pdb" class="w-full rounded border border-zinc-300 px-2 py-1.5 text-xs" bind:files={receptorFiles} /></div>
            </div>
            <button type="button" disabled={embedBusy || (!membraneFiles?.length && !selectedExistingMembrane) || (!receptorFiles?.length && !selectedExistingReceptor)} class="w-full rounded-lg border border-indigo-300 bg-indigo-50 px-4 py-2 text-xs text-indigo-700 hover:bg-indigo-100 disabled:opacity-50" onclick={embedReceptor}>
              {embedBusy ? "Inserimento in corso…" : "▶ Inserisci recettore nella membrana"}
            </button>
            {#if embedErr}
              <div class="mt-2 text-[10px] text-red-600">{embedErr}</div>
            {/if}
          </div>
        {/if}

        <div class="mt-5 flex flex-col sm:flex-row gap-3 border-t border-zinc-100 pt-5">
          <button type="button" class="rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={back}>← Indietro</button><button type="button" class="flex-1 rounded-xl bg-cyan-700 px-6 py-3 text-base font-semibold text-white hover:bg-cyan-800 shadow-lg shadow-cyan-200 disabled:opacity-50 disabled:cursor-not-allowed" onclick={next} disabled={!stepValid()}>Avanti →</button>
        </div>
      {:else if buildType === "protein"}
        <div class="space-y-5"><div><label class="block text-sm font-semibold text-zinc-900 mb-2">PDB ID</label><input type="text" bind:value={proteinPdbId} maxlength="4" placeholder="Es. 1ABC" class="w-full rounded-lg border border-zinc-300 px-4 py-2.5 text-sm uppercase tracking-widest text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-2 focus:ring-cyan-200" /></div><div class="border border-dashed border-zinc-300 rounded-xl p-6 text-center hover:bg-zinc-50 transition-colors"><label class="flex items-center gap-3 cursor-pointer group"><input type="file" accept=".pdb,.pdbqt" class="hidden" onchange={(e) => { if (e.target.files?.[0]) proteinPdbFile = e.target.files[0]; }} /><span class="text-2xl group-hover:text-cyan-500 transition-colors">📁</span><span class="text-sm text-zinc-700">{proteinPdbFile ? proteinPdbFile.name : "oppure carica un file PDB"}</span></label></div></div>
        <div class="pt-6 border-t border-zinc-100 flex gap-3"><button type="button" class="flex-1 rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={back}>← Indietro</button><button type="button" class="flex-1 rounded-xl bg-cyan-700 px-6 py-3 text-base font-semibold text-white hover:bg-cyan-800 shadow-lg shadow-cyan-200 disabled:opacity-50 disabled:cursor-not-allowed" onclick={next} disabled={!stepValid()}>Avanti →</button></div>
      {:else if buildType === "dna"}
        <div class="space-y-5"><div><label class="block text-sm font-semibold text-zinc-900 mb-2">Sequenza DNA</label><textarea bind:value={dnaSequence} rows="4" placeholder="ATGCCGTAAGGCTT…" class="w-full rounded-lg border border-zinc-300 px-4 py-2.5 font-mono text-sm uppercase text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-2 focus:ring-cyan-200"></textarea></div></div>
        <div class="pt-6 border-t border-zinc-100 flex gap-3"><button type="button" class="flex-1 rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={back}>← Indietro</button><button type="button" class="flex-1 rounded-xl bg-cyan-700 px-6 py-3 text-base font-semibold text-white hover:bg-cyan-800 shadow-lg shadow-cyan-200 disabled:opacity-50 disabled:cursor-not-allowed" onclick={next} disabled={!stepValid()}>Avanti →</button></div>
      {:else if buildType === "rna"}
        <div class="space-y-5"><div><label class="block text-sm font-semibold text-zinc-900 mb-2">Sequenza RNA</label><textarea bind:value={rnaSequence} rows="4" placeholder="AUGCCGUAAGGCUU…" class="w-full rounded-lg border border-zinc-300 px-4 py-2.5 font-mono text-sm uppercase text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-2 focus:ring-cyan-200"></textarea></div></div>
        <div class="pt-6 border-t border-zinc-100 flex gap-3"><button type="button" class="flex-1 rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={back}>← Indietro</button><button type="button" class="flex-1 rounded-xl bg-cyan-700 px-6 py-3 text-base font-semibold text-white hover:bg-cyan-800 shadow-lg shadow-cyan-200 disabled:opacity-50 disabled:cursor-not-allowed" onclick={next} disabled={!stepValid()}>Avanti →</button></div>
      {:else}
        <div class="space-y-5"><div class="border-2 border-dashed border-zinc-300 rounded-2xl p-8 text-center hover:border-cyan-400 hover:bg-cyan-50/30 transition-colors"><span class="text-4xl mb-3 block">💊</span><span class="text-xs text-zinc-600 mb-2 block">Trascina o scegli un file</span><input type="file" accept=".pdb,.sdf,.mol" class="hidden" onchange={(e) => { if (e.target.files?.[0]) smallMolFile = e.target.files[0]; }} /><label class="cursor-pointer group"><span class="text-sm text-zinc-700 group-hover:text-cyan-600">{smallMolFile ? smallMolFile.name : "PDB / SDF / MOL"}</span></label></div></div>
        <div class="pt-6 border-t border-zinc-100 flex gap-3"><button type="button" class="flex-1 rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={back}>← Indietro</button><button type="button" class="flex-1 rounded-xl bg-cyan-700 px-6 py-3 text-base font-semibold text-white hover:bg-cyan-800 shadow-lg shadow-cyan-200 disabled:opacity-50 disabled:cursor-not-allowed" onclick={next} disabled={!stepValid()}>Avanti →</button></div>
      {/if}
    </div>
  {/if}

  <!-- STEP 3 -->
  {#if step === 3}
    <div class="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div class="mb-6"><h2 class="text-lg font-semibold text-zinc-900">Ambiente</h2><p class="text-sm text-zinc-500">Box, solvente e ioni</p></div>
      <div class="grid grid-cols-1 gap-4">
        <div class="space-y-4">
          <h3 class="text-sm font-semibold text-zinc-900">Box (nm)</h3>
          <div class="grid grid-cols-3 gap-3">
            <div class="flex flex-col"><label class="text-xs text-zinc-500 mb-1">X</label><input type="number" step="0.5" min="2" bind:value={boxX} class="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500" /></div>
            <div class="flex flex-col"><label class="text-xs text-zinc-500 mb-1">Y</label><input type="number" step="0.5" min="2" bind:value={boxY} class="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500" /></div>
            <div class="flex flex-col"><label class="text-xs text-zinc-500 mb-1">Z</label><input type="number" step="0.5" min="2" bind:value={boxZ} class="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500" /></div>
          </div>
          <label class="flex items-center gap-3 mt-2"><input type="checkbox" bind:checked={centerStructure} class="accent-cyan-500" /><span class="text-sm text-zinc-700 cursor-pointer">Centra nel box</span></label>
          {#if totalCounts(upperCounts) > 0}
            <p class="text-[11px] leading-snug text-zinc-500">
              Suggerito per {totalCounts(upperCounts)} lipidi/foglio:
              <b class="text-zinc-700">{(Math.sqrt(totalCounts(upperCounts) * 0.56) + 1.8).toFixed(1)} × {(Math.sqrt(totalCounts(upperCounts) * 0.56) + 1.8).toFixed(1)} nm</b>
              (sqrt(n × 0.56) + 1.8 di margine per l'idratazione e gli steroli)
            </p>
          {/if}
        </div>
        <div class="space-y-4">
          <div><label class="block text-sm font-semibold text-zinc-900 mb-3">Solvente</label><select bind:value={solvent} class="w-full rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"><option value="water">Acqua TIP3P</option><option value="none">Nessuno</option></select></div>
          <div><label class="block text-sm font-semibold text-zinc-900 mb-3">Ioni</label><select bind:value={ions} class="w-full rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-zinc-900 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"><option value="KCl">KCl (potassio cloruro)</option><option value="NaCl">NaCl (sodio cloruro)</option><option value="none">Nessuno</option></select></div>
        </div>
      </div>
      <div class="mt-6 rounded-lg bg-zinc-50 border border-zinc-200 p-4"><p class="text-xs text-zinc-500 leading-relaxed"><span class="font-semibold text-zinc-700">ℹ️ Mescolamento laterale:</span> specie distribuite casualmente nei foglietti (seed riproducibile), come in una membrana reale.</p></div>
      <div class="mt-6 flex justify-between"><button type="button" class="rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={back}>← Indietro</button><button type="button" class="rounded-xl bg-cyan-700 px-6 py-3 text-base font-semibold text-white hover:bg-cyan-700 shadow-lg shadow-cyan-200" onclick={next}>Avanti →</button></div>
    </div>
  {/if}
  <!-- STEP 4 -->
  {#if step === 4}
    <div class="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div class="mb-6"><h2 class="text-lg font-semibold text-zinc-900">Riepilogo</h2><p class="text-sm text-zinc-500">Verifica prima di generare</p></div>
      {#if buildType !== "lipid"}
        <div class="mb-5 rounded-lg bg-amber-50 border border-amber-200 p-4 text-xs text-amber-800"><span class="font-semibold">ℹ️ Builder locale:</span> testato per lipidi. Per proteine/DNA/RNA/small molecules <b>verrà inviato automaticamente a CHARMM-GUI</b> al click su "Crea struttura".</div>
        {#if buildType === "protein"}
          <div class="mt-4 rounded-lg bg-blue-50 border border-blue-200 p-4 text-xs text-blue-800">
            <span class="font-semibold">🔗 Opzione 1 — Proteina nella membrana:</span><br/>
            Il builder scarica l'orientamento OPM/PPM del PDB, lo centra nella membrana lipidica, genera la topologia CHARMM36 e prepara i file per <code>gmx grompp</code>. <br/>
            <span class="font-semibold">⚠️ Limite attuale:</span> l'inserimento di una proteina in una <i>membrana già pronta</i> (opzione 2) richiede uno script dedicato in arrivo; per ora usa "Crea struttura" con il PDB della proteina.
          </div>
        {/if}
      {/if}
      <div class="rounded-lg bg-zinc-50 border border-zinc-200 p-5 font-mono text-xs leading-relaxed text-zinc-800">
        {#if buildType === "lipid"}
          <div class="flex justify-between mb-1"><span class="text-zinc-500">Superiore:</span> <span class="font-bold">{specString(upperCounts) || "—"}</span></div>
          <div class="flex justify-between mb-1"><span class="text-zinc-500">Inferiore:</span> <span class="font-bold">{symmetric ? specString(upperCounts) || "—" : specString(lowerCounts) || "—"}</span></div>
        {:else if buildType === "protein"}
          <div class="flex justify-between mb-1"><span class="text-zinc-500">Proteina:</span> <span class="font-bold">{proteinPdbId || proteinPdbFile?.name}</span></div>
        {:else if buildType === "dna"}
          <div class="flex justify-between mb-1"><span class="text-zinc-500">DNA:</span> <span class="font-bold">{dnaSequence}</span></div>
        {:else if buildType === "rna"}
          <div class="flex justify-between mb-1"><span class="text-zinc-500">RNA:</span> <span class="font-bold">{rnaSequence}</span></div>
        {:else}
          <div class="flex justify-between mb-1"><span class="text-zinc-500">Small molecule:</span> <span class="font-bold">{smallMolFile?.name}</span></div>
        {/if}
        <div class="flex justify-between mb-1"><span class="text-zinc-500">Box:</span> <span class="font-bold">{boxX}×{boxY}×{boxZ} nm</span></div>
        <div class="flex justify-between mb-1"><span class="text-zinc-500">Solvente:</span> <span class="font-bold">{solvent === "water" ? "acqua TIP3P" : "nessuno"}</span></div>
        <div class="flex justify-between"><span class="text-zinc-500">Ioni:</span> <span class="font-bold">{ions}</span></div>
      </div>
      {#if buildMetrics}
        <div class="mt-5 rounded-lg bg-emerald-50 border border-emerald-200 p-4"><div class="flex items-center gap-2 mb-3"><span class="text-emerald-700 font-semibold text-xs">📊 Metriche build</span></div><div class="grid grid-cols-2 gap-3 text-xs"><div><span class="text-zinc-500">Spessore P-P:</span> <span class="font-mono text-zinc-900">{buildMetrics.thickness_P_nm} nm</span></div><div><span class="text-zinc-500">APL misurata:</span> <span class="font-mono text-zinc-900">{buildMetrics.apl_measured_nm2} nm²</span></div><div><span class="text-zinc-500">APL attesa:</span> <span class="font-mono text-zinc-900">{buildMetrics.apl_expected_nm2} nm²</span></div><div class="col-span-2"><span class="text-zinc-500">Carica lipidi:</span> <span class={`font-mono ${chargeColor} text-zinc-900`}>{buildMetrics.net_lipid_charge_e.toFixed(2)} e</span></div><div><span class="text-zinc-500">Atomi totali:</span> <span class="font-mono text-zinc-900">{buildMetrics.natoms}</span></div></div></div>
      {/if}
      <div class="mt-5 rounded-lg bg-zinc-50 border border-zinc-200 p-4"><div class="flex items-center gap-2 mb-3"><span class="text-zinc-600 font-semibold text-xs">💾 Composizione</span></div><div class="flex flex-col sm:flex-row gap-3"><button type="button" onclick={saveJson} class="flex-1 rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-zinc-700 hover:bg-zinc-100">Scarica JSON</button><label class="flex-1 rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-zinc-700 hover:bg-zinc-100 cursor-pointer">Carica JSON<input type="file" accept=".json" class="hidden" onchange={loadJson} /></label></div></div>
      {#if buildLog}
        <div class="mt-5 rounded-lg bg-zinc-900 p-4 max-h-64 overflow-y-auto"><div class="flex items-center gap-2 mb-3"><span class="text-cyan-400 font-bold text-[10px]">LOG GROMACS</span><button type="button" onclick={onLogClear} class="text-zinc-500 hover:text-white">✕</button></div><pre class="text-[10px] text-zinc-300 overflow-x-auto">{buildLog}</pre></div>
      {/if}
      <div class="mt-6 flex flex-col sm:flex-row gap-4">{#if buildType === "lipid"}<button type="button" class="flex-1 rounded-xl bg-emerald-700 px-6 py-3.5 text-base font-semibold text-white hover:bg-emerald-800 shadow-lg shadow-emerald-200 disabled:bg-zinc-300 disabled:cursor-not-allowed" onclick={create} disabled={propLocalBusy || !stepValid()}>{#if propLocalBusy}<span class="inline-block w-5 h-5 border-2 border-current border-t-transparent rounded-full animate-spin mr-2"></span>Costruzione in corso…{:else}▶ Crea struttura (locale, GROMACS){/if}</button>{/if}<button type="button" class="flex-1 rounded-xl bg-cyan-700 px-6 py-3.5 text-base font-semibold text-white hover:bg-cyan-800 shadow-lg shadow-cyan-200" onclick={submit}>Invia a CHARMM-GUI</button></div>
      <div class="mt-4 flex justify-between"><button type="button" class="rounded-xl bg-zinc-900 px-6 py-3 text-base font-semibold text-white hover:bg-zinc-700 shadow-lg shadow-zinc-200" onclick={back}>← Indietro</button></div>
    </div>
  {/if}
</div>
