<script>
  // Schema della spec: striscia SVG puro (zero dipendenze) con le due foglie
  // stilizzate. ONESTO: è uno schema dalla spec, non la struttura — la nota
  // e il badge lo dicono. Live dal form (runes): cambia anche senza submit.
  // Click sulla striscia → toggle delle label (per schermi stretti).
  let { form } = $props();

  let showLabels = $state(true);

  const PALETTE = [
    "#22d3ee", // cyan
    "#a78bfa", // violet
    "#34d399", // emerald
    "#fbbf24", // amber
    "#f87171", // red
    "#f472b6", // pink
    "#60a5fa", // blue
    "#facc15", // yellow
  ];

  function hashColor(name) {
    // stabile per nome: stesso lipide → stesso colore in entrambe le foglie
    let h = 5381;
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  function lipidsOf(text) {
    // righe multiple e item separati da ";" (`POPC=48;CHOL=16`)
    const out = [];
    for (const item of String(text ?? "").split(/\n|;/)) {
      const m = /^\s*([A-Za-z0-9_]+)\s*=\s*(\d+)\s*$/.exec(item);
      if (m) out.push({ name: m[1], count: Number(m[2]) });
    }
    return out;
  }

  let upper = $derived(lipidsOf(form?.upper));
  let lower = $derived(lipidsOf(form?.lower));
  let membtype = $derived(form?.membtype ?? "");
  let presetMode = $derived(Boolean(membtype));

  function labelText(list) {
    if (!list.length) return "vuota";
    return list.map((l) => `${l.name} ${l.count}`).join(" + ");
  }

  const W = 640;
  const H = 120;
  const MARGIN = 16;
  const INNER = W - 2 * MARGIN;

  // testine per foglia: proporzioni reali, ma se il totale è enorme
  // si dispone proporzionalmente senza fondere l'SVG
  function layout(list) {
    const total = list.reduce((s, l) => s + l.count, 0);
    if (!total) return [];
    const drawTotal = Math.min(total, 84); // cap di leggibilità (agenti real: label vera)
    let x = MARGIN;
    return list.map((l) => {
      const band = INNER * (l.count / total);
      const n = Math.max(1, Math.round((l.count / total) * drawTotal));
      const res = { ...l, color: hashColor(l.name), bandStart: x, bandWidth: band, heads: n };
      x += band;
      return res;
    });
  }

  let upperLayout = $derived(presetMode ? [] : layout(upper));
  let lowerLayout = $derived(presetMode ? [] : layout(lower));
  let empty = $derived(!presetMode && !upper.length && !lower.length);

  // percorso delle code a zig-zag (2 segmenti per coda, 2 code per lipide)
  function tails(x, headY, tipY) {
    const dir = tipY > headY ? 1 : -1;
    const mid = (headY + tipY) / 2;
    return (
      `M ${x - 2} ${headY + 3 * dir} L ${x - 4.5} ${mid} L ${x - 1} ${tipY}` +
      ` M ${x + 2} ${headY + 3 * dir} L ${x + 4.5} ${mid} L ${x + 1} ${tipY}`
    );
  }
</script>

<div class="mb-2 rounded-lg border border-zinc-800 bg-zinc-900/60 p-2" aria-label="Schema della spec">
  <div class="mb-1 flex items-baseline justify-between gap-2 px-1">
    <span
      class="rounded border border-violet-500/50 bg-violet-950/60 px-2 py-0.5 text-[11px] font-semibold text-violet-300"
    >
      SCHEMA DELLA SPEC — provvisorio
    </span>
    <button
      type="button"
      class="text-[11px] text-zinc-500 hover:text-zinc-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
      onclick={() => (showLabels = !showLabels)}
      aria-pressed={showLabels}
    >
      {showLabels ? "nascondi label" : "mostra label"}
    </button>
  </div>

  {#if empty}
    <p class="px-1 pb-1 text-[11px] text-zinc-500">
      scrivi la composizione (es. <code class="text-zinc-400">POPC=64</code>) o scegli un membtype
      per vedere lo schema
    </p>
  {:else}
    <button
      type="button"
      class="block w-full cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400"
      onclick={() => (showLabels = !showLabels)}
      aria-pressed={showLabels}
      aria-label="Schema bilayer: clic per mostrare/nascondere le label della composizione"
    >
      <svg
        viewBox="0 0 {W} {H}"
        class="block h-[120px] w-full"
        role="img"
        aria-hidden={true}
      >
        <!-- linea centrale della membrana -->
        <line x1={MARGIN} y1={H / 2} x2={W - MARGIN} y2={H / 2} stroke="#3f3f46" stroke-dasharray="4 6" stroke-width="1" />

        {#if presetMode}
          <text x={W / 2} y={H / 2 - 10} text-anchor="middle" class="fill-zinc-300" font-size="15" font-weight="600">
            preset server `{membtype}`
          </text>
          <text x={W / 2} y={H / 2 + 12} text-anchor="middle" class="fill-zinc-500" font-size="11">
            composizione decisa dal preset: niente molecole disegnate
          </text>
        {:else}
          <!-- foglia upper: teste in alto, code verso il centro -->
          {#each upperLayout as type (type.name + type.bandStart)}
            {#each Array(type.heads) as _, hi (hi)}
              {@const x = type.bandStart + (type.bandWidth * (hi + 0.5)) / type.heads}
              <path d={tails(x, 18, 52)} stroke={type.color} stroke-width="1" fill="none" opacity="0.75" />
              <circle cx={x} cy={18} r={3.2} fill={type.color} />
            {/each}
          {/each}
          <!-- foglia lower: teste in basso, code verso il centro -->
          {#each lowerLayout as type (type.name + type.bandStart)}
            {#each Array(type.heads) as _, hi (hi)}
              {@const x = type.bandStart + (type.bandWidth * (hi + 0.5)) / type.heads}
              <path d={tails(x, 102, 68)} stroke={type.color} stroke-width="1" fill="none" opacity="0.75" />
              <circle cx={x} cy={102} r={3.2} fill={type.color} />
            {/each}
          {/each}
        {/if}
      </svg>
    </button>
  {/if}

  {#if showLabels && !empty}
    <div class="mt-1 px-1 text-[11px] leading-snug text-zinc-400">
      {#if presetMode}
        <span class="text-violet-300">{membtype}</span> — la composizione reale la conosce solo il
        server
      {:else}
        <span class="text-zinc-500">upper:</span>
        {upper.map((l) => `${l.name} ${l.count}`).join(" + ") || "—"}
        <span class="text-zinc-600"> · </span>
        <span class="text-zinc-500">lower:</span>
        {lower.map((l) => `${l.name} ${l.count}`).join(" + ") || "—"}
      {/if}
      <span class="text-zinc-600"> · la struttura reale appare quando il job è finito e estratto</span>
    </div>
  {/if}
</div>
