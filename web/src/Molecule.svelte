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
  let lastLoaded = null;
  let lastRequested = null; // il check corre dopo l'await: evita la race del cambio job
  let waterRepr = null;
  let compRef = null; // componente corrente in scena (sample o job reale)
  let mainRepr = null; // UNA sola rappresentazione principale (il solvente è a parte)

  const SELE_CLEAN = "not (water or resname SOD POT CLA NA K ION)";
  const SELE_SOLVENT = "water or resname SOD POT CLA NA K ION";
  const SAMPLE = "/api/sample/pdb";

  onMount(() => {
    if (!window.NGL) {
      state = "error";
      message = "NGL non disponibile (manca web/public/vendor/ngl.js)";
      return;
    }
    stage = new window.NGL.Stage(holder, { backgroundColor: "#0b0f1a" });
    // su schermo stretto i controlli partono chiusi (il ⚙ li riapre)
    if (typeof window.innerWidth === "number" && window.innerWidth < 640) panelOpen = false;
    hint = true;
    const hintTimer = setTimeout(() => (hint = false), 5000);
    return () => {
      clearTimeout(hintTimer);
      stage.removeAllComponents();
      stage.dispose();
      stage = null;
    };
  });

  function mainParams(name) {
    if (name === "ball+stick") return { sele: SELE_CLEAN }; // NGL usa il «+», non il tratteggio
    if (name === "licorice") return { sele: SELE_CLEAN, radius: 0.25 };
    if (name === "cartoon") return { sele: SELE_CLEAN };
    return { sele: SELE_CLEAN, radius: "schematic" }; // spacefill (default)
  }

  function setStyle(name) {
    const prev = style;
    style = name;
    try {
      if (!compRef) return;
      if (mainRepr) compRef.removeRepresentation(mainRepr); // SOLO la principale
      mainRepr = compRef.addRepresentation(name, mainParams(name));
    } catch (e) {
      // stile non applicabile al sistema in scena (es. cartoon su lipidi puri
      // in qualche build): torniamo al precedente, la scena resta valida
      style = prev;
      try {
        if (compRef) mainRepr = compRef.addRepresentation(prev, mainParams(prev));
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
    mainRepr = comp.addRepresentation(style, mainParams(style));
    waterRepr = comp.addRepresentation("spacefill", {
      sele: SELE_SOLVENT,
      radius: 0.3,
      opacity: 0.6,
    });
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
    showWater = !showWater;
    if (waterRepr) waterRepr.setVisibility(showWater);
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
    <div class="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 rounded border border-amber-500/70 bg-amber-950/85 px-3 py-1 text-center text-xs text-amber-200 backdrop-blur">
      <span class="font-semibold tracking-wide">STRUTTURA CAMPIONE</span>
      <span class="text-amber-300/80"> — demo locale, non il tuo sistema</span>
    </div>
  {/if}

  {#if hint}
    <div class="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded bg-black/70 px-3 py-1 text-xs text-zinc-300 backdrop-blur">
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
        class="flex flex-col gap-1.5 rounded bg-black/60 p-2 text-xs text-zinc-200 backdrop-blur"
        role="group"
        aria-label="Controlli vista 3D"
      >
        <label class="flex items-center gap-1.5">
          <span class="text-zinc-400">stile</span>
          <select
            bind:value={style}
            onchange={() => setStyle(style)}
            aria-label="Stile di rappresentazione"
            class="w-[7.5rem] rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 text-xs text-zinc-100 focus:border-cyan-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
          >
            <option value="spacefill">spacefill</option>
            <option value="ball+stick">ball+stick</option>
            <option value="licorice">licorice</option>
            <option value="cartoon">cartoon</option>
          </select>
        </label>
        <button
          type="button"
          class="rounded border border-zinc-600 px-1.5 py-0.5 text-left hover:bg-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
          onclick={resetView}
        >
          reimposta vista
        </button>
      </div>
    {/if}
    <button
      type="button"
      class="rounded bg-black/60 px-2 py-1 text-xs text-zinc-200 backdrop-blur hover:bg-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
      onclick={() => (panelOpen = !panelOpen)}
      aria-pressed={panelOpen}
      aria-label="Mostra/nascondi controlli vista 3D"
      title="controlli vista"
    >
      ⚙
    </button>
  </div>

  <label
    class="absolute right-3 bottom-3 flex cursor-pointer items-center gap-2 rounded bg-black/60 px-2 py-1 text-xs text-zinc-200 backdrop-blur transition-opacity focus-within:ring-2 focus-within:ring-cyan-500"
    class:opacity-0={state !== "ready"}
    class:pointer-events-none={state !== "ready"}
  >
    <input
      type="checkbox"
      class="accent-cyan-500"
      name="show-water"
      aria-label="Mostra acqua e ioni"
      checked={showWater}
      onchange={toggleWater}
    />
    acqua/ioni
  </label>
</div>
