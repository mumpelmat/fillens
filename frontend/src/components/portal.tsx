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
  Activity,
  Bell,
  ArrowDownRight,
  ArrowUpRight,
  Camera as CameraIcon,
  Check,
  CircleHelp,
  Database,
  Droplets,
  Gauge,
  GraduationCap,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Menu,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Timer,
  Trash2,
  Users,
  Warehouse,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import Training from "@/components/training";
import FTPTest from "@/components/ftp-test";
import PasswordRecovery from "@/components/password-recovery";
import { ContainerTabs, ContainerSettings, LevelCorrection, MeasurementKpis, measurementView, useDetailHistory, useOverviewHistories } from "@/components/dashboard";
import {
  RateHistory,
  Events,
  ContainerRules,
  SMTPSettings,
  Notifications,
} from "@/components/operations";
import {
  api,
  basePath,
  date,
  number,
  unit,
  write,
  type ContainerEvent,
  type Container,
  type FTPConnection,
  type Measurement,
  type Site,
  type Status,
  type User,
} from "@/lib/api";

const SOURCE_URL = "https://github.com/mumpelmat/fillens";

type Page =
  | "dashboard"
  | "containers"
  | "training"
  | "users"
  | "system"
  | "notifications";
type ModalState =
  | { kind: "container"; item?: Container }
  | { kind: "camera"; item: Container }
  | { kind: "ftp"; item?: FTPConnection }
  | { kind: "user" }
  | { kind: "password"; item: User };
const titles: Record<Page, string> = {
  notifications: "Benachrichtigungen",
  dashboard: "Dashboard",
  containers: "Anlagen & Behälter",
  users: "Benutzerverwaltung",
  system: "System & Verbindungen",
  training: "Bilder bestätigen & lernen",
};
const navigation = [
  { id: "notifications" as Page, label: "Meldungen", icon: Bell },
  { id: "dashboard" as Page, label: "Dashboard", icon: LayoutDashboard },
  { id: "containers" as Page, label: "Anlagen", icon: Warehouse },
  {
    id: "training" as Page,
    label: "Training",
    icon: GraduationCap,
  },
  { id: "users" as Page, label: "Benutzer", icon: Users, admin: true },
  { id: "system" as Page, label: "System", icon: Settings2, admin: true },
];
const periodOptions = [
  { id: "24h", name: "24 Stunden" },
  { id: "7d", name: "7 Tage" },
  { id: "30d", name: "30 Tage" },
  { id: "3m", name: "3 Monate" },
  { id: "1y", name: "1 Jahr" },
  { id: "all", name: "Gesamt" },
];

function Logo() {
  return (
    <div className="brand">
      <span className="brand-icon">
        <Droplets size={23} />
      </span>
      <span>
        fillens<span className="brand-dot">.</span>
      </span>
    </div>
  );
}
function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "green" | "amber" | "neutral";
}) {
  return (
    <span className={`badge ${tone}`}>
      <span />
      {children}
    </span>
  );
}
const workerPhases: Record<string, string> = {
  cycle: "Verarbeitet Durchlauf",
  rates: "Berechnet Raten",
  email: "Versendet E-Mails",
  training: "Lernt aus Bildern",
  training_collect: "Sammelt Trainingsbilder",
  analysis: "Analysiert Bild",
  connect: "Verbindet",
  discover: "Erkennt Ordner",
  scan: "Sucht Bilder",
  control: "Kontrollscan",
  download: "Lädt Bild",
  store: "Speichert Bild",
  cleanup: "Bereinigt Puffer",
};
function WorkerIndicator({ status }: { status: Status | null }) {
  const busy = !!status?.worker_busy && status.worker === "ok";
  const label =
    status?.worker === "ok" && status.manual_run_pending
      ? "Start angefordert"
      : busy
        ? workerPhases[status.worker_phase] || "Arbeitet"
        : status?.worker === "ok"
          ? "Bereit"
          : "Nicht erreichbar";
  return (
    <span
      className={`worker-indicator ${busy ? "busy" : ""}`}
      role="status"
      title={`Import-Worker: ${label}`}
    >
      {busy ? (
        <LoaderCircle size={15} className="spin" />
      ) : (
        <Activity size={15} />
      )}
      <span>Worker · {label}</span>
    </span>
  );
}

function Empty({
  icon: Icon = CameraIcon,
  title,
  children,
  action,
}: {
  icon?: typeof CameraIcon;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={25} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="error-notice" role="alert">
      {message}
    </div>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

function CameraPreview({
  image,
  name,
  timezone,
  onRefresh,
}: {
  image: NonNullable<Container["current_image"]>;
  name: string;
  timezone: string;
  onRefresh: () => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="camera-image">
      {failed ? (
        <div className="image-error" role="status">
          <CameraIcon size={23} />
          <p>Das Bild ist nicht mehr verfügbar.</p>
          <Button variant="outline" size="sm" onClick={onRefresh}>
            Ansicht aktualisieren
          </Button>
        </div>
      ) : (
        <img
          src={`${basePath}/api/images/${image.id}/content`}
          alt={`Aktuelles Bild: ${name}`}
          onError={() => setFailed(true)}
        />
      )}
      {!failed && (
        <span className="image-stamp">
          <CameraIcon size={13} />
          {date(image.captured_at, timezone)}
        </span>
      )}
    </div>
  );
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [recovering, setRecovering] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      onLogin(
        await api<User>(
          "/auth/login",
          write({ email: data.get("email"), password: data.get("password") }),
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <section className="login-intro">
        <Logo />
        <div>
          <span className="eyebrow light">WENIGER SCHÄTZEN. MEHR WISSEN.</span>
          <h1>
            Alles im Blick.
            <br />
            Bis zum letzten
            <br />
            <em>Prozent.</em>
          </h1>
          <p>
            Ein klarer Blick auf Ihre Behälter.
            <br />
            Aktuelle Bilder, verlässliche Messwerte
            <br />
            und Entwicklungen über die Zeit.
          </p>
        </div>
        <span className="login-foot">
          <ShieldCheck size={16} /> Lokal betrieben. Daten unter Ihrer
          Kontrolle.
          <a
            className="source-link"
            href={SOURCE_URL}
            target="_blank"
            rel="noreferrer"
          >
            Quellcode (AGPL-3.0)
          </a>
        </span>
      </section>
      <section className="login-form">
        {recovering ? <PasswordRecovery onBack={(message) => { setRecovering(false); setNotice(message || ""); setError(""); }} /> : <form onSubmit={submit}>
          <span className="eyebrow">FÜLLSTANDSPORTAL</span>
          <h2>Willkommen zurück</h2>
          <p>Melden Sie sich mit Ihrem Benutzerkonto an.</p>
          {notice && <div className="success-notice" role="status">{notice}</div>}
          {error && <ErrorNotice message={error} />}
          <Field label="E-Mail">
            <input
              name="email"
              type="email"
              autoComplete="username"
              required
              placeholder="name@unternehmen.de"
            />
          </Field>
          <Field label="Passwort">
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
            />
          </Field>
          <Button disabled={busy} type="submit" className="w-full">
            {busy ? <LoaderCircle className="spin" size={16} /> : null}Anmelden{" "}
            <ArrowUpRight size={16} />
          </Button>
          <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={() => { setRecovering(true); setNotice(""); }}>Passwort vergessen?</Button>
          <small>
            Das erste Administratorkonto wird bei der Installation angelegt.
          </small>
        </form>}
      </section>
    </main>
  );
}

function Modal({
  title,
  children,
  onClose,
  onSave,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  onSave: (data: FormData) => Promise<void>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = ref.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await onSave(new FormData(event.currentTarget));
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={ref}
      onCancel={(event) => {
        if (busy) event.preventDefault();
        else onClose();
      }}
      className="modal"
    >
      <form onSubmit={submit}>
        <div className="modal-head">
          <h2>{title}</h2>
          <Button
            aria-label="Schließen"
            variant="ghost"
            size="icon"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </Button>
        </div>
        <div className="modal-body">
          {error && <ErrorNotice message={error} />} {children}
        </div>
        <div className="modal-actions">
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onClose}
          >
            Abbrechen
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Check size={16} />
            )}
            Speichern
          </Button>
        </div>
      </form>
    </dialog>
  );
}

