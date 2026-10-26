/** Wrapper del fetch: errori HTTP → Error con .status e message leggibile. */
export async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!resp.ok) {
    let detail = await resp.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* body testuale: lo lasciamo com'è */
    }
    const err = new Error(detail || `HTTP ${resp.status}`);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

/** Parsing "LIPID=count" (una riga per lipid), validazione live del form. */
export const lipidRe = /^\s*[A-Za-z0-9_]+=\s*\d+\s*$/;

export function parseLipidText(text) {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
}

/** Il campo composto è valido? (upper+lower insieme, oppure membtype, non entrambi) */
export function isFormValid(form) {
  const upperLines = parseLipidText(form.upper).every((l) => lipidRe.test(l));
  const lowerLines = parseLipidText(form.lower).every((l) => lipidRe.test(l));
  const hasUpper = parseLipidText(form.upper).length > 0 && parseLipidText(form.lower).length > 0;
  if (form.membtype) return !hasUpper;
  return hasUpper && upperLines && lowerLines && form.margin > 0;
}

/** Body JSON per POST /api/jobs (i testi vuoti diventano null). */
export function formToPayload(form) {
  return {
    label: form.label || null,
    upper: form.upper || null,
    lower: form.lower || null,
    membtype: form.membtype || null,
    margin: Number(form.margin),
    wdist: form.wdist === "" || form.wdist === null ? null : Number(form.wdist),
    ion_conc: Number(form.ion_conc),
    ion_type: form.ion_type,
    membrane_only: Boolean(form.membrane_only),
    run_ffconverter: Boolean(form.run_ffconverter),
    clone_job: form.clone_job || null,
  };
}
