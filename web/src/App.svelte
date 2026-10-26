<script>
  // App a 3 colonne: form wizard | vista 3D | lista job + dettaglio.
  // Su schermo stretto le colonne si impilano. Polling: lista 10s, dettaglio 2s.
  import { onMount } from "svelte";
  import Molecule from "./Molecule.svelte";
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
  const ION_NOTE = "il server usa questi minuscoli, la doc ufficiale dice maiuscoli — bug noto (SOURCES.md §2)";

  // ------------------------------------------------------------------ jobs state
  let jobs = $state([]);
  let selectedId = $state(null);
  let detail = $state(null);
  let files = $state([]);
  let statusMsg = $state("");
  let toast = $state(null);
  let busy = $state(false);
  let previewOpen = $state(false);
  let previewData = $state(null);
  let mockMode = $state(false);

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
      if (detail.record.outdir) {
        try {
          files = (await api(`/api/files/${selectedId}`)).files ?? [];
        } catch {
          files = [];
        }
      } else {
        files = [];
      }
    } catch {
      /* job non interrogabile ora (404/token): tenant lo stato precedente, il polling riprova */
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
  }

  onMount(() => {
    refreshJobs();
    api("/api/ping").then((p) => (mockMode = Boolean(p.mock))).catch(() => {});
  });

  // polling: lista ogni 10s, dettaglio del job selezionato ogni 2s
  $effect(() => {
    refreshDetail();
    const t1 = setInterval(refreshDetail, 2000);
    const t2 = setInterval(refreshJobs, 10000);
    return () => {
      clearInterval(t1);
      clearInterval(t2);
    };
  });
</script>

