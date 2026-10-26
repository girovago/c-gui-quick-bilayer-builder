<script>
  // Batch multi-composizione: coda locale, esecuzione STRETTAMENTE sequenziale
  // (submit riga n → poll fino a done → fetch opzionale → riga n+1). ZERO job in
  // parallelo: il server CHARMM-GUI è sovraccarico, politeness.
  // Le impostazioni comuni (membrane_only, run_ffconverter, ioni, wdist) arrivano
  // dal wizard via prop `form` e vengono lette al momento del submit della riga
  // (se le cambi durante una run, si applicano alle righe successive).
  // La coda vive IN-SESSION: si perde chiudendo o ricaricando la pagina.
  import { api, formToPayload, lipidRe, parseLipidText } from "./api.js";

  let { form, mock = false, onPick = () => {}, onToast = () => {} } = $props();

  let rows = $state([]);
  let autoFetch = $state(true);
  let runState = $state("idle"); // idle | running | stopped
  let activeIdx = $state(-1);
  let note = $state("");
  let stopRequested = false;
  let runIdCounter = 0; // token del run: un nuovo Avvia invalida il vecchio loop
  let seq = 0;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const STATUS_LABEL = {
    queued: "in coda",
    submitting: "invio…",
    running: "in coda server",
    done: "finito",
    fetched: "estratto",
    error: "errore",
  };

  // Validazione riga batch: stessi LIPID=count del wizard MA con «;» come
  // separatore ammesso DENTRO la riga (es. "POPC=48;CHOL=16") — il wizard
  // accetta solo newline, le righe batch sono composizioni one-liner.
  function validSide(row, key) {
    const items = parseLipidText(row[key]).flatMap((l) => l.split(";").filter((s) => s.trim()));
    return items.length > 0 && items.every((l) => lipidRe.test(l));
  }
  function rowValid(row) {
    return validSide(row, "upper") && validSide(row, "lower") && Number(row.margin) > 0;
  }
  function labelOf(row, i) {
    return row.label.trim() || `batch-${i + 1}`;
  }

  function addRow(seed = {}) {
    rows.push({
      id: ++seq,
      upper: seed.upper ?? "POPC=64",
      lower: seed.lower ?? "POPC=64",
      margin: 20,
      label: "",
      status: "queued",
      jobid: null,
      err: "",
    });
  }

  // prima riga pre-tondita dal wizard: comodo punto di partenza
  addRow({ upper: form.upper, lower: form.lower });

  function removeRow(i) {
    rows.splice(i, 1);
    if (activeIdx > i) activeIdx -= 1;
  }

  let pendingRows = $derived(rows.filter((r) => r.status !== "done" && r.status !== "fetched"));
  let firstPending = $derived(rows.findIndex((r) => r.status !== "done" && r.status !== "fetched"));
  let canStart = $derived(
    runState !== "running" && pendingRows.length > 0 && pendingRows.every(rowValid)
  );

  function failSeq(i, e) {
    runState = "stopped";
    activeIdx = -1;
    note = `fermato per errore alla riga ${i + 1}: ${e?.message ?? e} — le righe completate restano in lista`;
    onToast("err", `Batch: riga ${i + 1} fallita — ${e?.message ?? e}`);
  }

  function stopAfter() {
    runState = "stopped";
    activeIdx = -1;
    note =
      "fermato su comando — le righe già inviate evolvono sul server (monitorale dalla lista job), le successive NON sono state inviate";
    onToast("ok", "Batch fermato");
  }

  async function runSequence() {
    runIdCounter++;
    const myRun = runIdCounter;
    stopRequested = false;
    runState = "running";
    note = "";
    activeIdx = -1;

    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      if (row.status === "done" || row.status === "fetched") continue; // salta le completate
      if (stopRequested || myRun !== runIdCounter) return stopAfter();
      const gone = () => myRun !== runIdCounter; // un nuovo Avvia ha sostituito questo loop

      activeIdx = i;
      row.status = "submitting";
      row.err = "";
      row.jobid = null;
      note = `riga ${i + 1} · ${labelOf(row, i)} — invio…`;

      let jobid;
      try {
        const payload = {
          ...formToPayload(form),
          label: labelOf(row, i),
          upper: row.upper,
          lower: row.lower,
          membtype: null, // il batch usa sempre le righe upper/lower esplicite
          margin: Number(row.margin),
        };
        const res = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
        if (gone()) return stopAfter();
        jobid = res.jobid;
        row.jobid = jobid;
        row.status = "running";
        onPick(jobid); // evidenzia il job nella lista e la aggiorna
        note = `riga ${i + 1} · ${labelOf(row, i)} — job ${jobid} in coda server`;
      } catch (e) {
        row.status = "error";
        row.err = e.message;
        return failSeq(i, e);
      }

      // ---- poll leggero: 1 chiamata GET per tick, blackout-tollerante
      let bad = 0;
      while (true) {
        if (stopRequested || gone()) return stopAfter();
        await sleep(mock ? 1500 : 2000);
        if (stopRequested || gone()) return stopAfter();
        if (document.hidden) continue; // tab in background: tick senza chiamata
        let data;
        try {
          data = await api(`/api/jobs/${jobid}`);
          bad = 0;
        } catch (e) {
          if (++bad > 10) {
            row.status = "error";
            row.err = `polling: ${e.message}`;
            return failSeq(i, e);
          }
          continue;
        }
        const st = data?.status?.status;
        if (st === "done") break;
        if (st && st !== "running" && st !== "pending" && st !== "queued") {
          row.status = "error";
          row.err = `il server segnala "${st}"`;
          return failSeq(i, new Error(row.err));
        }
      }
      row.status = "done";

      // ---- fetch opzionale (con retry: subito dopo done il 409 è possibile)
      if (autoFetch) {
        note = `riga ${i + 1} · ${labelOf(row, i)} — estrazione…`;
        for (let attempt = 0; attempt < 5; attempt++) {
          if (stopRequested || gone()) return stopAfter();
          try {
            await api(`/api/jobs/${jobid}/fetch`, {
              method: "POST",
              body: JSON.stringify({ wait: false }),
            });
            row.status = "fetched";
            break;
          } catch (e) {
            if (attempt === 4) {
              row.status = "error";
              row.err = `fetch: ${e.message}`;
              return failSeq(i, e);
            }
            await sleep(2000);
          }
        }
        if (gone()) return stopAfter();
      }
      note = `riga ${i + 1} · ${labelOf(row, i)} — ${STATUS_LABEL[row.status]}`;
    }

    activeIdx = -1;
    if (stopRequested) return stopAfter();
    runState = "idle";
    note = `batch completato: ${rows.length - pendingRows.length}/${rows.length} righe a posto`;
    onToast("ok", "Batch completato");
  }

  function start() {
    note = "";
    runSequence();
  }
  function stopRun() {
    stopRequested = true;
    note = "fermo chiesto: chiudo dopo l'operazione in corso…";
  }