function ContainerForm({ item, sites }: { item?: Container; sites: Site[] }) {
  const [site, setSite] = useState(item?.site_id || sites[0]?.id || "new");
  return (
    <>
      <Field label="Name">
        <input
          name="name"
          required
          maxLength={120}
          defaultValue={item?.name}
          placeholder="z. B. Papierkorb Werkstatt"
        />
      </Field>
      <Field label="Standort">
        <select
          name="site_id"
          value={site}
          onChange={(e) => setSite(e.target.value)}
        >
          {sites.map((s) => (
            <option value={s.id} key={s.id}>
              {s.name}
            </option>
          ))}
          <option value="new">+ Neuen Standort anlegen</option>
        </select>
      </Field>
      {site === "new" && (
        <div className="inset">
          <Field label="Name des Standorts">
            <input
              name="site_name"
              required
              maxLength={120}
              placeholder="z. B. Hauptgebäude"
            />
          </Field>
          <Field label="Zeitzone">
            <input name="site_timezone" defaultValue="Europe/Berlin" required />
          </Field>
        </div>
      )}
      <div className="form-grid">
        <Field label="Kapazität">
          <input
            name="capacity"
            type="number"
            min="0.001"
            max="1000000000"
            step="any"
            defaultValue={item?.capacity || 30}
            required
          />
        </Field>
        <Field label="Einheit">
          <select
            name="capacity_unit"
            defaultValue={item?.capacity_unit || "liter"}
          >
            <option value="liter">Liter (l)</option>
            <option value="m3">Kubikmeter (m³)</option>
          </select>
        </Field>
      </div>
      <Field label="Betriebsmodus">
        <select name="display_mode" defaultValue={item?.display_mode || "fill"}>
          <option value="fill">Füllen</option>
          <option value="consumption">Verbrauch</option>
        </select>
      </Field>
      <div className="form-grid">
        <Field label="Zeitbasis der Rate">
          <select
            name="rate_time_unit"
            defaultValue={item?.rate_time_unit || "hour"}
          >
            <option value="minute">Pro Minute</option>
            <option value="hour">Pro Stunde</option>
            <option value="day">Pro Tag</option>
          </select>
        </Field>
        <Field
          label="Glättungsfenster (Minuten)"
          hint="1 Minute bis 7 Tage. Größere Messlücken unterbrechen die Rate."
        >
          <input
            name="smoothing_minutes"
            type="number"
            min={1}
            max={10080}
            required
            defaultValue={item?.smoothing_minutes || 60}
          />
        </Field>
      </div>
      <Field
        label="Automatische Leerung / Nachfüllung ab (Prozentpunkte)"
        hint="Gegenbewegungen ab dieser Größe beginnen einen neuen Betriebszyklus."
      >
        <input
          name="event_threshold"
          type="number"
          min={0.01}
          max={100}
          step="any"
          required
          defaultValue={item?.event_threshold || 20}
        />
      </Field>
      <Field label="Beschreibung">
        <textarea
          name="description"
          maxLength={2000}
          rows={2}
          defaultValue={item?.description}
        />
      </Field>
      <label className="check-field">
        <input
          type="checkbox"
          name="active"
          defaultChecked={item?.active ?? true}
        />
        Behälter aktiv
      </label>
    </>
  );
}

function FTPForm({ item }: { item?: FTPConnection }) {
  return (
    <>
      <Field label="Verbindungsname">
        <input
          name="name"
          defaultValue={item?.name}
          required
          maxLength={120}
          placeholder="Kamera-FTP"
        />
      </Field>
      <div className="form-grid">
        <Field label="Protokoll">
          <select name="protocol" defaultValue={item?.protocol || "ftp"}>
            <option value="ftp">FTP</option>
            <option value="ftps">FTPS (explizites TLS)</option>
          </select>
        </Field>
        <Field label="Port">
          <input
            name="port"
            type="number"
            min={1}
            max={65535}
            defaultValue={item?.port || 21}
            required
          />
        </Field>
      </div>
      <Field label="Server">
        <input
          name="host"
          defaultValue={item?.host}
          placeholder="Hostname oder IP-Adresse"
          required
          maxLength={253}
        />
      </Field>
      <Field label="Benutzername">
        <input
          name="username"
          autoComplete="off"
          defaultValue={item?.username}
          required
          maxLength={200}
        />
      </Field>
      <Field
        label="Passwort"
        hint={
          item
            ? "Leer lassen, um das vorhandene Passwort zu behalten."
            : "Wird verschlüsselt gespeichert."
        }
      >
        <input
          name="password"
          type="password"
          autoComplete="new-password"
          required={!item}
          maxLength={1000}
        />
      </Field>
      <Field
        label="FTP-Root"
        hint="Unterhalb dieses Verzeichnisses wird rekursiv nach Bildern gesucht."
      >
        <input
          name="root_path"
          defaultValue={item?.root_path || "/"}
          required
        />
      </Field>
      <FTPTest connection={item} />
    </>
  );
}

