<script>
  // Wizard di composizione in 4 passi.
  // Scienza: la composizione nei due foglietti è sempre MESCOLATA a caso
  // (soluzione laterale 2D) — non è un'opzione estetica; per questo non
  // esiste una scelta "griglia/mosaico" nella UI.
  import { LIPID_CATALOG, LIPID_CATEGORIES } from "./lipidCatalog.js";

  const { onCreate, onSubmit } = $props();

  const SUPPORTED_LOCAL = new Set([
    "POPC", "CHOL", "POPE", "DPPC", "DSPC", "DOPS", "DOTAP", "SM",
  ]);

  // lipidi comuni mostrati quando non c'è ricerca/filtro
  const COMMON = new Set([
    "POPC", "CHOL", "POPE", "DPPC", "DSPC", "DOPS", "DOTAP", "SM",
    "DOPC", "DOPE", "DMPC", "DLPC", "DPPS", "POPS", "POPA", "POPG", "POPI",
  ]);

  const CAT_COLORS = {
    PC: "#3b82f6", PE: "#10b981", PS: "#ec4899", PA: "#f59e0b",
    PG: "#8b5cf6", "PI / Fosfoinositidi": "#06b6d4", Cardiolipine: "#dc2626",
    Steroli: "#ef4444", Sfingolipidi: "#14b8a6", Ceramidi: "#a855f7",
    "Acidi grassi": "#84cc16", Detergenti: "#78716c", Altri: "#a1a1aa",
  };

  const STEPS = ["Tipo", "Composizione", "Ambiente", "Riepilogo"];

  // --- stato wizard ---
  let step = $state(1);
  let maxVisited = $state(1);
  let buildType = $state("lipid");

  // --- composizione lipidi ---
  let symmetric = $state(true);
  let upperCounts = $state({});
  let lowerCounts = $state({});
  let activeSide = $state("upper"); // tab quando asimmetrico
  let search = $state("");
  let catFilter = $state("");

  // --- altri tipi ---
  let proteinPdbId = $state("");
  let proteinPdbFile = null;
  let dnaSequence = $state("");
  let rnaSequence = $state("");
  let smallMolFile = null;

  // --- ambiente ---
  let boxX = $state(12);
  let boxY = $state(12);
  let boxZ = $state(12);
  let centerStructure = $state(true);
  let solvent = $state("water");
  let ions = $state("KCl");

  let localBusy = $state(false);

  function goto(n) {
    if (n <= maxVisited) step = n;
  }
  function next() {
    if (stepValid()) {
      step = Math.min(step + 1, 4);
      maxVisited = Math.max(maxVisited, step);
    }
  }
  function back() {
    step = Math.max(step - 1, 1);
  }

  // --- composizione: contatori per lato attivo ---
  function counts() {
    return symmetric || activeSide === "upper" ? upperCounts : lowerCounts;
  }
  function setCounts(next) {
    if (symmetric || activeSide === "upper") upperCounts = next;
    else lowerCounts = next;
  }
  function countOf(id) {
    return counts()[id] || 0;
  }
  function add(id) {
    setCounts({ ...counts(), [id]: countOf(id) + 1 });
  }
  function sub(id) {
    const c = countOf(id);
    if (c > 0) {
      const n = { ...counts() };
      if (c === 1) delete n[id];
      else n[id] = c - 1;
      setCounts(n);
    }
  }
  function selectedList() {
    const entries = Object.entries(upperCounts).filter(([, v]) => v > 0);
    if (symmetric) return entries;
    const lower = Object.entries(lowerCounts).filter(([, v]) => v > 0);
    return entries; // la vista selezionati mostra upper; lower visibile in riepilogo
  }
  function totalCounts(c) {
    return Object.values(c).reduce((s, v) => s + v, 0);
  }

  function specString(c) {
    const parts = Object.entries(c)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${k}=${v}`);
    return parts.join(";");
  }

  // --- catalogo filtrato ---
  function filtered() {
    const q = search.trim().toUpperCase();
    let list = LIPID_CATALOG;
    if (catFilter) list = list.filter((l) => l.cat === catFilter);
    if (q) list = list.filter((l) => l.id.includes(q));
    else if (!catFilter) list = list.filter((l) => COMMON.has(l.id));
    return list.slice(0, 90);
  }
  function filteredTotal() {
    const q = search.trim().toUpperCase();
    let list = LIPID_CATALOG;
    if (catFilter) list = list.filter((l) => l.cat === catFilter);
    if (q) list = list.filter((l) => l.id.includes(q));
    else if (!catFilter) list = list.filter((l) => COMMON.has(l.id));
    return list.length;
  }
  const hasSearchOrFilter = $derived(!!search.trim() || !!catFilter);

  // --- validazione per passo ---
  function stepValid() {
    if (step === 2) {
      if (buildType === "lipid") {
        if (totalCounts(upperCounts) === 0) return false;
        if (!symmetric && totalCounts(lowerCounts) === 0) return false;
        return true;
      }
      if (buildType === "protein") return proteinPdbId.trim().length === 4 || !!proteinPdbFile;
      if (buildType === "dna") return dnaSequence.trim().length > 0;
      if (buildType === "rna") return rnaSequence.trim().length > 0;
      if (buildType === "small-mol") return !!smallMolFile;
    }
    return true;
  }

  // --- azioni finali ---
  function params() {
    return {
      boxX, boxY, boxZ, centerStructure, layout: "fluid", solvent, ions,
      buildType,
      upper: specString(upperCounts) || null,
      lower: symmetric ? specString(upperCounts) : specString(lowerCounts),
      proteinPdbId, proteinPdbFileName: proteinPdbFile?.name || null,
      dnaSequence, rnaSequence,
      smallMolFileName: smallMolFile?.name || null,
    };
  }

  function create() {
    if (!stepValid()) return;
    const p = params();
    if (onCreate) onCreate(p.upper || "POPC=64", p);
  }
  function submit() {
    if (!stepValid()) return;
    const p = params();
    if (onSubmit) onSubmit(p.upper || "POPC=64", p);
  }

  function summary() {
    if (buildType !== "lipid") return "";
    const u = specString(upperCounts) || "—";
    const l = symmetric ? u : specString(lowerCounts) || "—";
    return `Foglio superiore: ${u}\nFoglio inferiore: ${l}`;
  }
</script>

<div class="flex flex-col gap-4">
  <!-- Stepper -->
  <div class="flex items-center gap-1 rounded-xl border border-zinc-200 bg-white p-2">
    {#each STEPS as label, i}
      {#if i > 0}
        <div class="h-px flex-1 bg-zinc-200"></div>
      {/if}
      <button
        type="button"
        class="flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors {step === i + 1
          ? 'bg-cyan-50 text-cyan-700 ring-1 ring-cyan-300'
          : i + 1 <= maxVisited
            ? 'text-zinc-700 hover:bg-zinc-50'
            : 'text-zinc-300'}"
        onclick={() => goto(i + 1)}
        aria-current={step === i + 1 ? "step" : undefined}
      >
        <span class="flex h-5 w-5 items-center justify-center rounded-full text-[10px] {step === i + 1 ? 'bg-cyan-500 text-white' : i + 1 <= maxVisited ? 'bg-zinc-200 text-zinc-700' : 'bg-zinc-100 text-zinc-400'}">{i + 1}</span>
        {label}
      </button>
    {/each}
  </div>

  <!-- STEP 1: tipo sistema -->
  {#if step === 1}
    <div class="rounded-xl border border-zinc-200 bg-white p-4">
      <div class="grid grid-cols-3 gap-3">
        {#each [
          { id: "lipid", icon: "🫧", title: "Bilayer lipidico", sub: "Composizione due foglietti" },
          { id: "protein", icon: "🧬", title: "Proteina", sub: "PDB ID o file" },
          { id: "dna", icon: "🧵", title: "DNA", sub: "Sequenza nucleotidi" },
          { id: "rna", icon: "🧵", title: "RNA", sub: "Sequenza nucleotidi" },
          { id: "small-mol", icon: "💊", title: "Small molecule", sub: "PDB / SDF" },
        ] as t}
          <button
            type="button"
            class="flex flex-col items-start rounded-xl border p-4 text-left transition-all {buildType === t.id
              ? 'border-cyan-500 bg-cyan-50/60 ring-2 ring-cyan-200'
              : 'border-zinc-200 bg-white hover:border-zinc-300 hover:shadow-sm'}"
            onclick={() => (buildType = t.id)}
            aria-pressed={buildType === t.id}
          >
            <span class="text-2xl">{t.icon}</span>
            <span class="mt-2 text-sm font-semibold text-zinc-800">{t.title}</span>
            <span class="text-[11px] text-zinc-500">{t.sub}</span>
          </button>
        {/each}
      </div>
      <div class="mt-4 flex justify-end">
        <button type="button" class="rounded-lg bg-zinc-900 px-5 py-2 text-sm font-medium text-white hover:bg-zinc-700" onclick={next}>Avanti →</button>
      </div>
    </div>
  {/if}

  <!-- STEP 2: composizione -->
  {#if step === 2}
    <div class="rounded-xl border border-zinc-200 bg-white p-4">
      {#if buildType === "lipid"}
        <!-- simmetria -->
        <div class="mb-3 flex items-center gap-3 rounded-lg bg-zinc-50 p-2.5">
          <span class="text-xs font-medium text-zinc-700">Foglietti:</span>
          <button
            type="button"
            class="rounded-md px-3 py-1 text-xs font-medium {symmetric ? 'bg-cyan-500 text-white' : 'bg-white text-zinc-600 ring-1 ring-zinc-200 hover:bg-zinc-100'}"
            onclick={() => { symmetric = true; }}
            aria-pressed={symmetric}
          >Composizione identica</button>
          <button
            type="button"
            class="rounded-md px-3 py-1 text-xs font-medium {!symmetric ? 'bg-cyan-500 text-white' : 'bg-white text-zinc-600 ring-1 ring-zinc-200 hover:bg-zinc-100'}"
            onclick={() => { symmetric = false; }}
            aria-pressed={!symmetric}
          >Indipendente (asimmetrico)</button>
        </div>

        {#if !symmetric}
          <div class="mb-3 flex gap-2">
            <button type="button" class="rounded-lg px-4 py-1.5 text-xs font-semibold {activeSide === 'upper' ? 'bg-zinc-900 text-white' : 'bg-zinc-100 text-zinc-600'}" onclick={() => (activeSide = "upper")}>Foglio superiore ({totalCounts(upperCounts)})</button>
            <button type="button" class="rounded-lg px-4 py-1.5 text-xs font-semibold {activeSide === 'lower' ? 'bg-zinc-900 text-white' : 'bg-zinc-100 text-zinc-600'}" onclick={() => (activeSide = "lower")}>Foglio inferiore ({totalCounts(lowerCounts)})</button>
          </div>
        {/if}

        <!-- selezionati -->
        {#if selectedList().length > 0}
          <div class="mb-3 flex flex-wrap gap-1.5 rounded-lg bg-cyan-50/50 p-2">
            {#each selectedList() as [id, n] (id)}
              <span class="flex items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-xs font-medium text-zinc-800 ring-1 ring-zinc-200">
                {id} <span class="text-zinc-400">×</span> {n}
                <button type="button" class="text-zinc-400 hover:text-red-500" onclick={() => sub(id)} aria-label="Rimuovi {id}">✕</button>
              </span>
            {/each}
          </div>
        {/if}

        <!-- ricerca + filtro -->
        <div class="mb-2 flex gap-2">
          <input
            type="search"
            bind:value={search}
            placeholder="Cerca lipide (es. POPC, CHOL, cer…)"
            class="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-800 placeholder-zinc-400 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          />
          <select
            bind:value={catFilter}
            class="rounded-lg border border-zinc-300 bg-white px-2 py-2 text-xs text-zinc-700 focus:border-cyan-500 focus:outline-none"
          >
            <option value="">Tutte le categorie</option>
            {#each LIPID_CATEGORIES as c (c)}
              <option value={c}>{c}</option>
            {/each}
          </select>
        </div>

        <!-- lista catalogo -->
        <div class="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-zinc-100 p-1">
          {#each filtered() as l (l.id)}
            <div class="flex items-center justify-between rounded-lg px-2 py-1.5 {countOf(l.id) > 0 ? 'bg-cyan-50/60' : 'hover:bg-zinc-50'}">
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full" style="background-color: {CAT_COLORS[l.cat] || '#a1a1aa'}"></span>
                <span class="text-sm font-semibold text-zinc-800">{l.id}</span>
                <span class="text-[10px] text-zinc-400">{l.cat}</span>
                {#if !SUPPORTED_LOCAL.has(l.id)}
                  <span class="rounded bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 ring-1 ring-amber-200" title="Non costruibie in locale: usa 'Invia a CHARMM-GUI'">solo CHARMM-GUI</span>
                {/if}
              </div>
              <div class="flex items-center gap-1.5">
                <button type="button" class="flex h-7 w-7 items-center justify-center rounded-full bg-zinc-100 text-base font-bold text-zinc-600 hover:bg-zinc-200 disabled:opacity-40" onclick={() => sub(l.id)} disabled={countOf(l.id) === 0} aria-label="Rimuovi un {l.id}">−</button>
                <span class="w-8 text-center text-sm font-bold tabular-nums text-zinc-800">{countOf(l.id)}</span>
                <button type="button" class="flex h-7 w-7 items-center justify-center rounded-full bg-cyan-500 text-base font-bold text-white hover:bg-cyan-600" onclick={() => add(l.id)} aria-label="Aggiungi un {l.id}">+</button>
              </div>
            </div>
          {:else}
            <p class="p-4 text-center text-xs text-zinc-400">Nessun lipide trovato — modifica la ricerca o la categoria.</p>
          {/each}
        </div>
        <p class="mt-1 text-[10px] text-zinc-400">
          {#if hasSearchOrFilter}
            {filteredTotal()} risultati · {LIPID_CATALOG.length} lipidi disponibili nel catalogo CHARMM36
          {:else}
            Lipidi comuni mostrati — usa la ricerca per tutti i {LIPID_CATALOG.length} lipidi CHARMM36
          {/if}
          · mix laterale sempre casuale (mescolamento reale)
        </p>
      {:else if buildType === "protein"}
        <label class="block text-xs font-medium text-zinc-700">PDB ID (4 caratteri)</label>
        <input type="text" bind:value={proteinPdbId} maxlength="4" placeholder="Es. 1ABC"
          class="mt-1 w-full max-w-xs rounded-lg border border-zinc-300 px-3 py-2 text-sm uppercase tracking-widest text-zinc-800 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500" />
        <label class="mt-3 flex w-fit cursor-pointer items-center gap-2 rounded-lg border border-dashed border-zinc-300 px-4 py-2 text-xs text-zinc-600 hover:bg-zinc-50">
          <input type="file" accept=".pdb,.pdbqt" class="hidden" onchange={(e) => { if (e.target.files?.[0]) proteinPdbFile = e.target.files[0]; }} />
          📁 {proteinPdbFile ? proteinPdbFile.name : "oppure carica un file PDB"}
        </label>
      {:else if buildType === "dna"}
        <label class="block text-xs font-medium text-zinc-700">Sequenza DNA (A, T, G, C)</label>
        <textarea bind:value={dnaSequence} rows="3" placeholder="ATGCCGTAAGGCTT…"
          class="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 font-mono text-sm uppercase text-zinc-800 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"></textarea>
      {:else if buildType === "rna"}
        <label class="block text-xs font-medium text-zinc-700">Sequenza RNA (A, U, G, C)</label>
        <textarea bind:value={rnaSequence} rows="3" placeholder="AUGCCGUAAGGCUU…"
          class="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 font-mono text-sm uppercase text-zinc-800 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"></textarea>
      {:else if buildType === "small-mol"}
        <label class="flex w-fit cursor-pointer flex-col gap-1 rounded-xl border-2 border-dashed border-zinc-300 px-8 py-6 text-center hover:border-cyan-400 hover:bg-cyan-50/30">
          <span class="text-2xl">💊</span>
          <span class="text-xs text-zinc-600">{smallMolFile ? smallMolFile.name : "Trascina o scegli un file PDB / SDF / MOL"}</span>
          <input type="file" accept=".pdb,.sdf,.mol" class="hidden" onchange={(e) => { if (e.target.files?.[0]) smallMolFile = e.target.files[0]; }} />
        </label>
      {/if}

      <div class="mt-4 flex justify-between">
        <button type="button" class="rounded-lg px-4 py-2 text-sm font-medium text-zinc-600 hover:bg-zinc-100" onclick={back}>← Indietro</button>
        <button type="button" class="rounded-lg bg-zinc-900 px-5 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-300" onclick={next} disabled={!stepValid()}>Avanti →</button>
      </div>
    </div>
  {/if}

  <!-- STEP 3: ambiente -->
  {#if step === 3}
    <div class="rounded-xl border border-zinc-200 bg-white p-4">
      <div class="grid grid-cols-3 gap-3">
        {#each [["Box X (nm)", boxX, "boxX"], ["Box Y (nm)", boxY, "boxY"], ["Box Z (nm)", boxZ, "boxZ"]] as [label, val, key] (key)}
          <div>
            <label class="block text-xs font-medium text-zinc-600">{label}</label>
            <input type="number" step="0.5" min="2" value={val}
              oninput={(e) => { if (key === "boxX") boxX = +e.target.value; if (key === "boxY") boxY = +e.target.value; if (key === "boxZ") boxZ = +e.target.value; }}
              class="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-800 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500" />
          </div>
        {/each}
      </div>
      <label class="mt-3 flex w-fit cursor-pointer items-center gap-2 text-xs text-zinc-700">
        <input type="checkbox" bind:checked={centerStructure} class="accent-cyan-500" />
        Centra la struttura nel box
      </label>
      <div class="mt-4 grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs font-medium text-zinc-600">Solvente</label>
          <select bind:value={solvent} class="mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 focus:border-cyan-500 focus:outline-none">
            <option value="water">Acqua TIP3P</option>
            <option value="none">Nessuno</option>
          </select>
        </div>
        <div>
          <label class="block text-xs font-medium text-zinc-600">Ioni</label>
          <select bind:value={ions} class="mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 focus:border-cyan-500 focus:outline-none">
            <option value="KCl">KCl 0.15 M</option>
            <option value="NaCl">NaCl 0.15 M</option>
            <option value="none">Nessuno</option>
          </select>
        </div>
      </div>
      <p class="mt-3 rounded-lg bg-zinc-50 p-2 text-[10px] leading-relaxed text-zinc-500">
        ℹ️ I lipidi vengono posizionati con <b>mescolamento laterale casuale</b> (seed riproducibile), come in una membrana reale. La scelta piano/mosaico non esiste più: è sempre mosaico.
      </p>
      <div class="mt-4 flex justify-between">
        <button type="button" class="rounded-lg px-4 py-2 text-sm font-medium text-zinc-600 hover:bg-zinc-100" onclick={back}>← Indietro</button>
        <button type="button" class="rounded-lg bg-zinc-900 px-5 py-2 text-sm font-medium text-white hover:bg-zinc-700" onclick={next}>Avanti →</button>
      </div>
    </div>
  {/if}

  <!-- STEP 4: riepilogo -->
  {#if step === 4}
    <div class="rounded-xl border border-zinc-200 bg-white p-4">
      <div class="rounded-lg bg-zinc-50 p-3 font-mono text-xs leading-relaxed text-zinc-700">
        {#if buildType === "lipid"}
          <div><span class="text-zinc-400">superiore:</span> {specString(upperCounts) || "—"}</div>
          <div><span class="text-zinc-400">inferiore:</span> {symmetric ? specString(upperCounts) || "—" : specString(lowerCounts) || "—"}</div>
        {:else if buildType === "protein"}
          <div><span class="text-zinc-400">proteina:</span> {proteinPdbId || proteinPdbFile?.name}</div>
        {:else if buildType === "dna"}
          <div><span class="text-zinc-400">DNA:</span> {dnaSequence}</div>
        {:else if buildType === "rna"}
          <div><span class="text-zinc-400">RNA:</span> {rnaSequence}</div>
        {:else}
          <div><span class="text-zinc-400">small molecule:</span> {smallMolFile?.name}</div>
        {/if}
        <div class="mt-1"><span class="text-zinc-400">box:</span> {boxX}×{boxY}×{boxZ} nm · centratura: {centerStructure ? "sì" : "no"}</div>
        <div><span class="text-zinc-400">solvente:</span> {solvent === "water" ? "acqua TIP3P" : "nessuno"} · <span class="text-zinc-400">ioni:</span> {ions}</div>
      </div>
      <div class="mt-4 flex flex-col gap-2 sm:flex-row">
        <button type="button" class="flex-1 rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500 disabled:bg-zinc-300" onclick={create} disabled={localBusy}>
          {localBusy ? "Costruzione in corso…" : "▶ Crea struttura (locale, GROMACS)"}
        </button>
        <button type="button" class="flex-1 rounded-lg bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500" onclick={submit}>
          Invia a CHARMM-GUI
        </button>
      </div>
      <div class="mt-4 flex justify-between">
        <button type="button" class="rounded-lg px-4 py-2 text-sm font-medium text-zinc-600 hover:bg-zinc-100" onclick={back}>← Indietro</button>
      </div>
    </div>
  {/if}
</div>