<div class="mx-auto max-w-[1600px] px-4 py-4 lg:px-6">
  <header class="mb-4 flex items-baseline justify-between gap-4">
    <h1 class="text-lg font-semibold tracking-tight text-zinc-100">
      cgui-studio <span class="text-zinc-500">· Quick Bilayer wizard</span>
    </h1>
    <span class="rounded-full border border-cyan-800/60 px-2 py-0.5 text-xs text-cyan-300" title="CGUI_MOCK=1: nessuna chiamata reale a charmm-gui.org">
      {mockMode ? "MOCK" : "API reale"}
    </span>
  </header>

  <main class="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-[340px_minmax(0,1fr)_minmax(320px,1fr)]">
    <!-- ============================================ colonna FORM -->
    <section class="min-w-0 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3" aria-label="Costruzione">
      <h2 class="mb-2 text-sm font-semibold text-zinc-300">1 · Composizione</h2>
      <label class="mb-1 block text-xs text-zinc-400" for="f-label">etichetta</label>
      <input id="f-label" class="mb-2 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100 focus:border-cyan-500 focus:outline-none" bind:value={form.label} placeholder="es. POPC64" />

      <fieldset class="mb-2">
        <legend class="mb-1 text-xs text-zinc-500">modalità</legend>
        <div class="flex items-center gap-3 text-xs text-zinc-300">
          <label><input type="radio" name="mode" class="accent-cyan-500" checked={form.membtype === ""} onchange={() => (form.membtype = "")} /> upper/lower</label>
          <label><input type="radio" name="mode" class="accent-cyan-500" checked={form.membtype !== ""} onchange={() => (form.membtype = "PMm")} /> membtype</label>
        </div>
      </fieldset>

      {#if form.membtype === ""}
        <label class="mb-1 block text-xs text-zinc-400" for="f-upper">foglia upper (righe LIPID=count)</label>
        <textarea id="f-upper" rows="2" class="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-100 focus:border-cyan-500 focus:outline-none" bind:value={form.upper}></textarea>
        <label class="mb-1 block text-xs text-zinc-400" for="f-lower">foglia lower</label>
        <textarea id="f-lower" rows="2" class="mb-2 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-100 focus:border-cyan-500 focus:outline-none" bind:value={form.lower}></textarea>
        {#if !upperOk}
          <p class="mb-2 text-xs text-amber-400">righe non valide: servono LIPID=count (es. POPC=64)</p>
        {/if}
      {:else}
        <label class="mb-1 block text-xs text-zinc-400" for="f-membtype">biomembrana predefinita</label>
        <select id="f-membtype" class="mb-2 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100" bind:value={form.membtype}>
          {#each MEMB_PRESETS as m (m)}
            <option value={m}>{m}</option>
          {/each}
        </select>
      {/if}

      <h2 class="mb-2 mt-3 text-sm font-semibold text-zinc-300">2 · parametri</h2>
      <div class="grid grid-cols-3 gap-2">
        <label for="f-margin"><span class="mb-1 block text-xs text-zinc-400">margin</span>
          <input id="f-margin" type="number" step="0.1" min="0.5" class="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100" bind:value={form.margin} />
        </label>
        <label for="f-wdist"><span class="mb-1 block text-xs text-zinc-400">wdist</span>
          <input id="f-wdist" type="number" step="0.1" class="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100" bind:value={form.wdist} />
        </label>
        <label for="f-ionconc"><span class="mb-1 block text-xs text-zinc-400">ion_conc</span>
          <input id="f-ionconc" type="number" step="0.01" min="0" class="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100" bind:value={form.ion_conc} />
        </label>
      </div>
      <label for="f-iontype">
        <span class="mb-1 mt-2 block text-xs text-zinc-400">ion_type</span>
        <select id="f-iontype" class="w-40 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100" bind:value={form.ion_type}>
          <option>NaCl</option>
          <option>KCl</option>
        </select>
      </label>
      <p class="mt-1 flex items-start gap-1 text-[11px] leading-snug text-amber-400/90" role="note">
        <span aria-hidden="true">⚠</span>{ION_NOTE}
      </p>

      <div class="mt-3 flex flex-col gap-1 text-xs text-zinc-300">
        <label><input type="checkbox" class="accent-cyan-500" bind:checked={form.membrane_only} /> membrane_only</label>
        <label><input type="checkbox" class="accent-cyan-500" bind:checked={form.run_ffconverter} /> run_ffconverter (necessario per gli input GROMACS)</label>
      </div>

      <div class="mt-4 flex items-center gap-2">
        <button
          class="rounded bg-cyan-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-cyan-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-400"
          onclick={submitJob}
          disabled={!valid || busy}
          aria-disabled={!valid || busy}
        >
          Invia
        </button>
        <button
          class="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:opacity-50"
          onclick={showPreview}
          aria-expanded={previewOpen}
        >
          Payload
        </button>
        {#if statusMsg}
          <span class="truncate text-xs text-emerald-400">{statusMsg}</span>
        {/if}
      </div>

      {#if previewOpen && previewData}
        <div class="mt-3 max-h-56 overflow-auto rounded border border-zinc-700 bg-black/60 p-2 font-mono text-[11px] leading-relaxed text-zinc-300">
          <p class="text-zinc-500">// query</p>
          <pre>{JSON.stringify(previewData.query)}</pre>
          <p class="text-zinc-500">// form</p>
          <pre>{JSON.stringify(previewData.form)}</pre>
        </div>
      {/if}
    </section>

    <!-- ============================================ colonna 3D -->
    <section class="min-w-0" aria-label="Vista 3D">
      <Molecule jobid={record?.outdir ? selectedId : null} />
      <p class="mt-1 text-xs text-zinc-500">
        {record ? `job ${selectedId}${record.outdir ? " · system/step1_pdbreader.pdb" : " · non ancora estratto"}` : "nessun job selezionato"}
      </p>
    </section>

    <!-- ============================================ colonna JOB -->
    <section class="min-w-0 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3" aria-label="Job">
      <h2 class="mb-2 flex items-center gap-2 text-sm font-semibold text-zinc-300">
        job <span class="text-xs font-normal text-zinc-500">({jobs.length} · aggiornamento 10s)</span>
      </h2>
      {#if jobs.length === 0}
        <p class="mb-3 text-xs text-zinc-500">archivio vuoto — invia il primo build dal form</p>
      {:else}
        <ul class="mb-3 max-h-64 space-y-1 overflow-auto">
          {#each jobs as j (j.jobid)}
            <li>
              <button
                class={selectedId === j.jobid
                  ? "w-full rounded border border-cyan-600 bg-cyan-950/40 px-2 py-1 text-left text-xs text-cyan-100"
                  : "w-full rounded border border-zinc-700 px-2 py-1 text-left text-xs text-zinc-300 hover:bg-zinc-800"}
                onclick={() => selectJob(j.jobid)}
                aria-pressed={selectedId === j.jobid}
              >
                <span class="font-mono">{j.jobid}</span>
                <span class={`rounded px-1 text-[10px] ${j.state === "done" ? "bg-green-900 text-green-300" : j.state === "error" ? "bg-red-900 text-red-300" : "bg-zinc-700 text-zinc-200"}`}>
                  {(j.state ?? "?")}
                </span>
                {#if j.label}
                  <span class="ml-1 text-zinc-400">{j.label}</span>
                {/if}
              </button>
            </li>
          {/each}
        </ul>
      {/if}

      {#if record && status}
        <div class="rounded border border-zinc-700 bg-black/40 p-2 text-xs text-zinc-300" aria-live="polite">
          <p class="mb-1 font-semibold text-zinc-200">dettaglio · {selectedId}</p>
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
              class="rounded border border-zinc-600 px-2 py-1 hover:bg-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:opacity-40"
              onclick={() => fetchJob(selectedId)}
              disabled={busy || status.status !== "done"}
              aria-disabled={busy || status.status !== "done"}
            >
              Scarica e estrai
            </button>
            <button
              class="rounded border border-zinc-600 px-2 py-1 hover:bg-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
              onclick={() => cloneToForm(record)}
            >
              Clona nel form
            </button>
          </div>
          {#if files.length}
            <details class="mt-2">
              <summary class="cursor-pointer text-zinc-400">file estratti ({files.length})</summary>
              <ul class="mt-1 max-h-40 space-y-0.5 overflow-auto font-mono text-[11px] text-zinc-400">
                {#each files as f (f.name)}
                  <li class="flex justify-between gap-2" style="padding-left: {0.75 * Math.max(0, f.depth - 1)}rem">
                    {#if f.name.endsWith('.pdb') || f.name.endsWith('.txt') || f.name.endsWith('.top')}
                      <a href={`/api/files/${selectedId}/${f.name}`} target="_blank" rel="noreferrer" class="truncate underline decoration-dotted hover:text-cyan-300">{f.name}</a>
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
        ? "fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded px-4 py-2 text-sm shadow-lg bg-red-950 text-red-100"
        : "fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded px-4 py-2 text-sm shadow-lg bg-emerald-950 text-emerald-100"}
      role="status"
    >
      {toast.text}
    </div>
  {/if}

  <p class="mt-4 text-center text-xs text-zinc-600">
    cgui-studio (MIT) · sul server reale la coda può restare lunga: il mock chiude in ~8s
  </p>
</div>
