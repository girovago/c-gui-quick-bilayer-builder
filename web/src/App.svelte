<script>
  // App a 3 colonne: form wizard | vista 3D | lista job + dettaglio.
  // Su schermo stretto le colonne si impilano. Polling: lista 10s, dettaglio
  // adattivo (2s MOCK/running fresco, 20s altrimenti; pausa in background).
  import { onMount } from "svelte";
  import Molecule from "./Molecule.svelte";
  import SpecSketch from "./SpecSketch.svelte";
  import BatchPanel from "./BatchPanel.svelte";
  import { api, formToPayload, isFormValid, parseLipidText, lipidRe } from "./api.js";

  // ------------------------------------------------------------------ form state
  const blankForm = () => ({
    label: "",
    upper: "POPC=64",
    lower: "POPC=64",
    membtype: "",
    margin: 20,
    wdist: 1,
    ion_conc: 0.15,
    ion_type: "NaCl",
    membrane_only: true,
    run_ffconverter: true,
    clone_job: "",
  });
  let form = $state(blankForm());
  let valid = $derived(isFormValid(form));

  const MEMB_PRESETS = ["PMm", "aPM", "ENDm", "ENDf", "ENDp", "BTm", "BTf", "BTp", "GMm", "YMm", "ERm"];

  // ------------------------------------------------------------------ jobs state
  let jobs = $state([]);
  let selectedId = $state(null);
  let detail = $state(null);
  let files = $state([]);
  let statusMsg = $state("");
  let toast = $state(null);
  let busy = $state(false);
  let localBusy = $state(false);
  let previewOpen = $state(false);
  let previewData = $state(null);
  let mockMode = $state(false);
  let panel = $state("single"); // "single" | "batch" — i pannelli restano MONTATI (hidden):
  // cambiare tab non distrugge la coda batch (che vive in-session)

  const record = $derived(detail?.record ?? null);
  const status = $derived(detail?.status ?? null);
  const upperOk = $derived(
    form.membtype !== "" || (parseLipidText(form.upper).every((l) => lipidRe.test(l)) && parseLipidText(form.lower).every((l) => lipidRe.test(l)))
  );

  function flash(kind, text) {
    toast = { kind, text };
    clearTimeout(flash._t);
    flash._t = setTimeout(() => (toast = null), 5000);
  }

  // ------------------------------------------------------------------ API
  async function refreshJobs() {
    try {
      jobs = await api("/api/jobs");
    } catch {
      /* polling silenzioso */
    }
  }

  async function refreshDetail() {
    if (!selectedId) {
      detail = null;
      files = [];
      return;
    }
    try {
      detail = await api(`/api/jobs/${selectedId}`);
    } catch {
      /* job non interrogabile ora (404/token): tenant lo stato precedente, il polling riprova */
      return;
    }
    // Albero file: solo quando cambia outdir/archive_sha256 (o dopo un fetch
    // fallito). Niente 1 GET files extra a ogni tick.
    const key = filesKey(detail);
    if (!key) {
      files = [];
      filesDirty = true;
      return;
    }
    if (filesDirty && key !== lastFilesKey) {
      lastFilesKey = key;
      try {
        files = (await api(`/api/files/${selectedId}`)).files ?? [];
        filesDirty = false;
      } catch {
        files = [];
        lastFilesKey = ""; // il prossimo fetch riuscito riprova l'albero
      }
    }
  }

  async function submitJob() {
    busy = true;
    try {
      const res = await api("/api/jobs", { method: "POST", body: JSON.stringify(formToPayload(form)) });
      statusMsg = `inviato: ${res.jobid}`;
      flash("ok", `Job ${res.jobid} inviato`);
      selectedId = res.jobid;
      detail = null;
      await refreshJobs();
    } catch (e) {
      flash("err", e.message);
    } finally {
      busy = false;
    }
  }

  async function makeLocal() {
    localBusy = true;
    try {
      const body = { run_gmx: true };
      if (form.membtype === "") {
        body.spec = `${form.upper};${form.lower}`;
      } else {
        body.spec = form.membtype;
      }
      const res = await api("/api/make-bilayer", {
        method: "POST",
        body: JSON.stringify(body),
      });
      flash("ok", `Struttura locale: ${res.jobid} · ${res.files.length} file`);
      selectedId = res.jobid;
      detail = null;
      await refreshDetail();
      await refreshJobs();
    } catch (e) {
      flash("err", e.message);
    } finally {
      localBusy = false;
    }
  }

  async function fetchJob(id) {
    busy = true;
    try {
      const res = await api(`/api/jobs/${id}/fetch`, { method: "POST", body: JSON.stringify({ wait: false }) });
      flash("ok", `Estratti ${res.files.length} file · sha256 ${res.sha256.slice(0, 12)}…`);
      selectedId = id;
      await refreshDetail();
      await refreshJobs();
    } catch (e) {
      flash("err", e.message);
    } finally {
      busy = false;
    }
  }

  async function showPreview() {
    previewOpen = !previewOpen;
    if (previewOpen) {
      try {
        previewData = await api("/api/form-preview", { method: "POST", body: JSON.stringify(formToPayload(form)) });
      } catch (e) {
        flash("err", e.message);
      }
    }
  }

  function cloneToForm(rec) {
    const spec = rec.spec_dict ?? JSON.parse(rec.spec_json ?? "{}");
    form = {
      label: spec.label ?? "",
      upper: typeof spec.upper === "string" ? spec.upper : "",
      lower: typeof spec.lower === "string" ? spec.lower : "",
      membtype: spec.membtype ?? "",
      margin: spec.margin ?? 20,
      wdist: spec.wdist ?? 1,
      ion_conc: spec.ion_conc ?? 0.15,
      ion_type: spec.ion_type ?? "NaCl",
      membrane_only: spec.membrane_only ?? true,
      run_ffconverter: spec.run_ffconverter ?? true,
      clone_job: spec.clone_job_from ?? "",
    };
    flash("ok", "Campi ripresi nel form (clone server-side disattivo nell'MVP)");
  }

  function selectJob(id) {
    selectedId = id;
    detail = null;
    files = [];
    filesDirty = true;
  }

  /** Callback del BatchPanel: evidenzia il job nella lista e la aggiorna subito. */
  function pickAndRefresh(id) {
    selectJob(id);
    refreshJobs();
  }

  // ------------------------------------------------------------------ polling
  // Fix verifier (BUG 2): niente martelletta fissa a 2s sull'API reale.
  // - pausa completa della tab: document.hidden (visibilitychange);
  // - intervallo adattivo: 2s solo in MOCK o se il job è "running" da <2min,
  //   altrimenti 20s;
  // - l'albero /api/files/{id} si ricarica solo al cambio di
  //   outdir/archive_sha256 o dopo un fetch fallito (filesKey/filesDirty).
  let lastFilesKey = "";
  let filesDirty = true;
  let runningSince = null; // epoch ms da cui il job selezionato è "running"
  let detailTimer = null;
  let jobsTimer = null;

  function filesKey(d) {
    const r = d?.record;
    return r?.outdir ? `${r.outdir}|${r.archive_sha256 ?? ""}` : "";
  }

  function detailInterval() {
    if (mockMode) return 2000;
    const st = detail?.status?.status;
    if (st === "running") {
      if (runningSince === null) runningSince = Date.now();
      return Date.now() - runningSince < 120_000 ? 2000 : 20000;
    }
    runningSince = null;
    return 20000;
  }

  function tickDetail() {
    refreshDetail().finally(scheduleDetail);
  }
  function tickJobs() {
    refreshJobs().finally(scheduleJobs);
  }
  function scheduleDetail() {
    clearTimeout(detailTimer);
    if (document.hidden) return; // tab in background: nessuna chiamata
    detailTimer = setTimeout(tickDetail, detailInterval());
  }
  function scheduleJobs() {
    clearTimeout(jobsTimer);
    if (document.hidden) return;
    jobsTimer = setTimeout(tickJobs, 10000);
  }
  function onVisibility() {
    if (document.hidden) return; // tab nascosta: i timer restano fermi
    // tab tornata in primo piano: aggiorna subito e riparte il ciclo
    refreshDetail();
    scheduleDetail();
    refreshJobs();
    scheduleJobs();
  }

  onMount(() => {
    refreshJobs();
    refreshDetail();
    api("/api/ping")
      .then((p) => {
        mockMode = Boolean(p.mock);
        scheduleDetail(); // l'intervallo va ricalcolato ora che mock è noto
      })
      .catch(() => {});
    document.addEventListener("visibilitychange", onVisibility);
    scheduleJobs();
    scheduleDetail();
    return () => {
      clearTimeout(detailTimer);
      clearTimeout(jobsTimer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  });
</script>

<div class="mx-auto max-w-[1600px] px-4 py-4 lg:px-6">
  <header class="mb-4 flex items-baseline justify-between gap-4">
    <h1 class="text-lg font-semibold tracking-tight text-zinc-800">
      cgui-studio <span class="text-zinc-500">· Quick Bilayer wizard</span>
    </h1>
    <span class="rounded-full border border-cyan-300 px-2 py-0.5 text-xs text-cyan-700" title="CGUI_MOCK=1: nessuna chiamata reale a charmm-gui.org">
      {mockMode ? "MOCK" : "API reale"}
    </span>
  </header>

  <main class="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-[340px_minmax(0,1fr)_minmax(320px,1fr)]">
    <!-- ============================================ colonna FORM -->
    <section class="min-w-0 rounded-lg border border-zinc-200 bg-white p-3" aria-label="Costruzione">
      <div class="mb-3 flex gap-1" role="tablist" aria-label="Modalità di composizione">
        <button
          type="button"
          role="tab"
          class="rounded px-3 py-1 text-xs font-medium text-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 {panel === 'single'
            ? 'bg-cyan-100 ring-1 ring-cyan-500'
            : 'bg-zinc-200 text-zinc-600 hover:text-zinc-800'}"
          aria-selected={panel === "single"}
          onclick={() => (panel = "single")}
        >
          Singolo
        </button>
        <button
          type="button"
          role="tab"
          class="rounded px-3 py-1 text-xs font-medium text-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 {panel === 'batch'
            ? 'bg-cyan-100 ring-1 ring-cyan-500'
            : 'bg-zinc-200 text-zinc-600 hover:text-zinc-800'}"
          aria-selected={panel === "batch"}
          onclick={() => (panel = "batch")}
        >
          Batch
        </button>
      </div>

      <div class={panel !== "single" && "hidden"} role="tabpanel" aria-label="Wizard singolo">
        <h2 class="mb-2 text-sm font-semibold text-zinc-700">1 · Composizione</h2>
      <label class="mb-1 block text-xs text-zinc-600" for="f-label">etichetta</label>
      <input id="f-label" class="mb-2 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-800 focus:border-cyan-500 focus:outline-none" bind:value={form.label} placeholder="es. POPC64" />

      <fieldset class="mb-2">
        <legend class="mb-1 text-xs text-zinc-500">modalità</legend>
        <div class="flex items-center gap-3 text-xs text-zinc-700">
          <label><input type="radio" name="mode" class="accent-cyan-500" checked={form.membtype === ""} onchange={() => (form.membtype = "")} /> upper/lower</label>
          <label><input type="radio" name="mode" class="accent-cyan-500" checked={form.membtype !== ""} onchange={() => (form.membtype = "PMm")} /> membtype</label>
        </div>
      </fieldset>

      {#if form.membtype === ""}
        <label class="mb-1 block text-xs text-zinc-600" for="f-upper">foglio superiore (upper leaf) — righe NOME=count</label>
        <textarea id="f-upper" rows="2" class="w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-xs uppercase text-zinc-800 focus:border-cyan-500 focus:outline-none" bind:value={form.upper}></textarea>
        <label class="mb-1 mt-2 block text-xs text-zinc-600" for="f-lower">foglio inferiore (lower leaf)</label>
        <textarea id="f-lower" rows="2" class="mb-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-xs uppercase text-zinc-800 focus:border-cyan-500 focus:outline-none" bind:value={form.lower}></textarea>
        <p class="mb-2 text-[11px] leading-snug text-zinc-500">
          es. <code class="text-zinc-600">POPC=64</code> = 64 lipidi POPC su quel foglio · un tipo per riga
          (<code class="text-zinc-600">POPC=48</code> poi <code class="text-zinc-600">CHOL=16</code>) o più tipi su una riga
          (<code class="text-zinc-600">POPC=48;CHOL=16</code>)
        </p>
        {#if !upperOk}
          <p class="mb-2 text-xs text-amber-600">righe non valide: servono NOME=count (es. POPC=64)</p>
        {/if}
      {:else}
        <label class="mb-1 block text-xs text-zinc-600" for="f-membtype">biomembrana predefinita (preset del server)</label>
        <select id="f-membtype" placeholder="Scegli un preset del server" class="mb-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-800" bind:value={form.membtype}>
          {#each MEMB_PRESETS as m (m)}
            <option value={m}>{m}</option>
          {/each}
        </select>
        <p class="mb-2 text-[11px] leading-snug text-zinc-500">
          sostituisce upper/lower con una composizione predefinita dal server (es. POPE/POPG…):
          usala solo per membrane standard, torna a "upper/lower" per comporne una a mano
        </p>
      {/if}

      <h2 class="mb-2 mt-3 text-sm font-semibold text-zinc-700">2 · parametri</h2>
      <div class="grid grid-cols-3 gap-2">
        <label for="f-margin"><span class="mb-1 block text-xs text-zinc-600" title="spazio extra attorno al sistema (Å). Per un build di solo membrana il default 20 va bene">margin (Å)</span>
          <input id="f-margin" type="number" step="0.1" min="0.5" title="spazio extra attorno al sistema (Å). Per un build di solo membrana lascia il default 20" class="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-800" bind:value={form.margin} />
        </label>
        <label for="f-wdist"><span class="mb-1 block text-xs text-zinc-600" title="distanza tra lipidi e teste polari (Å): pilota lo spessore del vano vuoto nel bilayer">wdist (Å)</span>
          <input id="f-wdist" type="number" step="0.1" title="distanza tra lipidi e teste polari (Å): pilota lo spessore del vano vuoto nel bilayer; default 1" class="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-800" bind:value={form.wdist} />
        </label>
        <label for="f-ionconc"><span class="mb-1 block text-xs text-zinc-600" title="concentrazione del sale (mol/L): 0.15 M = fisiologica">ion_conc (M)</span>
          <input id="f-ionconc" type="number" step="0.01" min="0" title="concentrazione del sale (mol/L): 0.15 = fisiologica" class="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-800" bind:value={form.ion_conc} />
        </label>
      </div>
      <label for="f-iontype">
        <span class="mb-1 mt-2 block text-xs text-zinc-600" title="tipo di sale aggiunto per neutralizzare la carica del bilayer e raggiungere la concentrazione richiesta">ion_type</span>
        <select id="f-iontype" class="w-40 rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-800 uppercase" bind:value={form.ion_type} title="tipo di sale, solo NaCl o KCl: neutralizza la carica del sistema">
          <option value="NaCl">NACL</option>
          <option value="KCl">KCL</option>
        </select>
      </label>
      <div class="mt-3 flex flex-col gap-1 text-xs text-zinc-700">
        <label><input type="checkbox" class="accent-cyan-500" bind:checked={form.membrane_only} title="costruisce SOLO il bilayer, senza solvatare/inglobare una proteina da PDB Reader" /> membrane_only — solo bilayer, nessuna proteina</label>
        <label><input type="checkbox" class="accent-cyan-500" bind:checked={form.run_ffconverter} title="genera gli input dei motori MD (GROMACS/AMBER/NAMD) oltre alla struttura; disattiva se serve solo il PDB" /> run_ffconverter — genera input GROMACS/AMBER/NAMD (disattiva se serve solo il PDB)</label>
      </div>

      <div class="mt-4 flex flex-wrap gap-2">
        <button
          class="rounded bg-cyan-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-cyan-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-600"
          onclick={submitJob}
          disabled={!valid || busy}
          aria-disabled={!valid || busy}
          title="Invia la composizione al server CHARMM-GUI — nella coda reale l'attesa può essere molto lunga"
        >
          Invia
        </button>
        <button
          class="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-600"
          onclick={makeLocal}
          disabled={!valid || localBusy}
          aria-disabled={!valid || localBusy}
          title="Costruisce la bilayer in locale con GROMACS (editconf+solvate): nessuna attesa in coda, pronto in secondi"
        >
          Genera locale
        </button>
        {#if localBusy}
          <span class="text-xs text-emerald-700 animate-pulse">build locale in corso…</span>
        {/if}
        <button
          class="rounded border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:opacity-50"
          onclick={showPreview}
          aria-expanded={previewOpen}
          title="Mostra l'esatta richiesta che partirà verso il server (debug)"
        >
          Payload
        </button>
        {#if statusMsg}
          <span class="truncate text-xs text-emerald-700">{statusMsg}</span>
        {/if}
      </div>
      <p class="mt-1 text-[11px] leading-snug text-zinc-500">
        dopo l'invio il job entra nella coda del server: seguilo nella lista a destra
      </p>

      {#if previewOpen && previewData}
        <div class="mt-3 max-h-56 overflow-auto rounded border border-zinc-300 bg-white p-2 font-mono text-[11px] leading-relaxed text-zinc-700">
          <p class="text-zinc-500">// query</p>
          <pre>{JSON.stringify(previewData.query)}</pre>
          <p class="text-zinc-500">// form</p>
          <pre>{JSON.stringify(previewData.form)}</pre>
        </div>
      {/if}
      </div>

      <!-- BatchPanel resta MONTATO anche nei tab in cui non è visibile (class:hidden,
           non {#if}): cambiare tab non distrugge la coda in-session -->
      <div class={(panel !== "batch" && "hidden")} role="tabpanel" aria-label="Coda batch multi-composizione">
        <h2 class="mb-2 text-sm font-semibold text-zinc-700">Batch multi-composizione</h2>
        <BatchPanel {form} mock={mockMode} onPick={pickAndRefresh} onToast={flash} />
      </div>
    </section>

    <!-- ============================================ colonna 3D -->
    <section class="min-w-0" aria-label="Vista 3D">
      <SpecSketch {form} />
      <Molecule jobid={record?.outdir ? selectedId : null} />
      <p class="mt-1 text-xs text-zinc-500">
        {record ? `job ${selectedId}${record.outdir ? " · system/step1_pdbreader.pdb" : " · non ancora estratto"}` : "nessun job selezionato"}
      </p>
    </section>

    <!-- ============================================ colonna JOB -->
    <section class="min-w-0 rounded-lg border border-zinc-200 bg-white p-3" aria-label="Job">
      <h2 class="mb-2 flex items-center gap-2 text-sm font-semibold text-zinc-700">
        job <span class="text-xs font-normal text-zinc-500">({jobs.length} · aggiornamento 10s)</span>
      </h2>
      <p class="mb-2 text-[11px] leading-snug text-zinc-500">i build che hai inviato al server, con stato aggiornato in tempo reale</p>
      {#if jobs.length === 0}
        <p class="mb-3 text-xs text-zinc-500">archivio vuoto — invia il primo build dal form</p>
      {:else}
        <ul class="mb-3 max-h-64 space-y-1 overflow-auto">
          {#each jobs as j (j.jobid)}
            <li>
              <button
                class={selectedId === j.jobid
                  ? "w-full rounded border border-cyan-600 bg-cyan-50 px-2 py-1 text-left text-xs text-cyan-800"
                  : "w-full rounded border border-zinc-300 px-2 py-1 text-left text-xs text-zinc-700 hover:bg-zinc-100"}
                onclick={() => selectJob(j.jobid)}
                aria-pressed={selectedId === j.jobid}
              >
                <span class="font-mono">{j.jobid}</span>
                <span class={`rounded px-1 text-[10px] ${j.state === "done" ? "bg-green-100 text-green-800" : j.state === "error" ? "bg-red-100 text-red-800" : "bg-zinc-200 text-zinc-700"}`}>
                  {(j.state ?? "?")}
                </span>
                {#if j.label}
                  <span class="ml-1 text-zinc-600">{j.label}</span>
                {/if}
              </button>
            </li>
          {/each}
        </ul>
      {/if}

      {#if record && status}
        <div class="rounded border border-zinc-300 bg-white/50 p-2 text-xs text-zinc-700" aria-live="polite">
          <p class="mb-1 font-semibold text-zinc-700">dettaglio · {selectedId}</p>
          <dl class="mb-2 grid grid-cols-2 gap-x-3 gap-y-0.5">
            <dt class="text-zinc-500">stato</dt>
            <dd>{status.status ?? "?"}</dd>
            <dt class="text-zinc-500">coda</dt>
            <dd class="truncate" title={status.rank ?? ""}>{status.rank ?? "—"}</dd>
            <dt class="text-zinc-500">step</dt>
            <dd>{status.last_inp_step ?? "—"}</dd>
            <dt class="text-zinc-500">tar</dt>
            <dd>{status.has_tar ? "disponibile" : "no"}</dd>
          </dl>
          <div class="flex flex-wrap gap-2">
            <button
              class="rounded border border-zinc-600 px-2 py-1 hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:opacity-40"
              onclick={() => fetchJob(selectedId)}
              disabled={busy || status.status !== "done"}
              aria-disabled={busy || status.status !== "done"}
              title="Scarica l'archivio tar.gz del job finito ed estrailo in locale (results/… con manifest sha256)"
            >
              Scarica e estrai
            </button>
            <button
              class="rounded border border-zinc-600 px-2 py-1 hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
              onclick={() => cloneToForm(record)}
              title="Ricarica la composizione di questo job nei campi del form (l'API non ha undo: ogni modifica = nuovo job)"
            >
              Clona nel form
            </button>
          </div>
          {#if status.rank && /ahead|pending/i.test(status.rank)}
            <p class="mt-2 text-[11px] leading-snug text-amber-600">
              in coda sul server: la coda CHARMM-GUI oggi è sovraccarica, l'attesa può durare a lungo
            </p>
          {/if}
          {#if files.length}
            <details class="mt-2">
              <summary class="cursor-pointer text-zinc-600">file estratti ({files.length})</summary>
              <ul class="mt-1 max-h-40 space-y-0.5 overflow-auto font-mono text-[11px] text-zinc-600">
                {#each files as f (f.name)}
                  <li class="flex justify-between gap-2" style="padding-left: {0.75 * Math.max(0, f.depth - 1)}rem">
                    {#if f.name.endsWith('.pdb') || f.name.endsWith('.txt') || f.name.endsWith('.top')}
                      <a href={`/api/files/${selectedId}/${f.name}`} target="_blank" rel="noreferrer" class="truncate underline decoration-dotted hover:text-cyan-700">{f.name}</a>
                    {:else}
                      <span class="truncate">{f.name}</span>
                    {/if}
                    <span class="text-zinc-600">{(f.size / 1024).toFixed(1)}k</span>
                  </li>
                {/each}
              </ul>
            </details>
          {/if}
        </div>
      {:else if selectedId}
        <p class="text-xs text-zinc-500">in attesa dello stato del job {selectedId}…</p>
      {:else}
        <p class="text-xs text-zinc-500">seleziona un job per vederne live i dettagli</p>
      {/if}
    </section>
  </main>

  {#if toast}
    <div
      class={toast.kind === "err"
        ? "fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded px-4 py-2 text-sm shadow-lg bg-red-100 text-red-800 border border-red-300"
        : "fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded px-4 py-2 text-sm shadow-lg bg-emerald-100 text-emerald-800 border border-emerald-300"}
      role="status"
    >
      {toast.text}
    </div>
  {/if}

  <p class="mt-4 text-center text-xs text-zinc-600">
    cgui-studio (MIT) · sul server reale la coda può restare lunga: il mock chiude in ~8s
  </p>
</div>