</script>

<div class="flex min-h-0 flex-col" aria-label="Coda batch">
  <p class="mb-2 text-[11px] leading-snug text-zinc-500">
    le impostazioni comuni (membrane_only, run_ffconverter, ioni, wdist) sono quelle del wizard ·
    le righe girano UNA ALLA VOLTA · la coda locale vive finché non chiudi la pagina
  </p>

  {#if rows.length === 0}
    <p class="mb-3 text-xs text-zinc-500">nessuna riga in coda — aggiungine una con «+»</p>
  {:else}
    <ul class="mb-3 max-h-[28rem] space-y-2 overflow-auto pr-1">
      {#each rows as row, i (row.id)}
        <li
          class={`rounded border p-2 ${
            row.status === "error"
              ? "border-red-800 bg-red-950/30"
              : i === activeIdx
                ? "border-cyan-700 bg-cyan-950/30"
                : "border-zinc-700"
          }`}
        >
          <div class="mb-1.5 flex items-center gap-2">
            <span class="text-[11px] text-zinc-500">#{i + 1}</span>
            <input
              class="w-28 rounded border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 text-xs text-zinc-100 focus:border-cyan-500 focus:outline-none disabled:opacity-50"
              value={row.label}
              oninput={(e) => (row.label = e.target.value)}
              placeholder={`batch-${i + 1}`}
              aria-label={`etichetta riga ${i + 1}`}
              autocomplete="off"
              disabled={runState === "running"}
            />
            <span
              class={`ml-auto rounded px-1.5 py-0.5 text-[10px] ${
                row.status === "error"
                  ? "bg-red-900 text-red-300"
                  : row.status === "done" || row.status === "fetched"
                    ? "bg-green-900 text-green-300"
                    : row.status === "queued"
                      ? "bg-zinc-700 text-zinc-200"
                      : "bg-cyan-900 text-cyan-300"
              }`}
            >
              {STATUS_LABEL[row.status] ?? row.status}
            </span>
            <button
              type="button"
              class="rounded px-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:opacity-40"
              onclick={() => removeRow(i)}
              aria-label={`rimuovi riga ${i + 1}`}
              disabled={runState === "running"}
            >
              ×
            </button>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="mb-0.5 block text-[10px] text-zinc-500" for={`b-upper-${row.id}`}>upper</label>
              <textarea
                id={`b-upper-${row.id}`}
                rows="1"
                class="w-full rounded border bg-zinc-950 px-1.5 py-0.5 font-mono text-[11px] text-zinc-100 focus:outline-none disabled:opacity-50 {validSide(row, 'upper')
                  ? 'border-zinc-700'
                  : 'border-red-600'}"
                bind:value={row.upper}
                disabled={runState === "running"}
              ></textarea>
            </div>
            <div>
              <label class="mb-0.5 block text-[10px] text-zinc-500" for={`b-lower-${row.id}`}>lower</label>
              <textarea
                id={`b-lower-${row.id}`}
                rows="1"
                class="w-full rounded border bg-zinc-950 px-1.5 py-0.5 font-mono text-[11px] text-zinc-100 focus:outline-none disabled:opacity-50 {validSide(row, 'lower')
                  ? 'border-zinc-700'
                  : 'border-red-600'}"
                bind:value={row.lower}
                disabled={runState === "running"}
              ></textarea>
            </div>
          </div>

          <div class="mt-1 flex items-center gap-2">
            <label for={`b-margin-${row.id}`} class="text-[10px] text-zinc-500">margin</label>
            <input
              id={`b-margin-${row.id}`}
              type="number"
              step="0.1"
              min="0.5"
              class="w-20 rounded border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 text-xs text-zinc-100 disabled:opacity-50"
              bind:value={row.margin}
              disabled={runState === "running"}
            />
            {#if row.jobid}
              <button
                type="button"
                class="ml-auto font-mono text-[10px] text-zinc-400 underline decoration-dotted hover:text-cyan-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
                onclick={() => onPick(row.jobid)}
                title="apri il job nella lista a destra"
              >
                → job {row.jobid} nella lista
              </button>
            {/if}
          </div>

          {#if !rowValid(row)}
            <p class="mt-1 text-[10px] text-amber-400">
              riga non valida: serve LIPID=count (es. POPC=64) su upper e lower, margin &gt; 0 — più
              lipidi separati da «;»
            </p>
          {:else if row.err}
            <p class="mt-1 text-[10px] text-red-300">{row.err}</p>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}

  <div class="mb-3 flex items-center gap-2">
    <button
      type="button"
      class="rounded border border-zinc-600 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:opacity-40"
      onclick={() => addRow()}
      aria-label="aggiungi riga al batch"
      disabled={runState === "running"}
    >
      + aggiungi riga
    </button>
    <label class="flex items-center gap-1 text-xs text-zinc-300">
      <input type="checkbox" class="accent-cyan-500" bind:checked={autoFetch} />
      scarica ed estrai quando finito
    </label>
  </div>

  <div class="flex items-center gap-2">
    <button
      type="button"
      class="rounded bg-cyan-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-cyan-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-400"
      onclick={start}
      disabled={!canStart}
      aria-disabled={!canStart}
    >
      Avvia batch
    </button>
    {#if runState === "running"}
      <button
        type="button"
        class="rounded border border-red-700 px-3 py-1.5 text-sm text-red-200 hover:bg-red-950/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
        onclick={stopRun}
      >
        Ferma
      </button>
    {:else if runState === "stopped" && firstPending >= 0}
      <button
        type="button"
        class="rounded border border-cyan-700 px-3 py-1.5 text-sm text-cyan-200 hover:bg-cyan-950/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
        onclick={start}
      >
        Riprendi dalla riga {firstPending + 1} (salta le completate)
      </button>
    {/if}
  </div>

  <p
    class={`mt-2 min-h-[2.5rem] text-xs leading-snug ${
      runState === "stopped" ? "text-red-300" : "text-zinc-400"
    }`}
    aria-live="polite"
  >
    {note || "la coda è ferma — niente inviato finché non premi «Avvia batch»"}
  </p>
</div>
