<script>
  // Launcher di composizione lipidica: scegli, non scrivere.
  // I lipidi sono icone cliccabili con conteggio +/- .
  const LIPID_LIBRARY = [
    { id: "POPC", name: "POPC", color: "#3b82f6", icon: "🫧", atoms: 21 },
    { id: "CHOL", name: "CHOL", color: "#ef4444", icon: "💊", atoms: 27 },
    { id: "POPE", name: "POPE", color: "#10b981", icon: "🫧", atoms: 20 },
    { id: "DPPC", name: "DPPC", color: "#f59e0b", icon: "🫧", atoms: 21 },
    { id: "DSPC", name: "DSPC", color: "#8b5cf6", icon: "🫧", atoms: 21 },
    { id: "DOPS", name: "DOPS", color: "#ec4899", icon: "🫧", atoms: 22 },
    { id: "DOTAP", name: "DOTAP", color: "#06b6d4", icon: "🫧", atoms: 23 },
    { id: "SM", name: "SM", color: "#14b8a6", icon: "🫧", atoms: 28 },
  ];

  let lipids = $state(
    LIPID_LIBRARY.map((l) => ({ ...l, count: l.id === "POPC" ? 64 : 0 }))
  );

  let selectedLipid = $state("POPC"); // quale sto editando con +/- 
  let upperLeaf = $state(true); // true = foglio superiore, false = inferiore

  const { onCreate, onSubmit } = $props();

  // controlli fisici (scelti graficamente, mai a mano)
  let boxX = $state(12); // nm
  let boxY = $state(12); // nm
  let boxZ = $state(12); // nm
  let centerStructure = $state(true); // centratura nel box
  let layout = $state("planar"); // "planar" | "fluid"
  let solvent = $state("water"); // "water" | "none"
  let ions = $state("KCl"); // "KCl" | "NaCl" | "none"

  function lipidCount(id) {
    const l = lipids.find((x) => x.id === id);
    return l ? l.count : 0;
  }

  function increment(id) {
    const l = lipids.find((x) => x.id === id);
    if (l) l.count += 1;
  }

  function decrement(id) {
    const l = lipids.find((x) => x.id === id);
    if (l && l.count > 0) l.count -= 1;
  }

  function setCount(id, value) {
    const l = lipids.find((x) => x.id === id);
    if (l) l.count = Math.max(0, value);
  }

  function specString() {
    const upper = lipids
      .filter((l) => l.count > 0)
      .map((l) => `${l.id}=${l.count}`)
      .join(";");
    return upper || "POPC=64";
  }

  function totalLipids() {
    return lipids.reduce((s, l) => s + l.count, 0);
  }

  function totalAtoms() {
    return lipids.reduce((s, l) => s + l.count * l.atoms, 0);
  }

  function create() {
    const spec = specString();
    const params = { boxX, boxY, boxZ, centerStructure, layout, solvent, ions };
    if (onCreate) onCreate(spec, params);
  }

  function submit() {
    const spec = specString();
    if (onSubmit) onSubmit(spec);
  }
</script>

