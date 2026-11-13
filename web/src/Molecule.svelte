<script>
  // Vista 3D NGL: stage unico per component-mount (removeAllComponents al cambio
  // job, mai recreate del canvas → niente leak WebGPU/WebGL). NGL = dist UMD
  // vendORIZZATA in web/public/vendor/ngl.js (three.js incluso, no CDN).
  import { onMount } from "svelte";

  let { jobid = null, compact = false } = $props();

  let holder = $state(null);
  // TRAPPOLA Svelte 5 (11/11/2026): lo Stage NGL NON va in $state — Svelte lo
  // incapsula in un Proxy reattivo PROFONDO (gruppo di scena three.js, migliaia
  // di oggetti atomo) e ogni accesso passa per il proxy = freeze del main
  // thread. Variabile piena + flag $state "ready" solo per la reattività.
  let stage = null;
  let stageReady = $state(false);
  let state = $state("idle"); // idle | loading | ready | error
  let message = $state("");
  let showWater = $state(false);
  let showIons = $state(false);
  let hasWater = $state(false); // il sistema corrente ha acqua
  let hasIons = $state(false); // il sistema corrente ha ioni
  let isSample = $state(false); // TRUE = demo locale in scena (badge giallo)
  let isLocal = $state(false); // TRUE = build GROMACS generato in locale (badge azzurro)
  let style = $state("spacefill"); // stile della rappresentazione principale
  let hint = $state(false); // pillola hint mouse (5s dal mount)
  let note = $state(""); // messaggio onesto su stile non applicabile (es. cartoon su lipidi puri)
  let lastLoaded = null;
  let lastRequested = null; // il check corre dopo l'await: evita la race del cambio job
  let waterRepr = null;
  let ionRepr = null;
  let compRef = null; // componente corrente in scena (sample o job reale)
  let mainRepr = null; // UNA sola rappresentazione principale (solvente e ioni sono a parte)

  // TRAPPOLA verificata dal vivo (26/10, NGL vendORIZZATO): nelle selezioni le
  // keyword (`resname`, `element`…) tornano il set COMPLETO e — peggio — un
  // resname elencato che NON esiste nella struttura avvelena l'intera selezione
  // portandola al set completo (es. 'not (TIP3C TIP ...)' → tutti gli atomi).
  // Quindi niente selezioni statiche: i nomi solvente vengono presi SOLO
  // dall'intersezione tra un elenco canonico e i resname davvero presenti
  // (letti da structure.eachResidue), e se non ce ne sono il toggle si disabilita.
  const WATER_CANON = new Set([
    "TIP3", "TIP3C", "TIP", "TIPJ", "HOH", "WAT", "SOL", // acqua (CHARMM/PDB)
  ]);
  const ION_CANON = new Set([
    "SOD", "POT", "CLA", "CAL", "ZN", "ZN2", "MG", "LI", "NA", "K", "CL", // ioni CHARMM
  ]);
  const SAMPLE = "/api/sample/pdb";

  // resname presenti nella struttura filtrati su un elenco canonico (trappola
  // 26/10: nomi assenti avvelenano la selezione NGL → solo nomi REALI)
  function namesOf(st, canon) {
    try {
      const present = new Set();
      st.eachResidue(function (rp) { if (rp.resname) present.add(rp.resname); });
      return [...canon].filter((n) => present.has(n)).sort();
    } catch {
      return [];
    }
  }

  function solventResNames(st) {
    return namesOf(st, new Set([...WATER_CANON, ...ION_CANON]));
  }

  // NGL: i token separati da spazio sono AND, non OR ("SOL NA CL" = insieme
  // vuoto → repr invisibile). La forma corretta è "SOL or NA or CL" (bare
  // token = resname).
  function orSele(names) {
    return names.join(" or ");
  }

  onMount(() => {
    if (!window.NGL) {
      state = "error";
      message = "NGL non disponibile (manca web/public/vendor/ngl.js)";
      return;
    }
    // quality "low" esplicito (non "auto"): su iGPU vecchie (es. Intel HD 4400)
    // auto può scegliere medium → rendering software → main thread in loop =
    // popup "la pagina non risponde". Low = impostor semplici, sampleLevel 0.
    stage = new window.NGL.Stage(holder, { backgroundColor: "#0b0f1a", quality: "low" });
    // —— THROTTLE LOOP NGL (11/11/2026) —— NGL si ri-schedula a ogni frame e
    // dopo 500ms esegue un render "still" supersamplato (sampleLevel 3): con
    // WebGL software (iGPU vecchie, Firefox senza accelerazione) la CPU resta
    // al 70-100% e il browser apre a raffica «la pagina non risponde».
    // Fix DOPPIO:
    //   1. il nostro animate replica NGL (tick signal + reschedule) ma SALTA
    //      il render supersamplato: i frame normali passano per update()
    //      (render-on-demand), l'unico che fissa il supersample è il branch
    //      dentro animate() → rimosso, la scena resta identica ma senza il
    //      task da molti secondi su SwiftShader;
    //   2. a riposo (3s senza interazioni o tab nascosta) la catena rAF si
    //      SPEGNE del tutto; si riaccende su interazione o nuova load.
    const viewer = stage.viewer;
    viewer.__paused = true; // parte in pausa finché la prima load non finisce
    viewer.animate = () => {
      if (viewer.__paused) return; // catena spenta: nessun rAF futuro
      try {
        viewer.signals.ticked.dispatch(viewer.stats);
      } catch {}
      viewer.frameRequest = window.requestAnimationFrame(viewer.animate);
    };
    stage.__resume = () => {
      clearTimeout(viewer.__idleTimer);
      if (viewer.__paused) { viewer.__paused = false; viewer.animate(); }
      viewer.__idleTimer = setTimeout(() => (viewer.__paused = true), 3000);
    };
    stage.__pause = () => { clearTimeout(viewer.__idleTimer); viewer.__paused = true; };
    const onVis = () => {
      if (!stage || !stage.viewer) return;
      if (document.hidden) stage.__pause();
      else stage.__resume();
    };
    document.addEventListener("visibilitychange", onVis);
    stageReady = true; // abilita l'$effect di caricamento (stage è variabile piena, non reattiva)
    window.__ngl_stage = stage; // probe debug da tools.browser
    hint = true;
    const hintTimer = setTimeout(() => (hint = false), 5000);
    return () => {
      clearTimeout(hintTimer);
      clearTimeout(viewer.__idleTimer);
      document.removeEventListener("visibilitychange", onVis);
      stageReady = false;
      stage.removeAllComponents();
      stage.dispose();
      stage = null;
      delete window.__ngl_stage;
    };
  });

  function mainParams(name, comp) {
    // colorScheme: per sistemi di soli lipidi uso "resname" (POPC/CHL1 con
    // colori distinti: il carbonio grigio di default su sfondo scuro era
    // quasi invisibile); per sistemi con proteine resta "chainname"
    const scheme = comp && hasProtein() ? "chainname" : "resname";
    // esclusione solvente dinamica: solo resname presenti, con OR esplicito
    // (spazio = AND in NGL: "not (SOL NA CL)" = not(vuoto) = TUTTO — bug toggle)
    const names = comp ? solventResNames(comp.structure) : [];
    const clean = names.length ? `not (${orSele(names)})` : "";
    // cartoon: solo 'protein' (keyword che funziona e già esclude il solvente)
    if (name === "ball+stick") return clean ? { sele: clean, colorScheme: scheme } : { colorScheme: scheme }; // NGL usa il «+», non il tratteggio
    if (name === "licorice") return clean ? { sele: clean, radius: 0.25, colorScheme: scheme } : { radius: 0.25, colorScheme: scheme };
    if (name === "line") return clean ? { sele: clean, colorScheme: scheme } : { colorScheme: scheme };
    if (name === "cartoon") return { sele: "protein" };
    if (name === "surface") {
      // superficie molecolare del solo soluto (senza solvente): MSMS pesa
      // sull'acqua, quindi l'esclusione qui è anche una questione di performance
      const p = { opacity: 0.85, smooth: 3, colorScheme: scheme };
      if (clean) p.sele = clean;
      return p;
    }
    const p = { radius: "schematic", colorScheme: scheme }; // spacefill (default)
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
    stage?.__resume?.();
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

  let homeCamera = null; // camera memorizzata al load (TRAPPA: autoView NGL
  // su questa build non re-centra — non si notava perché il builder centra a 0)

  function resetView() {
    // autoView() di NGL non recentra qui (verificato 13/11: camera invariata
    // dopo pan manuale): ripristino la camera memorizzata al load della
    // struttura, che per il builder (centrato a 0,0,0) e' la vista giusta
    if (homeCamera && stage) {
      try {
        stage.viewer.camera.position.copy(homeCamera);
        stage.viewer.update && stage.viewer.update();
        stage.__resume?.();
        return;
      } catch {}
    }
    if (compRef) compRef.autoView();
    stage?.__resume?.();
  }

  function decorate(comp) {
    compRef = comp;
    // acqua e ioni SEPARATI (come RCSB): toggle indipendenti, default = visibili
    const water = namesOf(comp.structure, WATER_CANON);
    const ions = namesOf(comp.structure, ION_CANON);
    hasWater = water.length > 0;
    hasIons = ions.length > 0;
    showWater = hasWater;
    showIons = hasIons;
    let effStyle = style;
    if (style === "cartoon" && !hasProtein()) {
      // nuovo sistema senza backbone (es. si torna al sample): il cartoon
      // lascerebbe la scena vuota → spacefill e nota onesta
      effStyle = "spacefill";
      style = "spacefill";
      note = "cartoon non applicabile a questo sistema (nessuna proteina): spacefill attivo.";
    }
    mainRepr = comp.addRepresentation(effStyle, mainParams(effStyle, comp));
    if (hasWater) {
      waterRepr = comp.addRepresentation("spacefill", {
        sele: orSele(water),
        radius: 0.3,
        opacity: 0.55,
      });
    } else {
      waterRepr = null;
    }
    if (hasIons) {
      ionRepr = comp.addRepresentation("spacefill", {
        sele: orSele(ions),
        radius: 0.45,
        opacity: 0.95,
      });
    } else {
      ionRepr = null;
    }
    if (waterRepr) waterRepr.setVisibility(showWater);
    if (ionRepr) ionRepr.setVisibility(showIons);
    // memorizza la camera al load (autView non re-centra su questa build)
    try { homeCamera = stage.viewer.camera.position.clone(); } catch {}
    comp.autoView();
  }

  async function load(key, url) {
    if (!stage) return;
    lastRequested = key;
    stage.__resume?.(); // serve il loop per l'animazione di ingresso
    stage.removeAllComponents();
    // reset camera al load: la home e' quella iniziale (il builder centra la
    // struttura a 0,0,0) — autoView NGL non re-centra su questa build
    try {
      stage.viewer.camera.position.set(0, 0, -117.66);
      stage.viewer.update && stage.viewer.update();
    } catch {}
    waterRepr = null;
    ionRepr = null;
    mainRepr = null;
    compRef = null;
    showWater = false;
    showIons = false;
    state = "loading";
    // badge: sample (giallo) vs build locale (azzurro) — esclusivi
    isSample = false;
    isLocal = String(key).startsWith("local-");
    try {
      // /api/sample/pdb e /api/files/local-*/.../step1_pdbreader.pdb possono
      // non avere estensione riconoscibile da NGL: dichiaro ext pdb
      const comp = await stage.loadFile(url, {
        defaultRepresentation: false,
        ...(url === SAMPLE || isLocal ? { ext: "pdb" } : {}),
      });
      if (key !== lastRequested) return; // sistema cambiato durante l'await: scarta
      isSample = key === SAMPLE; // FIX 11/11: il badge sample spariva (load lo azzerava e non lo risettava)
      decorate(comp);
      state = "ready";
      stage.__resume?.(); // autoView anima, poi il loop si spegne da solo (3s)
    } catch (e) {
      if (key !== lastRequested) return; // race: il messaggio vale solo per l'ultima richiesta
      // step5_system.pdb = sistema completo (lipidi+solvente+ioni); se il build
      // non l'ha prodotto (no solvate o vecchio build) torno su step1 (solo lipidi)
      if (String(url).endsWith("step5_system.pdb")) {
        load(key, `/api/files/${key}/step1_pdbreader.pdb`);
        return;
      }
      state = "error";
      message = String(e?.message ?? e);
    }
  }

  function toggleWater() {
    if (!waterRepr) return;
    showWater = !showWater;
    waterRepr.setVisibility(showWater);
    stage?.__resume?.();
  }

  function toggleIons() {
    if (!ionRepr) return;
    showIons = !showIons;
    ionRepr.setVisibility(showIons);
    stage?.__resume?.();
  }

  async function snapshot() {
    if (!stage) return;
    stage?.__resume?.();
    try {
      const blob = await stage.makeImage({
        factor: 2, // risoluzione 2x: PNG nitido per presentazioni
        antialias: true,
        trim: true,
        transparent: false,
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `cgui_${isSample ? "sample" : (jobid || "vista")}.png`;
      a.click();
      URL.revokeObjectURL(a.href);
      note = "";
    } catch (e) {
      note = `snapshot non riuscito: ${String(e?.message ?? e)}`;
    }
  }

  $effect(() => {
    if (!stageReady || !stage) return;
    if (jobid) {
      isSample = false; // il badge sparirà al load: il job reale è quello vero
      if (jobid === lastLoaded) return;
      lastLoaded = jobid;
      // sistema COMPLETO con solvente+ioni se il build l'ha prodotto,
      // altrimenti fallback automatico su step1 (solo lipidi)
      load(jobid, `/api/files/${jobid}/step5_system.pdb`);
    } else {
      // nessun sistema estratto: struttura campione locale in scena
      isSample = true;
      if (lastLoaded === SAMPLE) return;
      lastLoaded = SAMPLE;
      load(SAMPLE, SAMPLE);
    }
  });
</script>

<div class="relative {compact ? "min-h-[45vh]" : "min-h-[80vh]"} overflow-hidden rounded-lg border border-zinc-800 bg-[#0b0f1a]">
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
  {#if isLocal}
    <div class="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 rounded border border-cyan-400 bg-cyan-100/90 px-3 py-1 text-center text-xs text-cyan-900 backdrop-blur">
      <span class="font-semibold tracking-wide">STRUTTURA LOCALE</span>
      <span class="text-cyan-700"> — build GROMACS in locale, nessuna coda CHARMM-GUI</span>
    </div>
  {/if}

  {#if hint}
    <div class="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded bg-white/80 px-3 py-1 text-xs text-zinc-700 backdrop-blur">
      trascina = ruota · rotella = zoom · tasto destro = sposta
    </div>
  {/if}

  <!-- toolbar strumenti (stile RCSB/AlphaFold): compatta, in alto a destra -->
  <div
    class="absolute top-2 right-2 z-10 flex flex-wrap items-center justify-end gap-1.5 rounded bg-white/85 p-1.5 text-xs text-zinc-700 backdrop-blur transition-opacity"
    class:opacity-0={state !== "ready"}
    class:pointer-events-none={state !== "ready"}
    role="group"
    aria-label="Strumenti vista 3D"
  >
    <label class="flex items-center gap-1">
      <span class="text-zinc-500">stile</span>
      <select
        bind:value={style}
        onchange={() => setStyle(style)}
        aria-label="Stile di rappresentazione"
        title="sfere = spacefill · ball+stick = sfere e bastoncini · licorice = solo bastoncini · linee = skeleton · superficie = molecular surface · cartoon = backbone (serve una proteina)"
        class="max-w-[8rem] rounded border border-zinc-300 bg-white px-1 py-0.5 text-xs text-zinc-800 focus:border-cyan-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
      >
        <option value="spacefill">sfere</option>
        <option value="ball+stick">ball+stick</option>
        <option value="licorice">licorice</option>
        <option value="line">linee</option>
        <option value="surface">superficie</option>
        <option value="cartoon">cartoon</option>
      </select>
    </label>
    {#if hasWater}
      <label
        class="flex cursor-pointer items-center gap-1 rounded border border-zinc-200 px-1.5 py-0.5 hover:bg-zinc-100 focus-within:outline focus-within:outline-2 focus-within:outline-cyan-400"
        title="mostra/nascondi l'acqua del solvente"
      >
        <input
          type="checkbox"
          class="accent-cyan-500"
          name="show-water"
          aria-label="Mostra acqua"
          checked={showWater}
          onchange={toggleWater}
        />
        acqua
      </label>
    {/if}
    {#if hasIons}
      <label
        class="flex cursor-pointer items-center gap-1 rounded border border-zinc-200 px-1.5 py-0.5 hover:bg-zinc-100 focus-within:outline focus-within:outline-2 focus-within:outline-cyan-400"
        title="mostra/nascondi gli ioni (NaCl/KCl)"
      >
        <input
          type="checkbox"
          class="accent-violet-500"
          name="show-ions"
          aria-label="Mostra ioni"
          checked={showIons}
          onchange={toggleIons}
        />
        ioni
      </label>
    {/if}
    <button
      type="button"
      class="min-h-6 rounded border border-zinc-300 px-2 py-1 hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
      onclick={resetView}
      title="Riporta la scena al centro e allo zoom originale"
    >
      vista
    </button>
    <button
      type="button"
      class="min-h-6 rounded border border-zinc-300 px-2 py-1 hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
      onclick={snapshot}
      title="Scarica un PNG della vista corrente"
    >
      📷
    </button>
  </div>

  {#if note}
    <p
      class="absolute bottom-3 left-3 z-10 max-w-[18rem] rounded bg-amber-100/90 px-2 py-1 text-[11px] leading-snug text-amber-800 backdrop-blur"
      role="status"
    >{note}</p>
  {/if}
</div>
