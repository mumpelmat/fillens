"use client";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Check,
  Crop,
  GraduationCap,
  LoaderCircle,
  Save,
  Upload,
  X,
  ZoomIn,
  SkipForward,
  Plus,
  Settings2,
  Archive,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, basePath, date, number, write, type Container } from "@/lib/api";
import { ContainerTabs } from "@/components/dashboard";
import { refreshLearningPage } from "@/lib/learning-refresh";
type Point = { x: number; y: number };
type ROI =
  { x: number; y: number; width: number; height: number } | { points: Point[] };
const fullROI: ROI = { x: 0, y: 0, width: 1, height: 1 };
const pointsOf = (roi: ROI): Point[] =>
  "points" in roi
    ? roi.points
    : [
        { x: roi.x, y: roi.y },
        { x: roi.x + roi.width, y: roi.y },
        { x: roi.x + roi.width, y: roi.y + roi.height },
        { x: roi.x, y: roi.y + roi.height },
      ];
const rejectionName = (reason: string) => reason === "lid_closed" ? "Deckel zu" : reason === "obstructed" ? "Sicht verdeckt" : "Nicht messbar";
const modeName = (m: string) =>
  m === "day" ? "Tag" : m === "night_ir" ? "Nacht" : "Unbekannt";
type Overview = {
  roi: ROI | null;
  learning_enabled: boolean;
  training_mode: "automatic" | "manual";
  image_retention_days: number;
  sample_count: number;
  approved_count: number;
  storage_bytes: number;
  latest_image: {id: string; content_url: string; captured_at: string; imported_at: string; state: string; sample_id: string | null} | null;
  recognition_requested: boolean;
  min_confidence: number;
  transform_key: string;
  upload_limit_bytes: number;
  series: { name: string; sample_count: number }[];
  collection: {
    open_count: number;
    limit: number | null;
    last_import_at: string | null;
    scan_at: string | null;
    live_error: string | null;
    deferred_count: number;
    newest_at: string | null;
    oldest_at: string | null;
    status: string;
    error: string | null;
    rejections: {
      count: number;
      latest_name: string | null;
      latest_attempts: number;
      latest_blocked: boolean;
      latest_retry_at: string | null;
    };
  };
  summary: {
    state: string;
    label: string;
    hint: string;
    can_retry: boolean;
    can_train: boolean;
    training_running: boolean;
    approved_count: number;
    has_active_recognition: boolean;
    unusable: { approved_count: number; ready_modes: string[] };
    modes: Record<
      string,
      {
        ready: boolean;
        label: string;
        approved_count: number;
        series_count: number;
        distinct_levels: number;
        target_span: number;
        validation_samples: number;
        mae_percent_points: number | null;
        estimated_reliability: number | null;
        expected_error_percent_points: number | null;
        recommendation: string;
      }
    >;
  };
};
type Item = {
  id: string;
  sample_id: string | null;
  content_url: string;
  has_reference: boolean;
  source: string;
  name: string;
  created_at: string;
  received_at: string;
  measured_at: string | null;
  fill_level: number | null;
  rejection_reason: string | null;
  analysis_reason: string | null;
  suggested_fill_level: number | null;
  estimated_error: number | null;
  image_mode: string;
  series_name: string;
  approved: boolean;
  transform_key: string | null;
};
type RestorePoint = {
  id: string;
  reason: string;
  approved_count: number;
  active_model_count: number;
  created_at: string;
};
type Queue = {
  offset: number;
  items: Item[];
  total_count: number;
  remaining_count: number;
  pending_count: number;
  confirmed_count: number;
};
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

