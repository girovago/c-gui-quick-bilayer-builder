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
  let lastLoaded = null;
  let lastRequested = null; // il check corre dopo l'await: evita la race del cambio job
  let waterRepr = null;

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
    return () => {
      stage.removeAllComponents();
      stage.dispose();
      stage = null;
    };
  });

  function decorate(comp) {
    comp.addRepresentation("spacefill", { sele: SELE_CLEAN, radius: "schematic" });
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
    state = "loading";
    try {
      const comp = await stage.loadFile(url, { defaultRepresentation: false });
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
