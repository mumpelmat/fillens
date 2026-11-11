"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Check, LoaderCircle, PencilLine, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { loadHistories } from "@/lib/history-loader";
import { api, dashboardLevelTone, number, unit, rateFactor, rateUnit, write,
  type Container, type ContainerEvent, type Measurement } from "@/lib/api";

export type HistoryData = { items: Measurement[]; total_count: number; sampled: boolean };
type HistoryState = HistoryData & { events: ContainerEvent[]; loading: boolean; error: string };
const emptyHistory: HistoryState = {
  items: [], total_count: 0, sampled: false, events: [], loading: true, error: "",
};

export function useDetailHistory(id: string | undefined, period: string, revision: number) {
  const key = `${id}:${period}:${revision}`;
  const [state, setState] = useState<{ key: string; data: HistoryState }>();
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    const options = { signal: controller.signal };
    Promise.all([
      api<HistoryData>(`/containers/${id}/measurements?period=${period}`, options),
      api<ContainerEvent[]>(`/containers/${id}/events`, options),
    ]).then(([data, events]) => {
      if (!controller.signal.aborted) setState({ key, data: { ...data, events, loading: false, error: "" } });
    }).catch((error: Error) => {
      if (!controller.signal.aborted) setState({ key, data: { ...emptyHistory, loading: false, error: error.message } });
    });
    return () => controller.abort();
  }, [id, period, revision, key]);
  return state?.key === key ? state.data : emptyHistory;
}

export function useOverviewHistories(containers: Container[], period: string, revision: number, enabled: boolean) {
  const ids = JSON.stringify(enabled ? containers.map((c) => c.id) : []);
  const key = `${ids}:${period}:${revision}`;
  const [state, setState] = useState<{ key: string; data: Record<string, HistoryState> }>();
  useEffect(() => {
    const queue: string[] = JSON.parse(ids);
    const controller = new AbortController();
    void loadHistories(queue,
      (id) => api<HistoryData>(`/containers/${id}/measurements?period=${period}&limit=200`, { signal: controller.signal }),
      (id, response) => {
        const result: HistoryState = "data" in response
          ? { ...response.data, events: [], loading: false, error: "" }
          : { ...emptyHistory, loading: false, error: response.error };
        setState((previous) => ({
          key, data: { ...(previous?.key === key ? previous.data : {}), [id]: result },
        }));
      }, controller.signal);
    return () => controller.abort();
  }, [ids, period, revision, key]);
  const data = state?.key === key ? state.data : {};
  return (id: string): HistoryState => data[id] || emptyHistory;
}

export function measurementView(container: Container) {
  const measured = container.current_measurement;
  const analysis = container.current_image?.analysis;
  const estimate = container.current_image && !container.current_image.has_measurement &&
    analysis?.raw_fill_level != null && ["pending", "skipped"].includes(analysis.status)
    ? analysis.raw_fill_level : null;
  const estimated = estimate != null;
  const level = estimated ? estimate : measured?.effective_fill_level;
  const absolute = estimated ? container.capacity * estimate / 100 : container.absolute_level;
  const tone = dashboardLevelTone(container.display_mode, level);
  const severity = tone === "red" ? (container.display_mode === "consumption" ? "Sehr niedrig" : "Sehr hoch")
    : tone === "orange" ? (container.display_mode === "consumption" ? "Niedrig" : "Hoch") : "";
  const label = tone === "unknown" ? "Noch keine Messung" : [estimated ? "Geschätzt" : measured?.analysis_method.startsWith("manual") ? "Nutzereingabe" : "", severity].filter(Boolean).join(" · ") || "Aktuell";
  return { measured, estimate, estimated, level, absolute, tone, label };
}

export function MeasurementKpis({ container }: { container: Container }) {
  const view = measurementView(container);
  const factor = rateFactor(container.rate_time_unit);
  const suffix = rateUnit(container.rate_time_unit);
  return <div className="kpi-grid" data-level-tone={view.tone}>
    <div className={`kpi kpi-primary level-tone-${view.tone} ${view.estimated ? "is-estimated" : ""}`}>
      <div className="kpi-label">Füllstand</div>
      <div className="kpi-value">{number(view.level)}<span>%</span></div><small>{view.label}</small>
    </div>
    <div className={`kpi inventory-kpi level-tone-${view.tone} ${view.estimated ? "is-estimated" : ""}`}>
      <div className="kpi-label">Inhalt</div>
      <div className="kpi-value">{number(view.absolute)}<span>/ {number(container.capacity)} {unit(container.capacity_unit)}</span></div>
    </div>
    <div className="kpi">
      <div className="kpi-label">{container.display_mode === "consumption" ? "Verbrauch" : "Füllrate"}</div>
      {container.rate_status !== "ready" ? <p className="rate-pending"><LoaderCircle size={16} className="spin" /> Wird neu berechnet …</p> : <>
        <div className="kpi-value">{number(container.operating_rate_absolute == null ? null : container.operating_rate_absolute * factor, 3)}<span>{unit(container.capacity_unit)}/{suffix}</span></div>
        <div className="rate-secondary">{number(container.operating_rate_percent == null ? null : container.operating_rate_percent * factor, 3)} <abbr title="Prozentpunkte der Gesamtkapazität">pp</abbr>/{suffix}</div>
      </>}
    </div>
  </div>;
}

