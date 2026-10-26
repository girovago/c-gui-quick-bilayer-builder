<script>
  // Vista 3D NGL: stage unico per component-mount (removeAllComponents al cambio
  // job, mai recreate del canvas → niente leak WebGPU/WebGL). NGL = dist UMD
  // vendORIZZATA in web/public/vendor/ngl.js (three.js incluso, no CDN).
  import { onMount } from "svelte";

  let { jobid = null } = $props();

  let holder = $state(null);
  let stage = $state(null);
  let state = $state("idle"); // idle | loading | ready | error
  let message = $state("");
  let showWater = $state(false);
  let isSample = $state(false); // TRUE = demo locale in scena (badge giallo)
  let style = $state("spacefill"); // stile della rappresentazione principale
  let panelOpen = $state(true); // pannello controlli (⚙ collassa su schermi stretti)
  let hint = $state(false); // pillola hint mouse (5s dal mount)
  let note = $state(""); // messaggio onesto su stile non applicabile (es. cartoon su lipidi puri)
  let hasSolvent = $state(true); // il sistema corrente ha acqua/ioni (il toggle si disabilita se no)
  let lastLoaded = null;
  let lastRequested = null; // il check corre dopo l'await: evita la race del cambio job
  let waterRepr = null;
  let compRef = null; // componente corrente in scena (sample o job reale)
  let mainRepr = null; // UNA sola rappresentazione principale (il solvente è a parte)

  // TRAPPOLA verificata dal vivo (26/10, NGL vendORIZZATO): nelle selezioni le
  // keyword (`resname`, `element`…) tornano il set COMPLETO e — peggio — un
  // resname elencato che NON esiste nella struttura avvelena l'intera selezione
  // portandola al set completo (es. 'not (TIP3C TIP ...)' → tutti gli atomi).
  // Quindi niente selezioni statiche: i nomi solvente vengono presi SOLO
  // dall'intersezione tra un elenco canonico e i resname davvero presenti
  // (letti da structure.eachResidue), e se non ce ne sono il toggle si disabilita.
  const SOLVENT_CANON = new Set([
    "TIP3", "TIP3C", "TIP", "TIPJ", "HOH", "WAT", "SOL", // acqua (CHARMM/PDB)
    "SOD", "POT", "CLA", "CAL", "ZN", "ZN2", "MG", "LI", "NA", "K", "CL", // ioni CHARMM
  ]);
  const SAMPLE = "/api/sample/pdb";

  function solventResNames(st) {
    try {
      const present = new Set();
      st.eachResidue(function (rp) { if (rp.resname) present.add(rp.resname); });
      return [...SOLVENT_CANON].filter((n) => present.has(n)).sort();
    } catch {
      return [];
    }
  }

  onMount(() => {
    if (!window.NGL) {
      state = "error";
      message = "NGL non disponibile (manca web/public/vendor/ngl.js)";
      return;
    }
    stage = new window.NGL.Stage(holder, { backgroundColor: "#0b0f1a" });
    window.__ngl_stage = stage; // probe debug da tools.browser
    // su schermo stretto i controlli partono chiusi (il ⚙ li riapre)
    if (typeof window.innerWidth === "number" && window.innerWidth < 640) panelOpen = false;
    hint = true;
    const hintTimer = setTimeout(() => (hint = false), 5000);
    return () => {
      clearTimeout(hintTimer);
      stage.removeAllComponents();
      stage.dispose();
      stage = null;
      delete window.__ngl_stage;
    };
  });

  function mainParams(name, comp) {
    // esclusione solvente dinamica (vedi nota SOLVENT_CANON in alto): solo
    // resname presenti; se il sistema non ha solvente → sele vuota = tutto
    const names = comp ? solventResNames(comp.structure) : [];
    const clean = names.length ? `not (${names.join(" ")})` : "";
    // cartoon: solo 'protein' (keyword che funziona e già esclude il solvente)
    if (name === "ball+stick") return clean ? { sele: clean } : {}; // NGL usa il «+», non il tratteggio
    if (name === "licorice") return clean ? { sele: clean, radius: 0.25 } : { radius: 0.25 };
    if (name === "cartoon") return { sele: "protein" };
    const p = { radius: "schematic" }; // spacefill (default)
    if (clean) p.sele = clean;
    return p;
  }

  function hasProtein() {
    // cartoon disegna il backbone proteico: conta atomi di backbone reali con
    // getAtomSet (le keyword qui funzionano: 'protein' → sottoinsieme corretto;
    // il solo resname-based matchava le code finte GLY/OLE dei lipidi)
    try {
      if (!compRef || !compRef.structure) return false;
      const st = compRef.structure;
      const bs = st.getAtomSet(new window.NGL.Selection("protein and backbone"));
      let n = 0;
      for (let i = 0; i < st.atomCount; i++) if (bs.isSet(i)) n++;
      return n > 0;
    } catch {
      return false;
    }
  }

  function setStyle(name) {
    const prev = style;
    style = name;
    note = "";
    try {
      if (!compRef) return;
      if (name === "cartoon" && !hasProtein()) {
        style = prev; // la select resta sullo stile reale, il note spiega perché
        note = "cartoon disegna solo catene proteiche: su un bilayer di soli lipidi non c'è nulla da disegnare.";
        return;
      }
      if (mainRepr) compRef.removeRepresentation(mainRepr); // SOLO la principale
      mainRepr = compRef.addRepresentation(name, mainParams(name, compRef));
    } catch (e) {
      // stile non applicabile al sistema in scena (es. cartoon su lipidi puri
      // in qualche build): torniamo al precedente, la scena resta valida
      style = prev;
      try {
        if (compRef) mainRepr = compRef.addRepresentation(prev, mainParams(prev, compRef));
      } catch {
        mainRepr = null;
      }
    }
  }

  function resetView() {
    if (compRef) compRef.autoView();
  }

  function decorate(comp) {
    compRef = comp;
    // solvente/ioni veri della STRUTTURA corrente: se non ce ne sono il
    // toggle acqua/ioni si disabilita (niente riferimenti fantasma)
    const names = solventResNames(comp.structure);
    hasSolvent = names.length > 0;
    let effStyle = style;
    if (style === "cartoon" && !hasProtein()) {
      // nuovo sistema senza backbone (es. si torna al sample): il cartoon
      // lascerebbe la scena vuota → spacefill e nota onesta
      effStyle = "spacefill";
      style = "spacefill";
      note = "cartoon non applicabile a questo sistema (nessuna proteina): spacefill attivo.";
    }
    mainRepr = comp.addRepresentation(effStyle, mainParams(effStyle, comp));
    if (hasSolvent) {
      waterRepr = comp.addRepresentation("spacefill", {
        sele: names.join(" "),
        radius: 0.3,
        opacity: 0.6,
      });
    } else {
      waterRepr = null;
      showWater = false;
    }
    if (waterRepr) waterRepr.setVisibility(showWater);
    comp.autoView();
  }

  async function load(key, url) {
    if (!stage) return;
    lastRequested = key;
    stage.removeAllComponents();
    waterRepr = null;
    mainRepr = null;
    compRef = null;
    state = "loading";
    try {
      // /api/sample/pdb non ha estensione nell'URL: NGL deduce il formato
      // dall'ultima parte del path → serve dichiararlo, altrimenti errore
      const comp = await stage.loadFile(url, {
        defaultRepresentation: false,
        ...(url === SAMPLE ? { ext: "pdb" } : {}),
      });
      if (key !== lastRequested) return; // sistema cambiato durante l'await: scarta
      decorate(comp);
      state = "ready";
    } catch (e) {
      if (key !== lastRequested) return; // race: il messaggio vale solo per l'ultima richiesta
      state = "error";
      message = String(e?.message ?? e);
    }
  }

  function toggleWater() {
    if (!hasSolvent || !waterRepr) return;
    showWater = !showWater;
    waterRepr.setVisibility(showWater);
  }

  $effect(() => {
    if (!stage) return;
    if (jobid) {
      isSample = false; // il badge sparirà al load: il job reale è quello vero
      if (jobid === lastLoaded) return;
      lastLoaded = jobid;
      load(jobid, `/api/files/${jobid}/system/step1_pdbreader.pdb`);
    } else {
      // nessun sistema estratto: struttura campione locale in scena
      isSample = true;
      if (lastLoaded === SAMPLE) return;
      lastLoaded = SAMPLE;
      load(SAMPLE, SAMPLE);
    }
  });