<div class="space-y-4">
  <!-- Grid lipidica -->
  <div class="rounded-xl border border-zinc-200 bg-white p-3">
    <h3 class="mb-3 text-xs font-semibold text-zinc-700 uppercase tracking-wider">
      Scegli i lipidi (clicca un'icona, poi + / −)
    </h3>
    <div class="grid grid-cols-4 gap-2">
      {#each lipids as l}
        <div
          role="button"
          tabindex="0"
          class="aspect-square rounded-lg border p-2 transition-all
            {l.id === selectedLipid
              ? 'border-cyan-500 bg-cyan-50 ring-1 ring-cyan-500'
              : 'border-zinc-200 bg-white hover:border-zinc-300 hover:bg-zinc-50'
            }
            {lipidCount(l.id) > 0 ? 'ring-1 ring-zinc-800' : ''}"
          onclick={() => (selectedLipid = l.id)}
          onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectedLipid = l.id; } }}
          title={l.name}
        >
          <div class="text-3xl mb-1">{l.icon}</div>
          <div class="text-xs font-medium text-zinc-800 leading-tight">
            {l.name}
          </div>
          <div
            class="text-[9px] font-mono text-zinc-500 bg-zinc-100 rounded px-1 py-0.5"
            title={`${l.atoms} atomi per lipide`}
          >
            {l.count}
          </div>
          <div class="flex justify-end gap-1 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              class="text-[10px] text-zinc-400 hover:text-zinc-800"
              onclick={(e) => { e.stopPropagation(); decrement(l.id); }}
              title="Rimuovi uno"
            >−</button>
            <button
              type="button"
              class="text-[10px] text-zinc-400 hover:text-zinc-800"
              onclick={(e) => { e.stopPropagation(); increment(l.id); }}
              title="Aggiungi uno"
            >+</button>
          </div>
        </div>
      {/each}
    </div>

    <div class="mt-3 flex items-center justify-between text-xs text-zinc-600">
      <span>Totale lipidi: <span class="font-medium text-zinc-800">{totalLipids()}</span></span>
      <span>Atomi stimati: <span class="font-medium text-zinc-800">{totalAtoms()}</span></span>
    </div>

    <div class="mt-2 flex gap-2">
      <button
        type="button"
        class="flex-1 rounded border border-zinc-300 bg-white px-3 py-1.5 text-xs text-zinc-700 hover:bg-zinc-50"
        onclick={() => {
          upperLeaf = true;
          selectedLipid = "POPC";
        }}
      >
        Foglio superiore
      </button>
      <button
        type="button"
        class="flex-1 rounded border border-zinc-300 bg-white px-3 py-1.5 text-xs text-zinc-700 hover:bg-zinc-50"
        onclick={() => {
          upperLeaf = false;
          selectedLipid = "POPC";
        }}
      >
        Foglio inferiore
      </button>
    </div>
  </div>

  <!-- Tenda: Box e posizionamento -->
  <div class="rounded-xl border border-zinc-200 bg-white p-3">
    <div class="flex items-center justify-between mb-2">
      <h3 class="text-xs font-semibold text-zinc-700 uppercase tracking-wider">
        Box e posizionamento
      </h3>
      <button
        type="button"
        class="text-[10px] text-zinc-500 hover:text-zinc-800"
        title="Espandi i controlli della scatola di simulazione"
      >
        ⌄
      </button>
    </div>
    <div class="grid grid-cols-3 gap-2 text-xs">
      <div>
        <label for="box-x" class="block text-zinc-600 mb-0.5">Box X (nm)</label>
        <input
          id="box-x"
          type="number"
          step="0.5"
          min="2"
          class="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-zinc-800 focus:border-cyan-500 focus:outline-none"
          bind:value={boxX}
        />
      </div>
      <div>
        <label for="box-y" class="block text-zinc-600 mb-0.5">Box Y (nm)</label>
        <input
          id="box-y"
          type="number"
          step="0.5"
          min="2"
          class="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-zinc-800 focus:border-cyan-500 focus:outline-none"
          bind:value={boxY}
        />
      </div>
      <div>
        <label for="box-z" class="block text-zinc-600 mb-0.5">Box Z (nm)</label>
        <input
          id="box-z"
          type="number"
          step="0.5"
          min="2"
          class="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-zinc-800 focus:border-cyan-500 focus:outline-none"
          bind:value={boxZ}
        />
      </div>
    </div>
    <div class="mt-2 flex items-center gap-2">
      <label class="flex items-center gap-1 text-xs text-zinc-700 cursor-pointer" title="Centra la struttura nel box (l'equivalente di editconf -c; applicato quando il backend lo supporta)">
        <input type="checkbox" class="accent-cyan-500" bind:checked={centerStructure} />
        Centra la struttura nel box
      </label>
    </div>
  </div>

  <!-- Tenda: Disposizione -->
  <div class="rounded-xl border border-zinc-200 bg-white p-3 mt-2">
    <h3 class="mb-2 text-xs font-semibold text-zinc-700 uppercase tracking-wider">
      Disposizione dei lipidi
    </h3>
    <div class="grid grid-cols-2 gap-2">
      <button
        type="button"
        class={`flex flex-col items-center rounded border px-3 py-2 text-xs ${
          layout === "planar"
            ? "border-cyan-500 bg-cyan-50 text-zinc-800 ring-1 ring-cyan-500"
            : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
        }`}
        onclick={() => (layout = "planar")}
        title="Griglia regolare, spessore costante"
        aria-pressed={layout === "planar"}
      >
        <div class="text-xl mb-1">📐</div>
        <div class="font-medium">Superficie piana</div>
        <div class="text-[9px] text-zinc-500">Griglia regolare, spessore costante</div>
      </button>
      <button
        type="button"
        class={`flex flex-col items-center rounded border px-3 py-2 text-xs ${
          layout === "fluid"
            ? "border-cyan-500 bg-cyan-50 text-zinc-800 ring-1 ring-cyan-500"
            : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
        }`}
        onclick={() => (layout = "fluid")}
        title="Distribuzione casuale realistica"
        aria-pressed={layout === "fluid"}
      >
        <div class="text-xl mb-1">🌊</div>
        <div class="font-medium">Mosaico fluido</div>
        <div class="text-[9px] text-zinc-500">Distribuzione casuale realistica</div>
      </button>
    </div>
  </div>

  <!-- Tenda: Solvente e ioni -->
  <div class="rounded-xl border border-zinc-200 bg-white p-3 mt-2">
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-zinc-700 mb-1">Solvente</label>
        <div class="flex items-center gap-2">
          <input type="radio" name="launcher-solvent" value="water" bind:group={solvent} class="accent-cyan-500" />
          <span class="text-xs text-zinc-800">Acqua TIP3P</span>
        </div>
        <div class="flex items-center gap-2 mt-1">
          <input type="radio" name="launcher-solvent" value="none" bind:group={solvent} class="accent-zinc-400" />
          <span class="text-xs text-zinc-600">Niente (solo lipidi)</span>
        </div>
      </div>
      <div>
        <label class="block text-xs text-zinc-700 mb-1">Ioni</label>
        <div class="flex items-center gap-2">
          <input type="radio" name="launcher-ions" value="KCl" bind:group={ions} class="accent-cyan-500" />
          <span class="text-xs text-zinc-800">KCl 0.15 M</span>
        </div>
        <div class="flex items-center gap-2 mt-1">
          <input type="radio" name="launcher-ions" value="NaCl" bind:group={ions} class="accent-zinc-400" />
          <span class="text-xs text-zinc-600">NaCl 0.15 M</span>
        </div>
        <div class="flex items-center gap-2 mt-1">
          <input type="radio" name="launcher-ions" value="none" bind:group={ions} class="accent-zinc-400" />
          <span class="text-xs text-zinc-600">Nessuno</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Azione -->
  <div class="mt-4">
    <div class="flex flex-wrap gap-2">
      <button
        type="button"
        class="flex-1 rounded bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-600"
        onclick={create}
        title="Genera la struttura bilayer con questi lipidi, box, solvente e ioni scelti"
      >
        ▶ Crea struttura
      </button>
      <button
        type="button"
        class="flex-1 rounded bg-cyan-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-cyan-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-600"
        onclick={submit}
        title="Invia la composizione al server CHARMM-GUI — nella coda reale l'attesa può essere molto lunga"
      >
        Invia a CHARMM-GUI
      </button>
    </div>
    <p class="mt-2 text-[10px] text-zinc-500">
      Nessuna attesa in coda: la struttura viene costruita in locale con GROMACS
    </p>
  </div>

  <!-- Spec nascosta per il backend (verrà collegata) -->
  <span class="hidden">{specString()}</span>
</div>