export function ContainerTabs({ containers, selected, onSelect, siteName, includeAll = true, panelId = "dashboard-content", idPrefix = "tab" }: {
  containers: Container[]; selected: string; onSelect: (id: string) => void; siteName?: (id: string) => string; includeAll?: boolean; panelId?: string; idPrefix?: string;
}) {
  const tabs = [...(includeAll ? [{ id: "all", name: "Alle" }] : []), ...containers.map((c) => ({ id: c.id,
    name: `${c.name}${siteName && containers.some((other) => other.id !== c.id && other.name === c.name) ? ` · ${siteName(c.site_id)}` : ""}${c.active ? "" : " · Inaktiv"}`,
  }))];
  return <div className="container-tabs" role="tablist" aria-label="Behälter" onKeyDown={(event) => {
    const current = tabs.findIndex((tab) => tab.id === selected);
    const next = event.key === "ArrowRight" ? (current + 1) % tabs.length
      : event.key === "ArrowLeft" ? (current - 1 + tabs.length) % tabs.length
      : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    onSelect(tabs[next].id);
    const button = event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=tab]")[next];
    button.focus(); button.scrollIntoView({ block: "nearest", inline: "nearest" });
  }}>
    {tabs.map((tab) => <button key={tab.id} id={`${idPrefix}-${tab.id}`} type="button" role="tab" aria-selected={selected === tab.id}
      aria-controls={panelId} tabIndex={selected === tab.id ? 0 : -1} onClick={() => onSelect(tab.id)}>{tab.name}</button>)}
  </div>;
}

export function ContainerSettings({ name, onAction }: { name: string; onAction: (action: "container" | "camera" | "rules") => void }) {
  return <details className="container-settings">
    <summary aria-label={`Einstellungen für ${name}`}><Settings2 size={15} /> Einstellungen</summary>
    <div className="settings-options">
      {([['container', 'Behälter bearbeiten'], ['camera', 'Kamera konfigurieren'], ['rules', 'Warnregeln']] as const).map(([action, label]) =>
        <button key={action} type="button" onClick={(event) => {
          event.currentTarget.closest("details")?.removeAttribute("open"); onAction(action);
        }}>{label}</button>)}
    </div>
  </details>;
}

/** Einzige Korrekturstelle im Dashboard: Messwert des aktuellen Bildes setzen und zugleich lernen. */
export function LevelCorrection({ container, admin, onSaved }: {
  container: Container; admin: boolean; onSaved: () => void;
}) {
  const image = container.current_image;
  const view = measurementView(container);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  if (!image || !(admin || container.camera.learning_enabled)) return null;
  const inputId = `level-correction-${container.id}`;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!image) return;
    setBusy(true); setError("");
    try {
      const result = await api<{ learned: boolean }>(`/containers/${container.id}/level-correction`,
        write({ image_id: image.id, fill_level: Number(value) }));
      setSaved(result.learned ? "Gespeichert und zum Lernen übernommen." : "Messwert gespeichert.");
      setOpen(false);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return <div className="level-correction">
    {!open ? <div className="level-correction-row">
      <span>{view.estimated ? "Automatisch geschätzt" : view.measured?.analysis_method.startsWith("manual") ? "Bestätigter Füllstand" : "Erkannter Füllstand"}: <strong>{number(view.level)} %</strong></span>
      {saved && <span className="level-correction-saved" role="status"><Check size={13} /> {saved}</span>}
      <Button type="button" size="sm" variant="outline" onClick={() => {
        setValue(view.level == null ? "" : String(Math.round(view.level * 10) / 10)); setError(""); setSaved(""); setOpen(true);
      }}><PencilLine size={14} /> Füllstand korrigieren</Button>
    </div> : <form className="level-correction-form" onSubmit={submit}>
      <label htmlFor={inputId}>Tatsächlicher Füllstand auf diesem Bild</label>
      <div className="level-correction-inputs">
        <input id={inputId} autoFocus type="number" inputMode="decimal" min={0} max={100} step="0.1" required
          value={value} onChange={(event) => setValue(event.target.value)} disabled={busy} />
        <span>%</span>
        <Button type="submit" size="sm" disabled={busy}>{busy ? <LoaderCircle size={14} className="spin" /> : <Check size={14} />} Übernehmen</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { setOpen(false); setError(""); }}>Abbrechen</Button>
      </div>
      <small>Wird als Messwert gespeichert und zum Lernen verwendet.</small>
      {error && <p className="level-correction-error" role="alert">{error}</p>}
    </form>}
  </div>;
}