function CameraForm({
  item,
  connections,
}: {
  item: Container;
  connections: FTPConnection[];
}) {
  const [scanMode, setScanMode] = useState(item.camera.scan_mode || "auto");
  return (
    <>
      <Field label="Kameraname">
        <input
          name="name"
          defaultValue={item.camera.name}
          required
          maxLength={120}
        />
      </Field>
      <Field label="FTP-Verbindung">
        <select
          name="ftp_connection_id"
          defaultValue={item.camera.ftp_connection_id || ""}
        >
          <option value="">Keine Verbindung</option>
          {connections.map((c) => (
            <option value={c.id} key={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>
      {connections.length === 0 && (
        <p className="hint">
          Legen Sie zuerst unter System eine FTP-Verbindung an.
        </p>
      )}
      <Field
        label="Kamera-Unterverzeichnis"
        hint="Relativ zum FTP-Root; leer bedeutet den gesamten Root durchsuchen."
      >
        <input
          name="ftp_root"
          defaultValue={item.camera.ftp_root}
          placeholder="z. B. kamera1/bilder"
        />
      </Field>
      <FTPTest connections={connections} />
      <Field
        label="Zeitzone der Kamera"
        hint="Für EXIF-Zeitstempel und die Auswahl aktueller Tagesordner. FTP-Zeitstempel gelten als UTC."
      >
        <input name="timezone" defaultValue={item.camera.timezone} required />
      </Field>
      <Field
        label="Ordnerstruktur"
        hint="Erkennt Jahres-/Monats-/Tagesordner auch dann, wenn der Root bereits auf Jahr oder Monat zeigt."
      >
        <select
          name="scan_mode"
          value={scanMode}
          onChange={(event) =>
            setScanMode(event.target.value as typeof scanMode)
          }
        >
          <option value="auto">Automatisch erkennen</option>
          <option value="date">Datumsmuster vorgeben</option>
          <option value="recursive">Beliebige Struktur · rekursiv</option>
        </select>
      </Field>
      {scanMode === "date" ? (
        <Field
          label="Datumsmuster"
          hint="Relativ zum Kamera-Verzeichnis: YYYY/MM/TT; bei Root /2026 nur MM/TT, bei /2026/10 nur TT. DD ist ebenfalls erlaubt."
        >
          <input
            name="date_pattern"
            defaultValue={item.camera.date_pattern || "YYYY/MM/TT"}
            required
            maxLength={500}
          />
        </Field>
      ) : (
        <input
          type="hidden"
          name="date_pattern"
          value={item.camera.date_pattern || "YYYY/MM/TT"}
        />
      )}
      {scanMode !== "recursive" && (
        <>
          <div className="form-grid">
            <Field
              label="Tagesordner prüfen"
              hint="Heute und die vorherigen Tage."
            >
              <input
                name="recent_days"
                type="number"
                min={1}
                max={31}
                defaultValue={item.camera.recent_days || 3}
                required
              />
            </Field>
            <Field
              label="Kontrollscan alle (Stunden)"
              hint="Geänderte Ordner und Stichproben."
            >
              <input
                name="full_scan_hours"
                type="number"
                min={1}
                max={720}
                defaultValue={item.camera.full_scan_hours || 24}
                required
              />
            </Field>
          </div>
          {item.camera.detected_date_pattern && (
            <p className="hint">
              Erkannte Struktur: {item.camera.detected_date_pattern}
            </p>
          )}
        </>
      )}
      <Field label="Bilddrehung">
        <select name="image_rotation" defaultValue={item.camera.image_rotation}>
          {[0, 90, 180, 270].map((n) => (
            <option key={n} value={n}>
              {n}° im Uhrzeigersinn
            </option>
          ))}
        </select>
      </Field>
      <label className="check-field">
        <input
          type="checkbox"
          name="image_flip"
          defaultChecked={item.camera.image_flip}
        />
        Bild horizontal spiegeln
      </label>
      <label className="check-field">
        <input
          type="checkbox"
          name="active"
          defaultChecked={item.camera.active}
        />
        Kamera aktiv
      </label>
      <p className="hint">
        Ausrichtung wird auf neu importierte Bilder angewendet.
      </p>
    </>
  );
}

function History({
  events,
  container,
  period,
  setPeriod,
  items,
  total,
  sampled,
  loading,
  compact = false,
  showPeriod = true,
}: {
  compact?: boolean;
  showPeriod?: boolean;
  events: ContainerEvent[];
  container: Container;
  period: string;
  setPeriod: (p: string) => void;
  items: Measurement[];
  total: number;
  sampled: boolean;
  loading: boolean;
}) {
  return (
    <section
      className={`panel history-panel ${compact ? "overview-history" : ""}`}
    >
      <div className="panel-head">
        <div>
          <h2>Füllstand im Verlauf</h2>
          <p>Gespeicherte Messwerte · {container.camera.timezone}</p>
        </div>
        {showPeriod && <select
          className="period-select"
          aria-label="Zeitraum"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
        >
          {periodOptions.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>}
      </div>
      {loading ? (
        <div className="chart-loading">
          <LoaderCircle className="spin" />
          Messwerte werden geladen …
        </div>
      ) : items.length === 0 ? (
        <Empty
          icon={Activity}
          title="Der Verlauf beginnt mit der ersten Messung"
        >
          Im gewählten Zeitraum liegen noch keine Messwerte vor.
        </Empty>
      ) : (
        <>
          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={items.map((m) => ({
                  ...m,
                  time: new Date(m.measured_at).getTime(),
                }))}
                margin={{ top: 12, right: 12, left: -15, bottom: 0 }}
              >
                <defs>
                  <linearGradient
                    id={`fill-gradient-${container.id}`}
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="1"
                  >
                    <stop offset="0%" stopColor="#237c60" stopOpacity={0.2} />
                    <stop
                      offset="100%"
                      stopColor="#237c60"
                      stopOpacity={0.01}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 5"
                  vertical={false}
                  stroke="#e8e9e4"
                />
                <XAxis
                  dataKey="time"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  axisLine={false}
                  tickLine={false}
                  minTickGap={45}
                  tick={{ fontSize: 11, fill: "#858b82" }}
                  tickFormatter={(value) =>
                    new Intl.DateTimeFormat("de-DE", {
                      timeZone: container.camera.timezone,
                      ...(period === "24h"
                        ? { hour: "2-digit", minute: "2-digit" }
                        : { day: "2-digit", month: "2-digit" }),
                    }).format(new Date(value))
                  }
                />
                <YAxis
                  domain={[0, 100]}
                  ticks={[0, 25, 50, 75, 100]}
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 11, fill: "#858b82" }}
                  tickFormatter={(value) => `${value}%`}
                />
                <Tooltip
                  labelFormatter={(value) =>
                    date(
                      new Date(Number(value)).toISOString(),
                      container.camera.timezone,
                    )
                  }
                  formatter={(value) => [
                    `${number(Number(value))} %`,
                    "Füllstand",
                  ]}
                  contentStyle={{
                    borderRadius: 10,
                    border: "1px solid #e1e5df",
                    fontSize: 12,
                  }}
                />
                {events
                  .filter(
                    (e) =>
                      items.length &&
                      new Date(e.occurred_at) >=
                        new Date(items[0].measured_at) &&
                      new Date(e.occurred_at) <=
                        new Date(items[items.length - 1].measured_at),
                  )
                  .map((e) => (
                    <ReferenceLine
                      key={e.id}
                      x={new Date(e.occurred_at).getTime()}
                      stroke="#b07831"
                      strokeDasharray="4 4"
                      label={{
                        value:
                          e.kind === "emptying" ? "Leerung" : "Nachfüllung",
                        position: "insideTop",
                        fontSize: 10,
                      }}
                    />
                  ))}
                <Area
                  name="Füllstand"
                  type="linear"
                  dataKey="effective_fill_level"
                  stroke="#237c60"
                  strokeWidth={2.5}
                  fill={`url(#fill-gradient-${container.id})`}
                  dot={
                    items.length < 20
                      ? { r: 3, strokeWidth: 2, fill: "#fff" }
                      : false
                  }
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="chart-footer">
            <span>
              <i /> Effektiver Füllstand
            </span>
            <span>
              {number(total, 0)} Messwerte
              {sampled ? ` · ${items.length} gleichmäßig verteilte Punkte` : ""}
            </span>
          </div>
        </>
      )}
    </section>
  );
}

function ContainerOverviewCard({ container, site, admin, data, period, setPeriod, onOpen, onSettings, onRefresh }: {
  container: Container; site: string; admin: boolean; data: ReturnType<typeof useDetailHistory>;
  period: string; setPeriod: (period: string) => void; onOpen: () => void;
  onSettings: (action: "container" | "camera" | "rules") => void; onRefresh: () => void;
}) {
  const view = measurementView(container);
  const timezone = container.camera.timezone;
  return <article className="panel fleet-card" aria-label={container.name}>
    <div className="panel-head">
      <div><h2>{container.name}</h2><p>{site} · {container.camera.name}</p></div>
      {admin && <ContainerSettings name={container.name} onAction={onSettings} />}
    </div>
    <div className="fleet-status">
      <Badge tone={!container.active ? "neutral" : container.alert_severity ? "amber" : "green"}>
        {!container.active ? "Behälter inaktiv" : container.alert_severity === 2 ? "Warnung" : container.alert_severity ? "Hinweis" : "Behälter aktiv"}
      </Badge>
      <span>{!container.camera.active ? "Kamera inaktiv" : container.camera.analysis_enabled ? "Lokale Auswertung" : "Manueller Betrieb"}</span>
      {container.active && container.alert_severity > 0 && <span>Aktive Meldung zum übernommenen Füllstand</span>}
    </div>
    {container.current_image ? <CameraPreview key={container.current_image.id} image={container.current_image}
      name={container.name} timezone={timezone} onRefresh={onRefresh} />
      : <Empty icon={CameraIcon} title="Noch kein Kamerabild">Für diesen Behälter ist noch kein Bild verfügbar.</Empty>}
    <LevelCorrection key={container.current_image?.id || "none"} container={container} admin={admin} onSaved={onRefresh} />
    <div className="fleet-metrics"><MeasurementKpis container={container} /></div>
    <div className="fleet-times">
      <span>Bild: {container.current_image ? date(container.current_image.captured_at, timezone) : "Noch keines vorhanden"}</span>
      <span>Letzte Messung: {view.measured ? `${number(view.measured.effective_fill_level)} % · ${date(view.measured.measured_at, timezone)}` : "Noch keine übernommen"}</span>
      {view.estimated && <strong>Aktuelle Anzeige geschätzt · noch nicht als Messwert übernommen</strong>}
    </div>
    {data.error ? <div className="fleet-history-error"><ErrorNotice message={data.error} /></div> : <History container={container}
      period={period} setPeriod={setPeriod} items={data.items} total={data.total_count} sampled={data.sampled}
      loading={data.loading} events={[]} compact showPeriod={false} />}
    <div className="fleet-actions"><Button variant="outline" onClick={onOpen}>Messungen ansehen <ArrowUpRight size={15} /></Button></div>
  </article>;
}

export default function Portal({
  initialPage = "dashboard",
}: {
  initialPage?: Page;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [boot, setBoot] = useState(true);
  const [page, setPage] = useState<Page>(initialPage);
  const [menu, setMenu] = useState(false);
  const [containers, setContainers] = useState<Container[]>([]);
  const [containersLoaded, setContainersLoaded] = useState(false);
  const [sites, setSites] = useState<Site[]>([]);
  const [selected, setSelected] = useState("");
  const [overview, setOverview] = useState(true);
  const [focusRules, setFocusRules] = useState(false);
  const [connections, setConnections] = useState<FTPConnection[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [modal, setModal] = useState<ModalState | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [requestingRun, setRequestingRun] = useState(false);
  const [period, setPeriod] = useState("7d");
  const [historyRevision, setHistoryRevision] = useState(0);
  const admin = user?.role === "admin";
  const trainingContainers = admin ? containers : containers.filter((c) => c.camera.learning_enabled);
  const canTrain = admin || trainingContainers.length > 0;
  const current = containers.find((c) => c.id === selected) || containers[0];
  const detail = useDetailHistory(page === "dashboard" && !overview ? current?.id : undefined, period, historyRevision);
  const { items: history, events, total_count: total, sampled, loading: historyLoading } = detail;
  const overviewHistory = useOverviewHistories(containers, period, historyRevision, page === "dashboard" && overview);
  const siteName = (id: string) => sites.find((site) => site.id === id)?.name || "";
  const openContainer = (id: string) => { setSelected(id); setOverview(false); setPage("dashboard"); };
  const openSettings = (item: Container, action: "container" | "camera" | "rules") => {
    setSelected(item.id);
    if (action === "rules") { setPage("containers"); setFocusRules(true); }
    else setModal({ kind: action, item });
  };
  useEffect(() => {
    if (page === "containers" && focusRules) {
      const element = document.getElementById("container-rules");
      element?.scrollIntoView({ block: "start" }); element?.focus({ preventScroll: true });
      setFocusRules(false);
    }
  }, [page, focusRules]);
  useEffect(() => {
    if (user?.role === "user" && (["users", "system"].includes(page) || page === "training" && containersLoaded && !canTrain))
      setPage("dashboard");
  }, [user, page, containersLoaded, canTrain]);
  useEffect(() => {
    api<User>("/auth/me")
      .then(setUser)
      .catch((e) => {
        if (e.status !== 401) setError(e.message);
      })
      .finally(() => setBoot(false));
    const expire = () => {
      setUser(null);
      setModal(null);
      setPage("dashboard");
    };
    window.addEventListener("fillens:expired", expire);
    return () => window.removeEventListener("fillens:expired", expire);
  }, []);
  const refresh = useCallback(async () => {
    if (!user) return;
    setRefreshing(true);
    try {
      const [cs, ss] = await Promise.all([
        api<Container[]>("/containers"),
        api<Site[]>("/sites"),
      ]);
      setContainers(cs);
      setContainersLoaded(true);
      setSites(ss);
      setSelected((prev) =>
        cs.some((c) => c.id === prev) ? prev : cs[0]?.id || "",
      );
      if (user.role === "admin") {
        const [fs, us, st] = await Promise.all([
          api<FTPConnection[]>("/ftp-connections"),
          api<User[]>("/users"),
          api<Status>("/system/status"),
        ]);
        setConnections(fs);
        setUsers(us);
        setStatus(st);
      }
      setHistoryRevision((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [user]);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 30000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (!admin) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api<Status>("/system/status");
        if (active) setStatus(value);
      } catch {
        if (active)
          setStatus((previous) =>
            previous
              ? {
                  ...previous,
                  worker: "missing",
                  worker_busy: false,
                  worker_phase: "offline",
                }
              : null,
          );
      } finally {
        if (active) timer = setTimeout(poll, 2000);
      }
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [admin]);
  async function systemRefresh() {
    setRequestingRun(true);
    setError("");
    setSuccess("");
    try {
      const result = await api<{ status: string }>(
        "/system/worker/run",
        write({}),
      );
      setSuccess(
        result.status === "running"
          ? "Der Worker arbeitet bereits."
          : result.status === "pending"
            ? "Ein Worker-Durchlauf ist bereits angefordert."
            : "Worker-Durchlauf angefordert. Start innerhalb etwa einer Sekunde.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      await refresh();
      setRequestingRun(false);
    }
  }
  async function save(form: FormData) {
    if (!modal) return;
    const get = (key: string) => String(form.get(key) ?? "");
    if (modal.kind === "container") {
      let site_id = get("site_id");
      if (site_id === "new") {
        const site = await api<Site>(
          "/sites",
          write({ name: get("site_name"), timezone: get("site_timezone") }),
        );
        site_id = site.id;
        setSites((prev) => [...prev, site]);
      }
      const item = await api<Container>(
        `/containers${modal.item ? `/${modal.item.id}` : ""}`,
        write(
          {
            site_id,
            name: get("name"),
            description: get("description"),
            capacity: Number(get("capacity")),
            capacity_unit: get("capacity_unit"),
            display_mode: get("display_mode"),
            rate_time_unit: get("rate_time_unit"),
            smoothing_minutes: Number(get("smoothing_minutes")),
            event_threshold: Number(get("event_threshold")),
            active: form.has("active"),
          },
          modal.item ? "PUT" : "POST",
        ),
      );
      setSelected(item.id);
    } else if (modal.kind === "ftp") {
      const data = {
        name: get("name"),
        host: get("host"),
        port: Number(get("port")),
        username: get("username"),
        password: get("password") || (modal.item ? null : ""),
        protocol: get("protocol"),
        root_path: get("root_path"),
      };
      await api(
        `/ftp-connections${modal.item ? `/${modal.item.id}` : ""}`,
        write(data, modal.item ? "PUT" : "POST"),
      );
    } else if (modal.kind === "camera") {
      await api(
        `/containers/${modal.item.id}/camera`,
        write(
          {
            name: get("name"),
            ftp_connection_id: get("ftp_connection_id") || null,
            ftp_root: get("ftp_root"),
            timezone: get("timezone"),
            scan_mode: get("scan_mode"),
            date_pattern: get("date_pattern"),
            recent_days: Number(get("recent_days") || 3),
            full_scan_hours: Number(get("full_scan_hours") || 24),
            image_rotation: Number(get("image_rotation")),
            image_flip: form.has("image_flip"),
            active: form.has("active"),
          },
          "PUT",
        ),
      );
    } else if (modal.kind === "password") {
      if (get("password") !== get("password_repeat"))
        throw new Error("Die Passwörter stimmen nicht überein.");
      await api(
        `/users/${modal.item.id}/password`,
        write({ password: get("password") }, "PUT"),
      );
    } else if (modal.kind === "user") {
      await api(
        "/users",
        write({
          email: get("email"),
          password: get("password"),
          role: get("role"),
        }),
      );
    }
    setSuccess("Änderungen gespeichert.");
    setError("");
    await refresh();
  }
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      setUser(null);
      setPage("dashboard");
      setOverview(true);
      setModal(null);
      setError("");
      setSuccess("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (boot)
    return (
      <div className="boot">
        <Logo />
        <LoaderCircle className="spin" />
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={(value) => {
          setContainersLoaded(false);
          setContainers([]);
          setUser(value);
          setOverview(true);
          setPage(
            value.role === "admin" ||
              !["users", "system"].includes(initialPage)
              ? initialPage
              : "dashboard",
          );
          setError("");
        }}
      />
    );
  const measured = current?.current_measurement;
  const currentUnit = unit(current?.capacity_unit || "liter");
  const timezone = current?.camera.timezone || "Europe/Berlin";
  const imageAnalysis = current?.current_image?.analysis;
  const view = current ? measurementView(current) : null;
  const estimated = view?.estimated || false;
  const levelTone = view?.tone || "unknown";
  return (
    <div
      className={`app-shell ${page === "dashboard" ? "dashboard-shell" : page === "training" ? "learning-shell" : ""}`}
    >
      <aside className={`sidebar ${menu ? "is-open" : ""}`}>
        <Logo />
        <span className="sidebar-label">ARBEITSPLATZ</span>
        <nav>
          {navigation
            .filter((n) => (!n.admin || admin) && (n.id !== "training" || canTrain))
            .map((n) => (
              <button
                key={n.id}
                className={page === n.id ? "active" : ""}
                onClick={() => {
                  setPage(n.id);
                  if (n.id === "dashboard") setOverview(true);
                  setMenu(false);
                }}
              >
                <n.icon size={18} />
                {n.label}
                {page === n.id && <span className="nav-dot" />}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="account">
            <span className="avatar">{user.email[0].toUpperCase()}</span>
            <div>
              <strong title={user.email}>{user.email}</strong>
              <small>{admin ? "Administrator" : "Benutzer"}</small>
            </div>
            <button aria-label="Abmelden" title="Abmelden" onClick={logout}>
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      {menu && (
        <button
          className="sidebar-backdrop"
          aria-label="Menü schließen"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu"
              aria-label="Menü öffnen"
              onClick={() => setMenu(true)}
            >
              <Menu size={22} />
            </button>
            <span>Portal</span>
            <span className="slash">/</span>
            <strong>{titles[page]}</strong>
          </div>
          <div className="topbar-right">
            {admin && <WorkerIndicator status={status} />}
            <span className="header-avatar">{user.email[0].toUpperCase()}</span>
          </div>
        </header>
        <main
          className={`main ${page === "dashboard" ? "dashboard-main" : page === "training" ? "learning-main" : ""}`}
        >
          <div className="page-heading">
            <div>
              <span className="eyebrow">FÜLLSTANDSPORTAL</span>
              <h1>{page === "dashboard" && !overview && current ? `Messungen · ${current.name}` : titles[page]}</h1>
              <p>
                {page === "dashboard"
                  ? "Aktuelle Füllstände. Klare Entscheidungen."
                  : page === "containers"
                    ? "Standorte, Kapazitäten und Kameras verwalten."
                      : page === "users"
                        ? "Zugriff gezielt vergeben und verwalten."
                        : page === "training"
                          ? "Bilder bestätigen. Den Rest übernimmt das System."
                          : "Verbindungen und Importstatus im Blick behalten."}
              </p>
            </div>
            <div className="heading-actions">
              <Button
                variant="outline"
                size="icon"
                aria-label={
                  page === "system"
                    ? "Worker-Durchlauf starten und aktualisieren"
                    : "Daten aktualisieren"
                }
                title={
                  page === "system"
                    ? status?.manual_run_pending
                      ? "Worker-Start angefordert"
                      : status?.worker_busy
                        ? "Worker arbeitet bereits"
                        : "Worker-Durchlauf starten und aktualisieren"
                    : "Aktualisieren"
                }
                onClick={() =>
                  void (page === "system" ? systemRefresh() : refresh())
                }
                disabled={
                  refreshing ||
                  requestingRun ||
                  (page === "system" &&
                    !!(status?.worker_busy || status?.manual_run_pending))
                }
              >
                <RefreshCw
                  size={16}
                  className={
                    refreshing ||
                    requestingRun ||
                    (page === "system" &&
                      (status?.worker_busy || status?.manual_run_pending))
                      ? "spin"
                      : ""
                  }
                />
              </Button>
              {admin && (page === "containers" || !containers.length) && (
                <Button onClick={() => setModal({ kind: "container" })}>
                  <Plus size={16} />
                  Behälter anlegen
                </Button>
              )}
              {admin && page === "users" && (
                <Button onClick={() => setModal({ kind: "user" })}>
                  <Plus size={16} />
                  Benutzer anlegen
                </Button>
              )}
              {admin && page === "system" && (
                <Button onClick={() => setModal({ kind: "ftp" })}>
                  <Plus size={16} />
                  FTP-Verbindung
                </Button>
              )}
            </div>
          </div>
          {error && (
            <div className="dismissible">
              <ErrorNotice message={error} />
              <button
                aria-label="Fehler schließen"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {success && (
            <div className="success-notice" role="status">
              <Check size={16} />
              {success}
              <button
                aria-label="Meldung schließen"
                onClick={() => setSuccess("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {page === "dashboard" && containers.length > 0 && <>
            <ContainerTabs containers={containers} selected={overview ? "all" : current.id} siteName={siteName}
              onSelect={(id) => { if (id === "all") setOverview(true); else openContainer(id); }} />
            {overview && <div id="dashboard-content" role="tabpanel" aria-labelledby="tab-all">
              <div className="overview-toolbar"><span>{containers.length} Behälter</span>
                <label>Zeitraum für alle Verläufe <select className="period-select" value={period} onChange={(event) => setPeriod(event.target.value)}>
                  {periodOptions.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
                </select></label>
              </div>
              <div className="fleet-grid">
                {containers.map((container) => <ContainerOverviewCard key={container.id}
                  container={container} site={siteName(container.site_id)} admin={admin}
                  data={overviewHistory(container.id)} period={period} setPeriod={setPeriod}
                  onOpen={() => openContainer(container.id)} onSettings={(action) => openSettings(container, action)}
                  onRefresh={() => void refresh()} />)}
              </div>
            </div>}
          </>}
          {page === "dashboard" && (!current || !overview) &&
            (!current ? (
              <section className="panel">
                <Empty
                  icon={Warehouse}
                  title={
                    refreshing
                      ? "Anlagen werden geladen …"
                      : "Ein guter Überblick beginnt hier"
                  }
                  action={
                    admin ? (
                      <Button onClick={() => setModal({ kind: "container" })}>
                        <Plus size={16} />
                        Ersten Behälter anlegen
                      </Button>
                    ) : undefined
                  }
                >
                  {admin
                    ? "Legen Sie einen Standort und einen Behälter an. Verbinden Sie anschließend Ihre Kamera über FTP."
                    : "Es sind noch keine Behälter eingerichtet. Ihr Administrator kann die Einrichtung vornehmen."}
                </Empty>
              </section>
            ) : (
              <div id="dashboard-content" role="tabpanel" aria-labelledby={`tab-${current.id}`}>
                <div className="selection-bar">
                  <div>
                    <span className="container-symbol">
                      <Trash2 size={20} />
                    </span>
                    <div>
                      <strong>{current.name}</strong>
                    </div>
                    <span className="selection-divider" />
                    <span className="site-name">
                      {sites.find((s) => s.id === current.site_id)?.name}
                      <small>
                        {number(current.capacity)} {currentUnit} Kapazität
                      </small>
                    </span>
                  </div>
                  {admin && <ContainerSettings name={current.name} onAction={(action) => openSettings(current, action)} />}
                  <Badge
                    tone={
                      !current.active
                        ? "neutral"
                        : current.current_image
                          ? "green"
                          : "amber"
                    }
                  >
                    {!current.active
                      ? "Inaktiv"
                      : current.current_image
                        ? "Bild vorhanden"
                        : "Wartet auf Bild"}
                  </Badge>
                </div>
                {page === "dashboard" && (
                  <>
                    <MeasurementKpis container={current} />
                    <div
                      className={`overview-status level-tone-${levelTone}`}
                      role="status"
                    >
                      <span className="overview-mode">
                        {current.display_mode === "fill"
                          ? "Füllen"
                          : "Verbrauch"} · {current.camera.analysis_enabled ? "Lokale Auswertung" : "Manueller Betrieb"}
                      </span>
                      <span>
                        {estimated && current.current_image
                          ? `Neuestes Bild ${date(current.current_image.captured_at, timezone)} nicht übernommen${imageAnalysis?.confidence != null ? ` · Vertrauen ${number(imageAnalysis.confidence * 100, 0)} %` : ""}${measured ? ` · letzte Messung ${number(measured.effective_fill_level)} % (${date(measured.measured_at, timezone)})` : ""}`
                          : measured
                            ? `Messung: ${date(measured.measured_at, timezone)}`
                            : "Wartet auf erste Messung"}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setPage("notifications")}
                      >
                        {current.alert_severity
                          ? "Aktive Meldungen"
                          : "Meldungen"}
                      </Button>
                    </div>
                    <div className="dashboard-columns">
                      <section className="panel camera-panel">
                        <div className="panel-head">
                          <div>
                            <h2>Aktuelles Kamerabild</h2>
                            <p>{current.camera.name}</p>
                          </div>
                        </div>
                        {current.current_image ? (
                          <>
                            <CameraPreview
                              key={current.current_image.id}
                              image={current.current_image}
                              name={current.name}
                              timezone={timezone}
                              onRefresh={() => void refresh()}
                            />
                            <div className="image-details">
                              <span>
                                <Timer size={14} />
                                Zeitquelle:{" "}
                                {current.current_image.timestamp_source ===
                                "exif"
                                  ? "EXIF"
                                  : current.current_image.timestamp_source ===
                                      "ftp_mtime"
                                    ? "FTP"
                                    : "Importzeit"}
                              </span>
                              <span>
                                {current.current_image.image_mode === "day"
                                  ? "Tag"
                                  : current.current_image.image_mode ===
                                      "night_ir"
                                    ? "Nacht / IR"
                                    : "Modus unbekannt"}
                              </span>
                            </div>
                            <LevelCorrection
                              key={current.current_image.id}
                              container={current}
                              admin={admin}
                              onSaved={() => void refresh()}
                            />
                          </>
                        ) : (
                          <Empty title="Noch kein Kamerabild">
                            {admin
                              ? current.camera.ftp_connection_id
                                ? "Der Worker sucht automatisch nach neuen Bildern. Den Importstatus finden Sie unter System."
                                : "Verbinden Sie die Kamera über die Einstellungen mit einem FTP-Verzeichnis."
                              : "Für diesen Behälter ist noch kein Kamerabild verfügbar."}
                          </Empty>
                        )}
                      </section>
                    </div>
                    {detail.error ? <ErrorNotice message={detail.error} /> : <History
                      container={current} period={period} setPeriod={setPeriod}
                      items={history} total={total} sampled={sampled} loading={historyLoading} events={events}
                    />}
                  </>
                )}
                <RateHistory
                  container={current}
                  items={history}
                  events={events}
                  loading={historyLoading}
                />
                {history.length > 0 && (
                  <section className="panel table-panel">
                    <div className="panel-head">
                      <div>
                        <h2>Messwerte</h2>
                        <p>
                          {sampled
                            ? "Gleichmäßig verteilter Auszug aus der Historie"
                            : "Alle Messwerte im gewählten Zeitraum"}
                        </p>
                      </div>
                      <Badge>{number(total, 0)} Einträge</Badge>
                    </div>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Aufnahmezeit</th>
                            <th>Füllstand</th>
                            <th>Inhalt zum Messzeitpunkt</th>
                            <th>Änderung / h</th>
                            <th>Modus</th>
                            <th>Verfahren</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...history].reverse().map((m) => (
                            <tr key={m.id}>
                              <td>{date(m.measured_at, timezone)}</td>
                              <td>
                                <strong>
                                  {number(m.effective_fill_level)} %
                                </strong>
                              </td>
                              <td>
                                {number(m.absolute_level)}{" "}
                                {unit(m.capacity_unit)}
                              </td>
                              <td>
                                {number(m.change_rate_percent)} %
                                <small className="block">
                                  {number(m.change_rate_absolute)}{" "}
                                  {unit(m.capacity_unit)}/h
                                </small>
                              </td>
                              <td>
                                {m.image_mode === "day"
                                  ? "Tag"
                                  : m.image_mode === "night_ir"
                                    ? "Nacht / IR"
                                    : "Unbekannt"}
                              </td>
                              <td>
                                {m.analysis_method === "local_rf_v1"
                                  ? "Lokales Modell"
                                  : m.analysis_method === "manual_review_v1"
                                    ? "Manuell geprüft"
                                    : "Manuell"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                )}
                <Events
                  key={current.id}
                  container={current}
                  events={events}
                  admin={admin}
                  onChanged={refresh}
                />
                {page === "dashboard" && (
                  <details className="dashboard-measure-details">
                    <summary>Messwertdetails</summary>
                    <section className="panel measure-panel">
                      <div className="panel-head">
                        <div>
                          <h2>Letzte Messung</h2>
                          <p>Details zum übernommenen Messwert.</p>
                        </div>
                        <span className="round-icon">
                          <Gauge size={18} />
                        </span>
                      </div>
                      <div className="measurement-display">
                        <span>LETZTE MESSUNG</span>
                        <strong>
                          {number(measured?.effective_fill_level)}
                          <small>%</small>
                        </strong>
                        <p>
                          {measured
                            ? date(measured.measured_at, timezone)
                            : "Noch kein Füllstand erfasst"}
                        </p>
                      </div>
                      <div className="measurement-context">
                        <div>
                          <span>Verfahren</span>
                          <strong>
                            {measured?.analysis_method === "local_rf_v1"
                              ? "Lokales Modell"
                              : measured?.analysis_method === "manual_review_v1"
                                ? "Manuell geprüft"
                                : "Manuelle Erfassung"}
                          </strong>
                        </div>
                        <div>
                          <span>Modell-Vertrauen</span>
                          <strong>
                            {measured?.confidence != null
                              ? `${number(measured.confidence * 100)} % · heuristisch`
                              : "Nicht automatisch bewertet"}
                          </strong>
                        </div>
                      </div>
                      <p className="measurement-help">
                        <CircleHelp size={14} />
                        Der Messwert bleibt dauerhaft erhalten. Das Kamerabild
                        wird automatisch ersetzt.
                      </p>
                    </section>
                  </details>
                )}
              </div>
            ))}
          {page === "containers" && (
            <div className="container-grid">
              {containers.length === 0 ? (
                <section className="panel">
                  <Empty icon={Warehouse} title="Noch keine Behälter">
                    Legen Sie den ersten Behälter mit seiner Kapazität an.
                  </Empty>
                </section>
              ) : (
                containers.map((c) => (
                  <section key={c.id} className="panel container-card">
                    <div className="container-card-top">
                      <span className="container-symbol">
                        <Warehouse size={22} />
                      </span>
                      <Badge tone={c.active ? "green" : "neutral"}>
                        {c.active ? "Aktiv" : "Inaktiv"}
                      </Badge>
                    </div>
                    <h2>{c.name}</h2>
                    <p>{sites.find((s) => s.id === c.site_id)?.name}</p>
                    <div className="container-card-value">
                      {number(c.current_measurement?.effective_fill_level)}
                      <small>%</small>
                      <span>
                        {number(c.capacity)} {unit(c.capacity_unit)}
                      </span>
                    </div>
                    <div className="level-track">
                      <span
                        style={{
                          width: `${c.current_measurement?.effective_fill_level || 0}%`,
                        }}
                      />
                    </div>
                    <div className="container-description">
                      {c.description || "Keine Beschreibung hinterlegt."}
                    </div>
                    <div className="card-camera">
                      <CameraIcon size={15} />
                      {c.camera.ftp_connection_id
                        ? "FTP-Verbindung zugewiesen"
                        : "Kamera noch nicht verbunden"}
                    </div>
                    <div className="container-actions">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          openContainer(c.id);
                        }}
                      >
                        Übersicht
                        <ArrowUpRight size={13} />
                      </Button>
                      {admin && (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              setModal({ kind: "container", item: c })
                            }
                          >
                            Bearbeiten
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Kamera für ${c.name} konfigurieren`}
                            onClick={() =>
                              setModal({ kind: "camera", item: c })
                            }
                          >
                            <CameraIcon size={16} />
                          </Button>
                        </>
                      )}
                    </div>
                  </section>
                ))
              )}
            </div>
          )}
          {page === "users" && admin && (
            <section className="panel table-panel">
              <div className="panel-head">
                <div>
                  <h2>Zugriffsberechtigte Benutzer</h2>
                  <p>
                    Administratoren konfigurieren und erfassen. Benutzer lesen.
                  </p>
                </div>
                <Badge>{users.length} Benutzer</Badge>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>E-Mail</th>
                      <th>Rolle</th>
                      <th>Status</th>
                      <th>Aktion</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id}>
                        <td>
                          <strong>{u.email}</strong>
                          {u.id === user.id && (
                            <span className="you-label">Sie</span>
                          )}
                        </td>
                        <td>
                          <select
                            className="role-select"
                            aria-label={`Rolle für ${u.email}`}
                            value={u.role}
                            disabled={u.id === user.id}
                            title={
                              u.id === user.id
                                ? "Eigene Rolle kann nicht geändert werden"
                                : undefined
                            }
                            onChange={async (event) => {
                              const role = event.target.value as User["role"];
                              if (
                                !window.confirm(
                                  `${u.email} zum ${role === "admin" ? "Administrator" : "Benutzer"} machen?`,
                                )
                              )
                                return;
                              try {
                                await api(
                                  `/users/${u.id}/role`,
                                  write({ role }, "PUT"),
                                );
                                setSuccess(`Rolle von ${u.email} geändert.`);
                                await refresh();
                              } catch (e) {
                                setError((e as Error).message);
                              }
                            }}
                          >
                            <option value="user">Benutzer</option>
                            <option value="admin">Administrator</option>
                          </select>
                        </td>
                        <td>
                          <Badge tone={u.active ? "green" : "neutral"}>
                            {u.active ? "Aktiv" : "Inaktiv"}
                          </Badge>
                        </td>
                        <td>
                          <div className="user-actions">
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={u.id === user.id}
                              onClick={async () => {
                                try {
                                  await api(
                                    `/users/${u.id}/active?active=${!u.active}`,
                                    { method: "PUT" },
                                  );
                                  await refresh();
                                } catch (e) {
                                  setError((e as Error).message);
                                }
                              }}
                            >
                              {u.active ? "Deaktivieren" : "Aktivieren"}
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() =>
                                setModal({ kind: "password", item: u })
                              }
                            >
                              Passwort setzen
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="danger-action"
                              disabled={u.id === user.id}
                              title={
                                u.id === user.id
                                  ? "Eigenes Konto kann nicht gelöscht werden"
                                  : undefined
                              }
                              onClick={async () => {
                                if (
                                  !window.confirm(
                                    `Benutzer ${u.email} dauerhaft löschen? Messwerte und Lernbilder bleiben erhalten.`,
                                  )
                                )
                                  return;
                                try {
                                  await api(`/users/${u.id}`, {
                                    method: "DELETE",
                                  });
                                  setSuccess(`Benutzer ${u.email} gelöscht.`);
                                  await refresh();
                                } catch (e) {
                                  setError((e as Error).message);
                                }
                              }}
                            >
                              <Trash2 size={14} />
                              Löschen
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {page === "notifications" && (
            <Notifications revision={historyRevision} />
          )}
          {page === "containers" && admin && current && (
            <>
              <div className="operations-selector">
                <label>
                  Warnregeln für{" "}
                  <select
                    value={current.id}
                    onChange={(e) => setSelected(e.target.value)}
                  >
                    {containers.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div id="container-rules" tabIndex={-1}>
                <ContainerRules key={current.id} container={current} onChanged={refresh} />
              </div>
            </>
          )}
          {page === "training" && canTrain && (
            <Training
              admin={admin}
              containers={trainingContainers}
              selected={selected}
              onSelect={setSelected}
              onChanged={refresh}
            />
          )}
          {page === "system" && admin && (
            <>
              <SMTPSettings />
              <div className="system-overview">
                {[
                  {
                    label: "Datenbank",
                    value: status ? "Verbunden" : "Unbekannt",
                    ok: status?.database === "ok",
                    icon: Database,
                  },
                  {
                    label: "Import-Worker",
                    value:
                      status?.worker === "ok"
                        ? status.manual_run_pending
                          ? "Durchlauf angefordert · startet gleich"
                          : status.worker_busy
                            ? workerPhases[status.worker_phase] || "Arbeitet"
                            : status.last_run_finished_at
                              ? `Bereit · zuletzt ${date(status.last_run_finished_at)}`
                              : "Bereit · wartet auf nächsten Scan"
                        : "Kein aktuelles Lebenszeichen",
                    ok: status?.worker === "ok",
                    icon: status?.worker_busy ? LoaderCircle : Activity,
                  },
                  {
                    label: "Analyseverfahren",
                    value: "Manuell / lokales Modell",
                    ok: true,
                    icon: Gauge,
                  },
                ].map((s) => (
                  <div className="panel system-kpi" key={s.label}>
                    <s.icon
                      size={21}
                      className={
                        s.label === "Import-Worker" && status?.worker_busy
                          ? "spin"
                          : ""
                      }
                    />
                    <div>
                      <span>{s.label}</span>
                      <strong>{s.value}</strong>
                    </div>
                    <Badge tone={s.ok ? "green" : "amber"}>
                      {s.ok ? "OK" : "Prüfen"}
                    </Badge>
                  </div>
                ))}
              </div>
              <section className="panel">
                <div className="panel-head">
                  <div>
                    <h2>FTP-Verbindungen</h2>
                    <p>
                      Zugangsdaten werden verschlüsselt gespeichert und nicht
                      angezeigt.
                    </p>
                  </div>
                </div>
                {connections.length === 0 ? (
                  <Empty
                    icon={Database}
                    title="Bilder automatisch einlesen"
                    action={
                      <Button onClick={() => setModal({ kind: "ftp" })}>
                        <Plus size={16} />
                        Verbindung einrichten
                      </Button>
                    }
                  >
                    Richten Sie einen FTP-Zugang ein und weisen Sie ihn
                    anschließend unter Anlagen einer Kamera zu.
                  </Empty>
                ) : (
                  <div className="connection-list">
                    {connections.map((c) => (
                      <div className="connection-row" key={c.id}>
                        <span className="connection-icon">
                          <Database size={20} />
                        </span>
                        <div>
                          <h3>{c.name}</h3>
                          <p>
                            {c.protocol.toUpperCase()} · {c.host}:{c.port} ·{" "}
                            {c.root_path}
                          </p>
                        </div>
                        <Badge tone="green">Konfiguriert</Badge>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setModal({ kind: "ftp", item: c })}
                        >
                          Bearbeiten
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </section>
              <section className="panel table-panel">
                <div className="panel-head">
                  <div>
                    <h2>Kameras & Bildpuffer</h2>
                    <p>
                      Scanintervall: {status?.poll_seconds || 60} Sekunden ·
                      Letztes Lebenszeichen: {date(status?.heartbeat_at)}
                    </p>
                  </div>
                  <Badge>Maximal 3 Bilder / Kamera</Badge>
                </div>
                {!status?.cameras.length ? (
                  <Empty title="Noch keine Kameras">
                    Mit jedem neuen Behälter wird eine Kamera angelegt.
                  </Empty>
                ) : (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Kamera</th>
                          <th>Letzter Scan</th>
                          <th>Letzter Import</th>
                          <th>Puffer</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {status.cameras.map((c) => (
                          <tr key={c.id}>
                            <td>
                              <strong>{c.name}</strong>
                              <small className="block">
                                {c.scan_kind === "date_control"
                                  ? "Tagesordner + Kontrollscan"
                                  : c.scan_kind === "date"
                                    ? "Gezielte Tagesordner"
                                    : "Rekursive Suche"}{" "}
                                · {number(c.scanned_entries, 0)} Einträge
                              </small>
                              {(c.detected_date_pattern ||
                                c.scan_mode === "date") && (
                                <small className="block">
                                  {c.detected_date_pattern || c.date_pattern}
                                </small>
                              )}
                            </td>
                            <td>{date(c.last_scan_at)}</td>
                            <td>{date(c.last_import_at)}</td>
                            <td>
                              {c.buffer_count} / 3{" "}
                              <small className="block">
                                {number(c.buffer_bytes / 1024 / 1024, 2)} MB
                              </small>
                            </td>
                            <td>
                              <Badge
                                tone={
                                  c.error
                                    ? "amber"
                                    : c.configured && c.active
                                      ? "green"
                                      : "neutral"
                                }
                              >
                                {c.error
                                  ? "Fehler"
                                  : !c.active
                                    ? "Inaktiv"
                                    : c.configured
                                      ? "Konfiguriert"
                                      : "Nicht verbunden"}
                              </Badge>
                              {c.error && (
                                <p className="table-error">
                                  {c.error}
                                  <br />
                                  <code>{c.error_code}</code>
                                </p>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
          <footer className="footer">
            <span>
              fillens<span className="brand-dot">.</span>{" "}
              <span className="footer-separator">/</span> Klarheit über Ihren
              Füllstand
            </span>
            <span>
              Messwerte bleiben. Bilder wechseln.{" "}
              <a
                className="source-link"
                href={SOURCE_URL}
                target="_blank"
                rel="noreferrer"
              >
                Quellcode (AGPL-3.0)
              </a>
            </span>
          </footer>
        </main>
      </div>
      {modal && admin && (
        <Modal
          key={`${modal.kind}-${"item" in modal ? modal.item?.id || "new" : "new"}`}
          title={
            modal.kind === "container"
              ? modal.item
                ? "Behälter bearbeiten"
                : "Behälter anlegen"
              : modal.kind === "camera"
                ? "Kamera konfigurieren"
                : modal.kind === "password"
                  ? `Passwort für ${modal.item.email} setzen`
                  : modal.kind === "ftp"
                    ? modal.item
                      ? "FTP-Verbindung bearbeiten"
                      : "FTP-Verbindung anlegen"
                    : "Benutzer anlegen"
          }
          onClose={() => setModal(null)}
          onSave={save}
        >
          {modal.kind === "container" && (
            <ContainerForm item={modal.item} sites={sites} />
          )}
          {modal.kind === "ftp" && <FTPForm item={modal.item} />}
          {modal.kind === "camera" && (
            <CameraForm item={modal.item} connections={connections} />
          )}
          {modal.kind === "password" && (
            <>
              <Field label="Neues Passwort" hint="Mindestens 12 Zeichen. Bestehende Sitzungen des Kontos werden beendet.">
                <input
                  autoFocus
                  name="password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={256}
                  autoComplete="new-password"
                />
              </Field>
              <Field label="Passwort wiederholen">
                <input
                  name="password_repeat"
                  type="password"
                  required
                  minLength={12}
                  maxLength={256}
                  autoComplete="new-password"
                />
              </Field>
            </>
          )}
          {modal.kind === "user" && (
            <>
              <Field label="E-Mail">
                <input
                  name="email"
                  type="email"
                  required
                  maxLength={254}
                  autoComplete="off"
                />
              </Field>
              <Field label="Passwort" hint="Mindestens 12 Zeichen.">
                <input
                  name="password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={256}
                  autoComplete="new-password"
                />
              </Field>
              <Field label="Rolle">
                <select name="role" defaultValue="user">
                  <option value="user">Benutzer · lesen</option>
                  <option value="admin">
                    Administrator · konfigurieren und erfassen
                  </option>
                </select>
              </Field>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