function ROIEditor({
  src,
  roi,
  onChange,
}: {
  src: string | undefined;
  roi: ROI;
  onChange: (roi: ROI) => void;
}) {
  const [drawing, setDrawing] = useState(false);
  const drag = useRef<number | null>(null);
  const points = pointsOf(roi);
  const bounds = (event: React.PointerEvent<HTMLDivElement>) => {
    const r = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (event.clientY - r.top) / r.height)),
    };
  };
  const shape = points.map((p) => `${p.x * 100},${p.y * 100}`).join(" ");
  return (
    <>
      {src ? (
        <div
          className="roi-stage"
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            if (drawing && points.length < 32)
              onChange({ points: [...points, bounds(e)] });
          }}
          onPointerMove={(e) => {
            if (drag.current == null) return;
            const next = [...points];
            next[drag.current] = bounds(e);
            onChange({ points: next });
          }}
          onPointerUp={() => {
            drag.current = null;
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
        >
          <img
            src={src}
            alt="Vieleck um den relevanten Behälterinhalt festlegen"
            draggable={false}
          />
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <defs>
              <mask id="roi-mask">
                <rect width="100" height="100" fill="white" />
                <polygon points={shape} fill="black" />
              </mask>
            </defs>
            <rect
              width="100"
              height="100"
              fill="#10251d"
              opacity=".55"
              mask="url(#roi-mask)"
            />
            <polygon
              points={shape}
              fill="none"
              stroke="#b9ef8a"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            {points.map((p, i) => (
              <circle
                key={i}
                cx={p.x * 100}
                cy={p.y * 100}
                r="1.2"
                fill={i === 0 ? "#fff" : "#b9ef8a"}
                stroke="#237c60"
                strokeWidth=".3"
                style={{
                  pointerEvents: "all",
                  cursor: drawing ? "pointer" : "grab",
                }}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  if (drawing) {
                    if (i === 0 && points.length >= 3) setDrawing(false);
                  } else {
                    drag.current = i;
                    (
                      e.currentTarget.parentElement
                        ?.parentElement as HTMLElement
                    )?.setPointerCapture(e.pointerId);
                  }
                }}
              />
            ))}
          </svg>
        </div>
      ) : (
        <div className="training-empty">
          <Crop size={27} />
          <p>
            Zuerst ein Trainingsbild hochladen oder ein Kamerabild importieren.
          </p>
        </div>
      )}
      <p className="hint">
        {drawing
          ? "Eckpunkte nacheinander anklicken. Zum Schließen den ersten Punkt oder ‚Vieleck schließen‘ wählen."
          : "Eckpunkte ziehen oder Koordinaten bearbeiten. Nur das Innere des Vielecks wird ausgewertet."}
      </p>
      <div className="training-actions">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            setDrawing(true);
            onChange({ points: [] });
          }}
        >
          Vieleck neu zeichnen
        </Button>
        {drawing && (
          <Button
            type="button"
            size="sm"
            disabled={points.length < 3}
            onClick={() => setDrawing(false)}
          >
            Vieleck schließen
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            setDrawing(false);
            onChange({ points: pointsOf(fullROI) });
          }}
        >
          Gesamtes Bild
        </Button>
      </div>
      <div className="polygon-points">
        {points.map((p, i) => (
          <div key={i}>
            <span>Punkt {i + 1}</span>
            {(["x", "y"] as const).map((key) => (
              <label key={key}>
                {key.toUpperCase()} (%)
                <input
                  type="number"
                  min={0}
                  max={100}
                  step="0.1"
                  value={Math.round(p[key] * 1000) / 10}
                  onChange={(e) => {
                    const next = [...points];
                    next[i] = { ...p, [key]: Number(e.target.value) / 100 };
                    onChange({ points: next });
                  }}
                />
              </label>
            ))}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label={`Punkt ${i + 1} entfernen`}
              disabled={points.length <= 3 && !drawing}
              onClick={() =>
                onChange({ points: points.filter((_, j) => j !== i) })
              }
            >
              <X size={14} />
            </Button>
          </div>
        ))}
      </div>
    </>
  );
}

function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [zoom, setZoom] = useState(false);
  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog
      className="image-lightbox"
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-label="Referenzbild vergrößert"
    >
      <div className="lightbox-controls">
        <Button variant="outline" onClick={() => setZoom(!zoom)}>
          {zoom ? "Einpassen" : "Originalgröße"}
        </Button>
        <Button variant="outline" aria-label="Bild schließen" onClick={onClose}>
          <X size={20} />
        </Button>
      </div>
      <div className={`lightbox-image ${zoom ? "zoomed" : ""}`}>
        <img
          src={src}
          alt="Referenzbild zur Prüfung des tatsächlichen Füllstands"
        />
      </div>
    </dialog>
  );
}

function PanelDialog({
  title,
  children,
  onClose,
  busy,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog
      className="learning-dialog"
      ref={ref}
      aria-label={title}
      onCancel={(e) => {
        if (busy) e.preventDefault();
        else onClose();
      }}
    >
      <div className="learning-dialog-head">
        <h2>{title}</h2>
        <Button
          aria-label="Schließen"
          variant="ghost"
          size="icon"
          disabled={busy}
          onClick={onClose}
        >
          <X size={20} />
        </Button>
      </div>
      {children}
    </dialog>
  );
}