</script>

<div class="relative min-h-[70vh] overflow-hidden rounded-lg border border-zinc-800 bg-[#0b0f1a]">
  <div bind:this={holder} class="absolute inset-0"></div>

  {#if state !== "ready"}
    <div class="pointer-events-none absolute inset-0 flex items-center justify-center px-14 text-center">
      <p class="text-sm text-zinc-400">
        {#if state === "loading"}
          carico la struttura…
        {:else if state === "error"}
          {message || "errore sconosciuto"}
        {/if}
      </p>
    </div>
  {/if}

  {#if isSample}
    <div class="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 rounded border border-amber-400 bg-amber-100/90 px-3 py-1 text-center text-xs text-amber-900 backdrop-blur">
      <span class="font-semibold tracking-wide">STRUTTURA CAMPIONE</span>
      <span class="text-amber-700"> — demo locale, non il tuo sistema</span>
    </div>
  {/if}

  {#if hint}
    <div class="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded bg-white/80 px-3 py-1 text-xs text-zinc-700 backdrop-blur">
      trascina = ruota · rotella = zoom · tasto destro = sposta
    </div>
  {/if}

  <!-- pannello controlli (in basso a sinistra); collassabile col ⚙ -->
  <div
    class="absolute left-3 bottom-3 z-10 flex items-end gap-2 transition-opacity"
    class:opacity-0={state !== "ready"}
    class:pointer-events-none={state !== "ready"}
  >
    {#if panelOpen}
      <div
        class="flex flex-col gap-1.5 rounded bg-white/85 p-2 text-xs text-zinc-700 backdrop-blur"
        role="group"
        aria-label="Controlli vista 3D"
      >
        <label class="flex items-center gap-1.5">
          <span class="text-zinc-500">stile</span>
          <select
            bind:value={style}
            onchange={() => setStyle(style)}
            aria-label="Stile di rappresentazione"
            class="w-[7.5rem] rounded border border-zinc-300 bg-white px-1 py-0.5 text-xs text-zinc-800 focus:border-cyan-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
          >
            <option value="spacefill">spacefill</option>
            <option value="ball+stick">ball+stick</option>
            <option value="licorice">licorice</option>
            <option value="cartoon">cartoon</option>
          </select>
        </label>
        {#if note}
          <p class="max-w-[13rem] text-[11px] leading-snug text-amber-700" role="status">{note}</p>
        {/if}
        <button
          type="button"
          class="rounded border border-zinc-300 px-1.5 py-0.5 text-left hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
          onclick={resetView}
        >
          reimposta vista
        </button>
      </div>
    {/if}
    <button
      type="button"
      class="rounded bg-white/85 px-2 py-1 text-xs text-zinc-700 backdrop-blur hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
      onclick={() => (panelOpen = !panelOpen)}
      aria-pressed={panelOpen}
      aria-label="Mostra/nascondi controlli vista 3D"
      title="controlli vista"
    >
      ⚙
    </button>
  </div>

  <label
    class="absolute right-3 bottom-3 flex cursor-pointer items-center gap-2 rounded bg-white/85 px-2 py-1 text-xs text-zinc-700 backdrop-blur transition-opacity focus-within:ring-2 focus-within:ring-cyan-500"
    class:opacity-0={state !== "ready"}
    class:pointer-events-none={state !== "ready"}
  >
    <input
      type="checkbox"
      class="accent-cyan-500 disabled:opacity-40 disabled:cursor-not-allowed"
      name="show-water"
      aria-label="Mostra acqua e ioni"
      aria-disabled={!hasSolvent}
      title={hasSolvent ? "mostra/nascondi acqua e ioni" : "questo sistema non contiene solvente"}
      disabled={!hasSolvent}
      checked={showWater}
      onchange={toggleWater}
    />
    acqua/ioni
  </label>
</div>