function TrainingCamera({
  admin,
  container,
  onChanged,
}: {
  admin: boolean;
  container: Container;
  onChanged: () => Promise<void>;
}) {
  const camera = container.camera;
  const [info, setInfo] = useState<Overview | null>(null);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [restorePoints, setRestorePoints] = useState<RestorePoint[]>([]);
  const [selected, setSelected] = useState("");
  const [tab, setTab] = useState("pending");
  const [excluded, setExcluded] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dialog, setDialog] = useState<"upload" | "roi" | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [roi, setROI] = useState<ROI>(fullROI);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState("");
  const [recognition, setRecognition] = useState(true);
  const [minimum, setMinimum] = useState(75);
  const [newImages, setNewImages] = useState(false);
  const [retentionDays, setRetentionDays] = useState(7);
  const [retentionCount, setRetentionCount] = useState<number | null>(null);
  const [trainingMode, setTrainingMode] = useState<"automatic" | "manual">("automatic");
  const displayedQueue = useRef<Queue | null>(null);
  const initialized = useRef(false);
  const loadVersion = useRef(0);
  const limit = 20;
  const current =
    queue?.items.find((i) => i.id === selected) || queue?.items[0];
  const load = useCallback(async (background = false) => {
    const version = ++loadVersion.current;
    const [next, rows, points] = await Promise.all([
      api<Overview>(`/training/cameras/${camera.id}`),
      api<Queue>(
        `/training/cameras/${camera.id}/queue?tab=${tab}&offset=${offset}&limit=${limit}&exclude=${encodeURIComponent(excluded.join(","))}`,
      ),
      admin
        ? api<{ items: RestorePoint[] }>(
            `/training/cameras/${camera.id}/restore-points`,
          )
        : Promise.resolve({ items: [] }),
    ]);
    if (version !== loadVersion.current) return;
    setInfo(next);
    const refresh = refreshLearningPage(displayedQueue.current, rows, background);
    displayedQueue.current = refresh.page;
    setQueue(refresh.page);
    setNewImages(old => background ? old || refresh.hasUpdates : false);
    setRestorePoints(points.items);
    if (!background || refresh.page === rows) setSelected((previous) =>
      rows.items.some((i) => i.id === previous) ? previous : rows.items[0]?.id || "",
    );
    if (!initialized.current) {
      setRetentionDays(next.image_retention_days);
      setTrainingMode(next.training_mode);
      setRecognition(next.recognition_requested);
      setMinimum(next.min_confidence * 100);
      initialized.current = true;
    }
  }, [admin, camera.id, tab, offset, excluded]);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    load()
      .catch((e) => {
        if (alive) { setError(e.message); if (e.status === 403) void onChanged(); }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
      loadVersion.current++;
    };
  }, [load]);
  useEffect(() => {
    if (busy) return;
    const timer = setInterval(() => {
      void load(true).catch((e) => {setError(e.message); if(e.status === 403) void onChanged();});
    }, 5000);
    return () => clearInterval(timer);
  }, [load, busy]);
  useEffect(() => {
    if (!admin || !info || retentionDays >= info.image_retention_days || !Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 365) {
      setRetentionCount(null);
      return;
    }
    let alive = true;
    setRetentionCount(null);
    api<{affected_unreviewed: number}>(`/training/cameras/${camera.id}/retention-preview?days=${retentionDays}`)
      .then(result => {if (alive) setRetentionCount(result.affected_unreviewed);})
      .catch(() => {if (alive) setError("Die Auswirkung der Aufbewahrung konnte nicht ermittelt werden.");});
    return () => {alive = false;};
  }, [admin, camera.id, retentionDays, info?.image_retention_days]);
  async function openLatest() {
    if (!info?.latest_image?.sample_id) return;
    const latest = info.latest_image;
    const nextTab = ["confirmed", "unusable"].includes(latest.state) ? "confirmed" : "pending";
    setBusy(true);
    try {
      const rows = await api<Queue>(`/training/cameras/${camera.id}/queue?tab=${nextTab}&limit=${limit}&focus=sample_${latest.sample_id}`);
      loadVersion.current++;
      setTab(nextTab);
      setOffset(rows.offset);
      setExcluded([]);
      displayedQueue.current = rows;
      setQueue(rows);
      setSelected(`sample_${latest.sample_id}`);
      setNewImages(false);
    } catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await load();
      await onChanged();
      setNotice(message);
    } catch (e) {
      setError((e as Error).message);
      if ((e as {status?: number}).status === 403) await onChanged();
    } finally {
      setBusy(false);
    }
  }
  async function confirm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!current) return;
    const form = new FormData(e.currentTarget);
    await act(
      async () => {
        await api(
          `/training/cameras/${camera.id}/queue/${current.id}/confirm`,
          write({
            fill_level: Number(form.get("fill")),
            ...(admin ? {image_mode: form.get("mode"), series_name: form.get("series")} : {}),
          }),
        );
      },
      info?.learning_enabled
        ? "Bestätigt. Der Lernstand wird automatisch aktualisiert, sobald genug geprüfte Bilder vorliegen."
        : "Bestätigt und gespeichert. Weiteres Lernen ist pausiert.",
    );
  }
  async function learnUnusable(button: HTMLButtonElement) {
    if (!current || !button.form) return;
    const form = new FormData(button.form);
    await act(async () => {
      await api(
        `/training/cameras/${camera.id}/queue/${current.id}/unusable`,
        write({reason: form.get("rejection_reason"),
          ...(admin ? {image_mode: form.get("mode"), series_name: form.get("series")} : {})}),
      );
    }, "Als ungeeignetes Lernbild gespeichert. Bestehende Messwerte bleiben erhalten; die Erkennung wird nach Qualitätsprüfung aktualisiert.");
  }
  function skip() {
    if (!current) return;
    if (tab === "pending") {
      void act(() => api(`/training/cameras/${camera.id}/queue/${current.id}/discard`, {method: "POST"}), "Bild verworfen. Es ist Platz für ein neues Prüfbild.");
      return;
    }
    setExcluded((v) => [...v, current.id]);
    setOffset(0);
    setNotice("");
    setError("");
  }
  function changeTab(value: string) {
    setTab(value);
    setQueue(null);
    setOffset(0);
    setExcluded([]);
    setNotice("");
  }
  function addFiles(next: File[]) {
    setFiles(next);
    setDialog("upload");
    setError("");
  }
  async function upload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!files.length) return;
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    setNotice("");
    let count = 0;
    const failed: string[] = [];
    for (let index = 0; index < files.length; index++) {
      const file = files[index];
      setProgress(`${index + 1} / ${files.length} · ${file.name}`);
      const body = new FormData();
      body.set("camera_id", camera.id);
      body.set("file", file);
      body.set("image_mode", "auto");
      body.set(
        "series_name",
        String(form.get("series") || "").trim() ||
          `Kamera ${new Date().toISOString().slice(0, 10)}`,
      );
      const fill = String(form.get("fill") || "");
      if (fill !== "") body.set("target_fill_level", fill);
      try {
        await api("/training/samples", { method: "POST", body });
        count++;
      } catch (e) {
        failed.push(`${file.name}: ${(e as Error).message}`);
      }
    }
    try {
      await load();
      await onChanged();
    } catch (e) {
      failed.push((e as Error).message);
    } finally {
      setBusy(false);
      setProgress("");
    }
    setNotice(`${count} Bild(er) hinzugefügt.`);
    if (failed.length) setError(failed.slice(0, 3).join(" "));
    else {
      setDialog(null);
      setFiles([]);
      setTab("pending");
      setOffset(0);
      setExcluded([]);
    }
  }
  const src = current?.has_reference
    ? `${basePath}/api${current.content_url}`
    : undefined;
  const setupSource =
    src ||
    (container.current_image
      ? `${basePath}/api/images/${container.current_image.id}/content`
      : undefined);
  const incompatible =
    !!current?.transform_key &&
    !!info &&
    current.transform_key !== info.transform_key;
  const notices = (
    <>
      {error && (
        <div className="error-notice" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="learning-notice" role="status">
          {notice}
        </div>
      )}
    </>
  );
  return (
    <>
      <div className="learning-bar">
        <div>
          <strong>{info?.summary.label || "Lernstand wird geladen …"}</strong>
          <p>
            {queue
              ? `${queue.confirmed_count} bestätigt · ${queue.pending_count} zu prüfen`
              : ""}
          </p>
        </div>
        <div className="learning-mode-status">
          {(["day", "night_ir"] as const).map((mode) => (
            <span
              key={mode}
              className={
                info?.summary.modes[mode]?.ready ? "ready" : "collecting"
              }
            >
              <strong>{modeName(mode)}</strong> ·{" "}
              {info?.summary.modes[mode]?.label || "Wird geladen"}
              {info?.summary.modes[mode]?.mae_percent_points != null && (
                <small>
                  Fehler {number(info.summary.modes[mode].mae_percent_points)} PP
                  {info.summary.modes[mode].estimated_reliability != null &&
                    ` · Zuverlässigkeit ${number(info.summary.modes[mode].estimated_reliability * 100)} %`}
                </small>
              )}
            </span>
          ))}
        </div>
        {admin && <label className="learning-toggle">
          <input
            type="checkbox"
            role="switch"
            checked={info?.learning_enabled ?? false}
            disabled={busy || !info}
            onChange={(e) => {
              const enabled = e.target.checked;
              const previous = info?.learning_enabled ?? false;
              setInfo((old) =>
                old ? { ...old, learning_enabled: enabled } : old,
              );
              void act(
                async () => {
                  try {
                    await api(
                      `/training/cameras/${camera.id}/learning`,
                      write({ enabled }, "PUT"),
                    );
                  } catch (e) {
                    setInfo((old) =>
                      old ? { ...old, learning_enabled: previous } : old,
                    );
                    throw e;
                  }
                },
                enabled
                  ? "Weiter lernen eingeschaltet."
                  : "Lernen pausiert. Die vorhandene Erkennung bleibt nutzbar.",
              );
            }}
          />
          <span>Weiter lernen</span>
        </label>}
      </div>
      {info?.summary.unusable && <p className="muted" role="status">
        {info.summary.unusable.approved_count} bestätigte ungeeignete Lernbilder · {info.summary.unusable.ready_modes.length
          ? `Ungeeignete Bilder erkennen: ${info.summary.unusable.ready_modes.map(modeName).join(", ")}`
          : "Automatisches Verwerfen noch nicht freigegeben. Messbare und ungeeignete Bilder aus mindestens zwei unabhängigen Aufnahmegruppen je Bildmodus bestätigen."}
      </p>}
      {admin && info && <form className="panel learning-controls" onSubmit={e => {
        e.preventDefault();
        void act(async () => {
          await api(`/training/cameras/${camera.id}/learning`, write({training_mode: trainingMode, image_retention_days: retentionDays}, "PUT"));
        }, "Lerneinstellungen gespeichert.");
      }}>
        <Field label="Training">
          <select value={trainingMode} onChange={e => setTrainingMode(e.target.value as "automatic" | "manual")}>
            <option value="automatic">Automatisch trainieren</option>
            <option value="manual">Training manuell starten</option>
          </select>
        </Field>
        <Field label="Automatische Bilder aufbewahren (Tage)" hint="1–365 Tage ab lokaler Aufnahme. Bestätigte Lernbilder, Uploads und die neueste Vorschau bleiben erhalten.">
          <input type="number" min={1} max={365} required value={retentionDays} onChange={e => setRetentionDays(Number(e.target.value))}/>
        </Field>
        {retentionDays < info.image_retention_days && <p role="status">{retentionCount === null ? "Betroffene Bilder werden ermittelt …" : `${retentionCount} unbewertete Lernbilder werden durch die verkürzte Aufbewahrung entfernt.`}</p>}
        <Button disabled={busy || (retentionDays < info.image_retention_days && retentionCount === null)}>Einstellungen speichern</Button>
        <Button type="button" variant="outline" disabled={busy || !info.summary.can_train} onClick={() => void act(
          () => api("/models/train", write({camera_id: camera.id, managed: true})), "Training wurde angefordert. Geprüfte Verbesserungen werden automatisch übernommen."
        )}>Training starten</Button>
        <p className="hint">{info.training_mode === "manual" ? "Bewertungen werden gespeichert; Training beginnt erst auf Anforderung." : "Bestätigte Bewertungen werden automatisch zum Training verwendet."} {info.summary.training_running && "Ein bereits laufendes Training wird abgeschlossen."} Sammlung und Training pausieren über „Weiter lernen“; die Erkennung bleibt separat steuerbar.</p>
      </form>}
      {info?.latest_image && <section className="panel learning-latest" aria-label="Aktuelles Kamerabild">
        <img src={`${basePath}/api${info.latest_image.content_url}`} alt="Neueste verfügbare Kameraaufnahme" loading="lazy"/>
        <div><strong>Aktuelles Kamerabild</strong>
          <p>Aufnahme: {date(info.latest_image.captured_at, camera.timezone)} · Import: {date(info.latest_image.imported_at, camera.timezone)}</p>
          <p>{({pending: "Offen", confirmed: "Bestätigt", unusable: "Als ungeeignet bewertet", discarded: "Verworfen", expired: "Aufbewahrung abgelaufen", waiting: "Übernahme in die Lernliste ausstehend", deferred: "Übernahme in die Lernliste ausstehend"} as Record<string,string>)[info.latest_image.state]}</p>
          {["pending", "confirmed", "unusable"].includes(info.latest_image.state) && <Button type="button" variant="outline" disabled={busy} onClick={() => void openLatest()}>Bildbewertung öffnen</Button>}
        </div>
      </section>}
      {newImages && <div className="learning-notice" role="status">Neue Bilder verfügbar <Button variant="outline" disabled={busy} onClick={() => {
        setTab("pending"); setOffset(0); setExcluded([]); setSelected("");
        displayedQueue.current = null; setQueue(null); setNewImages(false);
        if (tab === "pending" && offset === 0 && excluded.length === 0) void load();
      }}>Neueste Bilder anzeigen</Button></div>}
      <div className="learning-tools">
        <div role="tablist" aria-label="Bildliste">
          <button
            role="tab"
            aria-selected={tab === "pending"}
            onClick={() => changeTab("pending")}
          >
            Zu prüfen <span>{queue?.pending_count ?? 0}</span>
          </button>
          <button
            role="tab"
            aria-selected={tab === "confirmed"}
            onClick={() => changeTab("confirmed")}
          >
            Bestätigt <span>{queue?.confirmed_count ?? 0}</span>
          </button>
        </div>
        {admin && <div
          className={`learning-add ${dragging ? "dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!busy) addFiles(Array.from(e.dataTransfer.files));
          }}
        >
          <span>Bilder hier ablegen</span>
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => {
              setError("");
              setDialog("upload");
            }}
          >
            <Plus size={15} />
            Bilder hinzufügen
          </Button>
        </div>}
      </div>
      {info?.collection && <div className="hint" role="status">
        <p>{queue?.pending_count ?? info.collection.open_count} offen · {info.approved_count} bestätigt · {number(info.storage_bytes / 1024 / 1024)} MB Lernbilder · Aufbewahrung: {info.image_retention_days} Tage</p>
        <p>Letzter Import: {date(info.collection.last_import_at, camera.timezone)} · Neuestes offenes Lernbild: {date(info.collection.newest_at, camera.timezone)}</p>
        <p>{!info.learning_enabled ? "Sammlung pausiert" : "Laufende Sammlung aktiv"}{info.collection.live_error && ` · Import: ${info.collection.live_error}`}</p>
        <p>FTP-Nachsammlung: {info.collection.status === "unconfigured" ? "Keine automatische Bildquelle eingerichtet" : info.collection.status === "blocked" || info.collection.status === "error" ? "Gestört" : info.collection.status === "paused" ? "Pausiert" : info.collection.status === "collecting" ? "Bilder werden gesucht …" : info.collection.status === "exhausted" ? "Aktuell keine weiteren Bilder gefunden" : "Aktiv"}{info.collection.error && ` · ${info.collection.error}`}{info.collection.rejections.count > 0 && ` · ${info.collection.rejections.count} FTP-Datei(en) abgelehnt`}</p>
      </div>}
      {notices}
      {admin && info && !info.roi && (
        <div className="learning-setup">
          <span>
            Einmalig den relevanten Behälterbereich festlegen, damit die
            Erkennung lernen kann.
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setROI(fullROI);
              setDialog("roi");
            }}
          >
            Bildbereich festlegen
          </Button>
        </div>
      )}
      {admin && info?.roi && !info.recognition_requested && (
        <div className="learning-setup">
          <span>Die automatische Füllstandserkennung ist pausiert.</span>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                await api(
                  `/training/cameras/${camera.id}/config`,
                  write(
                    {
                      roi: info.roi,
                      recognition_requested: true,
                      min_confidence: info.min_confidence,
                    },
                    "PUT",
                  ),
                );
                setRecognition(true);
              }, "Automatische Erkennung eingeschaltet. Ein geeigneter Lernstand wird automatisch verwendet.")
            }
          >
            Erkennung fortsetzen
          </Button>
        </div>
      )}
      {admin && info?.summary.can_retry && (
        <div className="learning-setup">
          <span>{info.summary.hint}</span>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void act(
                () =>
                  api(`/training/cameras/${camera.id}/retry`, {
                    method: "POST",
                  }),
                "Lernen wird erneut versucht.",
              )
            }
          >
            Erneut versuchen
          </Button>
        </div>
      )}
      <section className="panel learning-workspace" aria-label="Bildprüfung">
        {loading && !queue ? (
          <div className="learning-empty">
            <LoaderCircle className="spin" />
            Bilder werden geladen …
          </div>
        ) : !current ? (
          <div className="learning-empty">
            <Check size={26} />
            <h2>
              {tab === "confirmed"
                ? "Noch keine bestätigten Bilder"
                : excluded.length && queue?.pending_count
                  ? "Übrige Bilder übersprungen"
                  : "Alles geprüft"}
            </h2>
            <p>
              {tab === "confirmed"
                ? "Bestätigte Bilder findest du später hier."
                : "Neue Bilder hinzufügen oder weitere Kamerabilder prüfen, sobald sie eintreffen."}
            </p>
            {excluded.length > 0 && (
              <Button
                variant="outline"
                onClick={() => {
                  setExcluded([]);
                  setOffset(0);
                }}
              >
                Übersprungene Bilder anzeigen
              </Button>
            )}
          </div>
        ) : (
          <>
            <div className="learning-review">
              <div className="learning-picture">
                <div className="learning-picture-head">
                  <span>
                    {current.source !== "upload"
                      ? "Kamerabild"
                      : "Hinzugefügtes Bild"}{" "}
                    · {modeName(current.image_mode)}
                  </span>
                  <span>
                    {date(
                      current.measured_at || current.created_at,
                      camera.timezone,
                    )}
                  </span>
                </div>
                {src ? (
                  <button
                    type="button"
                    className="learning-full-image"
                    aria-label="Referenzbild vergrößern"
                    onClick={() => setLightbox(src)}
                  >
                    <img
                      src={src}
                      alt="Referenzbild: tatsächlichen Füllstand prüfen"
                    />
                    <span>
                      <ZoomIn size={18} />
                    </span>
                  </button>
                ) : (
                  <div className="learning-missing">
                    Dieses frühere Referenzbild ist nicht mehr vorhanden. Du
                    kannst es überspringen.
                  </div>
                )}
              </div>
              <form
                key={`${current.id}-${current.fill_level}-${current.rejection_reason}-${current.image_mode}-${current.series_name}`}
                className="learning-review-form"
                onSubmit={confirm}
              >
                <div>
                  <span className="eyebrow">BILD PRÜFEN</span>
                  <h2>Wie voll ist der Behälter?</h2>
                  <p>Zur Lernliste hinzugefügt: {date(current.received_at, camera.timezone)}</p>
                  <p>Den tatsächlichen Füllstand eingeben.</p>
                </div>
                {current.rejection_reason && <p role="status"><strong>Ungeeignet: {rejectionName(current.rejection_reason)}</strong> · Als Gegenbeispiel gespeichert, ohne Füllstand.</p>}
                {current.analysis_reason && <p className="muted">{current.analysis_reason}</p>}
                {current.suggested_fill_level != null && (
                  <p className="learning-estimate">
                    Bisherige Schätzung:{" "}
                    <strong>{number(current.suggested_fill_level)} %</strong>
                    {current.estimated_error != null && (
                      <> · erwartete Abweichung ±{number(current.estimated_error)} PP</>
                    )}
                  </p>
                )}
                {incompatible && (
                  <div className="error-notice">
                    Bild passt nicht zur aktuellen Kameradrehung. Bitte passende
                    Bilder hinzufügen.
                  </div>
                )}
                <Field label="Tatsächlicher Füllstand (%)">
                  <input
                    name="fill"
                    type="number"
                    min={0}
                    max={100}
                    step="0.1"
                    required
                    autoFocus
                    defaultValue={current.fill_level ?? ""}
                    placeholder="0–100"
                    onFocus={(e) => e.currentTarget.select()}
                  />
                </Field>
                <Button type="submit" disabled={busy || !src || incompatible}>
                  {busy ? (
                    <LoaderCircle size={16} className="spin" />
                  ) : (
                    <Check size={16} />
                  )}{" "}
                  {tab === "pending"
                    ? "Bestätigen & weiter"
                    : "Änderung speichern"}
                </Button>
                <Field label="Grund für ungeeignetes Lernbild">
                  <select name="rejection_reason" defaultValue={current.rejection_reason ?? "lid_closed"}>
                    <option value="lid_closed">Deckel zu</option>
                    <option value="obstructed">Sicht verdeckt</option>
                    <option value="unusable">Nicht messbar</option>
                  </select>
                </Field>
                <Button variant="outline" type="button" disabled={busy || !src || incompatible}
                  onClick={(e) => void learnUnusable(e.currentTarget)}>
                  {tab === "pending" ? "Als ungeeignet lernen & weiter" : "Als ungeeignet speichern"}
                </Button>
                <small>Das Bild bleibt zum Lernen erhalten. Für Duplikate oder unnötige Bilder „Nur verwerfen“ wählen.</small>
                <Button
                  variant="ghost"
                  type="button"
                  disabled={busy}
                  onClick={skip}
                >
                  <SkipForward size={15} />
                  {tab === "pending" ? "Nur verwerfen & weiter" : "Weiteres Bild"}
                </Button>
                {admin ? <details className="learning-image-details">
                  <summary>Bilddetails</summary>
                  <Field label="Tag / Nacht">
                    <select name="mode" defaultValue={current.image_mode}>
                      <option value="day">Tag</option>
                      <option value="night_ir">Nacht / IR</option>
                      <option value="unknown">Unbekannt</option>
                    </select>
                  </Field>
                  <Field label="Aufnahmegruppe">
                    <input
                      name="series"
                      maxLength={100}
                      required
                      defaultValue={current.series_name}
                      list={`series-${camera.id}`}
                    />
                  </Field>
                  {current.sample_id && (
                    <Button
                      variant="ghost"
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm("Dieses Lernbild dauerhaft löschen?")
                        )
                          void act(
                            () =>
                              api(`/training/samples/${current.sample_id}`, {
                                method: "DELETE",
                              }),
                            "Bild entfernt.",
                          );
                      }}
                    >
                      Bild entfernen
                    </Button>
                  )}
                </details> : <p className="hint">{modeName(current.image_mode)} · {current.series_name}</p>}
              </form>
            </div>
            <div className="learning-filmstrip" aria-label="Bilder auswählen">
              {queue?.items.map((item) => (
                <button
                  key={item.id}
                  aria-label={`Bild auswählen: ${item.name}`}
                  aria-pressed={current.id === item.id}
                  disabled={busy}
                  className={current.id === item.id ? "selected" : ""}
                  onClick={() => setSelected(item.id)}
                >
                  {item.has_reference ? (
                    <img
                      loading="lazy"
                      src={`${basePath}/api${item.content_url}`}
                      alt=""
                    />
                  ) : (
                    <span>Kein Bild</span>
                  )}
                  <span>
                    {item.rejection_reason ? `Ungeeignet: ${rejectionName(item.rejection_reason)}` : item.approved && item.fill_level != null
                      ? `${number(item.fill_level)} %`
                      : modeName(item.image_mode)}
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
        {queue && queue.remaining_count > limit && (
          <div className="learning-pages">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Zurück
            </Button>
            <span>
              {offset + 1}–{Math.min(offset + limit, queue.remaining_count)} /{" "}
              {queue.remaining_count}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + limit >= queue.remaining_count}
              onClick={() => setOffset(offset + limit)}
            >
              Weitere Bilder
            </Button>
          </div>
        )}
      </section>
      <div className="learning-footer">
        <span>{info?.summary.hint}</span>
        {admin && <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => {
            setROI(info?.roi || fullROI);
            setError("");
            setDialog("roi");
          }}
        >
          <Crop size={15} />
          Bildbereich bearbeiten
        </Button>}
      </div>
      {admin && <details className="learning-settings">
        <summary>
          <Settings2 size={15} />
          Weitere Einstellungen
        </summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!info?.roi) {
              setError("Zuerst den Bildbereich festlegen.");
              return;
            }
            void act(
              () =>
                api(
                  `/training/cameras/${camera.id}/config`,
                  write(
                    {
                      roi: info.roi,
                      recognition_requested: recognition,
                      min_confidence: minimum / 100,
                    },
                    "PUT",
                  ),
                ),
              "Einstellungen gespeichert.",
            );
          }}
        >
          <label className="check-field">
            <input
              type="checkbox"
              checked={recognition}
              onChange={(e) => setRecognition(e.target.checked)}
            />
            Automatische Füllstandserkennung eingeschaltet
          </label>
          <Field
            label="Mindestvertrauen für automatische Messwerte (%)"
            hint="Unsichere Ergebnisse bleiben zur Prüfung offen."
          >
            <input
              type="number"
              min={50}
              max={100}
              value={minimum}
              onChange={(e) => setMinimum(Number(e.target.value))}
            />
          </Field>
          <p>
            Neue Lernstände werden automatisch geprüft und übernommen. Bilder
            aus derselben Aufnahmerunde sollten derselben Aufnahmegruppe
            zugeordnet bleiben.
          </p>
          <Button size="sm" disabled={busy}>
            Einstellungen speichern
          </Button>
        </form>
        <section className="learning-restore" aria-label="Lernstand sichern und wiederherstellen">
          <div>
            <h3>Lernstand sichern & zurücksetzen</h3>
            <p>
              Eine Sicherung enthält Modelle, bestätigte Vorgaben, Bildbereich
              und Erkennungseinstellungen. Messwerte und Bilder bleiben bei
              einem Reset erhalten.
            </p>
          </div>
          <div className="learning-restore-actions">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() =>
                void act(
                  () =>
                    api(`/training/cameras/${camera.id}/restore-points`, {
                      method: "POST",
                    }),
                  "Wiederherstellungspunkt erstellt.",
                )
              }
            >
              <Archive size={15} /> Jetzt sichern
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    "Lernstand wirklich zurücksetzen? Vorher wird automatisch eine Sicherung erstellt. Messwerte und Bilder bleiben erhalten.",
                  )
                )
                  void act(
                    () =>
                      api(`/training/cameras/${camera.id}/reset`, {
                        method: "POST",
                      }),
                    "Lernstand zurückgesetzt. Die neuesten Bilder stehen erneut zur Prüfung bereit.",
                  );
              }}
            >
              <RotateCcw size={15} /> Lernstand zurücksetzen
            </Button>
          </div>
          {restorePoints.length > 0 && (
            <div className="learning-restore-list">
              {restorePoints.map((point) => (
                <div key={point.id}>
                  <span>
                    <strong>{date(point.created_at, camera.timezone)}</strong>
                    {` · ${point.approved_count} Vorgaben · ${point.active_model_count} Modell(e)`}
                  </span>
                  <div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(
                            "Diesen Lernstand wiederherstellen? Der aktuelle Stand wird vorher automatisch gesichert.",
                          )
                        )
                          void act(
                            () =>
                              api(
                                `/training/cameras/${camera.id}/restore-points/${point.id}/restore`,
                                { method: "POST" },
                              ),
                            "Lernstand wiederhergestellt.",
                          );
                      }}
                    >
                      Wiederherstellen
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Wiederherstellungspunkt löschen"
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm("Diese Sicherung dauerhaft löschen?"))
                          void act(
                            () =>
                              api(
                                `/training/cameras/${camera.id}/restore-points/${point.id}`,
                                { method: "DELETE" },
                              ),
                            "Wiederherstellungspunkt gelöscht.",
                          );
                      }}
                    >
                      <Trash2 size={15} />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </details>}
      <datalist id={`series-${camera.id}`}>
        {info?.series.map((s) => (
          <option key={s.name} value={s.name}>
            {s.sample_count} Bilder
          </option>
        ))}
      </datalist>
      {lightbox && (
        <Lightbox src={lightbox} onClose={() => setLightbox(null)} />
      )}
      {admin && dialog === "roi" && (
        <PanelDialog
          title="Bildbereich festlegen"
          busy={busy}
          onClose={() => setDialog(null)}
        >
          <div className="learning-dialog-body">
            {error && (
              <div className="error-notice" role="alert">
                {error}
              </div>
            )}
            <ROIEditor src={setupSource} roi={roi} onChange={setROI} />
            <Button
              disabled={busy || pointsOf(roi).length < 3}
              onClick={() =>
                void act(async () => {
                  await api(
                    `/training/cameras/${camera.id}/config`,
                    write(
                      {
                        roi,
                        recognition_requested:
                          info?.recognition_requested ?? true,
                        min_confidence: info?.min_confidence ?? 0.75,
                      },
                      "PUT",
                    ),
                  );
                  setDialog(null);
                }, "Bildbereich gespeichert. Der Lernstand wird passend aktualisiert.")
              }
            >
              <Save size={15} />
              Bildbereich speichern
            </Button>
          </div>
        </PanelDialog>
      )}
      {admin && dialog === "upload" && (
        <PanelDialog
          title="Bilder hinzufügen"
          busy={busy}
          onClose={() => setDialog(null)}
        >
          <form onSubmit={upload} className="learning-dialog-body">
            {notices}
            <div
              className={`training-dropzone ${dragging ? "dragging" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (!busy) setFiles(Array.from(e.dataTransfer.files));
              }}
            >
              <Upload size={24} />
              <p>Bilder hier ablegen oder Dateien auswählen</p>
              <input
                aria-label="Bilder auswählen"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                disabled={busy}
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
              />
              <p role="status">{files.length} Datei(en) ausgewählt</p>
            </div>
            <p className="learning-upload-hint">
              JPEG, PNG oder WebP · bis{" "}
              {number((info?.upload_limit_bytes || 15728640) / 1024 / 1024, 0)}{" "}
              MiB pro Bild. Tag und Nacht werden automatisch erkannt.
            </p>
            <details>
              <summary>
                Aufnahmegruppe und gemeinsamer Füllstand (optional)
              </summary>
              <Field label="Aufnahmegruppe">
                <input
                  name="series"
                  list={`series-${camera.id}`}
                  maxLength={100}
                  placeholder={`Kamera ${new Date().toISOString().slice(0, 10)}`}
                />
              </Field>
              <Field label="Tatsächlicher Füllstand für alle (%)">
                <input
                  name="fill"
                  type="number"
                  min={0}
                  max={100}
                  step="0.1"
                  placeholder="Sonst danach einzeln bestätigen"
                />
              </Field>
            </details>
            <Button disabled={busy || !files.length} type="submit">
              {busy ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Upload size={16} />
              )}
              Bilder hinzufügen
            </Button>
            {progress && <p role="status">{progress}</p>}
          </form>
        </PanelDialog>
      )}
    </>
  );
}

export default function Training({
  admin,
  containers,
  selected,
  onSelect,
  onChanged,
}: {
  admin: boolean;
  containers: Container[];
  selected: string;
  onSelect: (id: string) => void;
  onChanged: () => Promise<void>;
}) {
  const current = containers.find((c) => c.id === selected) || containers[0];
  if (!current)
    return (
      <section className="panel learning-empty">
        <GraduationCap size={25} />
        <p>Zuerst einen Behälter anlegen.</p>
      </section>
    );
  return (
    <>
      <div className="training-selection compact-training-selection">
        <div className="training-container-tabs">
          <ContainerTabs containers={containers} selected={current.id} onSelect={onSelect}
            includeAll={false} panelId="training-content" idPrefix="training-tab" />
        </div>
        <label className="training-container-select">
          Behälter
          <select value={current.id} onChange={(e) => onSelect(e.target.value)}>
            {containers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <span>Ein Lernstand für Tag und Nacht</span>
      </div>
      <div id="training-content" role="tabpanel" aria-label={`Training · ${current.name}`}>
        <TrainingCamera
          admin={admin}
          key={current.camera.id}
          container={current}
          onChanged={onChanged}
        />
      </div>
    </>
  );
}
