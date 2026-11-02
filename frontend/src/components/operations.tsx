"use client";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { Button } from "@/components/ui/button";
import {
  api,
  date,
  number,
  unit,
  write,
  rateFactor,
  rateUnit,
  type Container,
  type ContainerEvent,
  type Measurement,
} from "@/lib/api";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
function Error({ value }: { value: string }) {
  return value ? (
    <div className="error-notice" role="alert">
      {value}
    </div>
  ) : null;
}
export function RateHistory({
  container,
  items,
  events,
  loading,
}: {
  container: Container;
  items: Measurement[];
  events: ContainerEvent[];
  loading: boolean;
}) {
  const [kind, setKind] = useState("volume");
  const factor = rateFactor(container.rate_time_unit);
  const label = `${kind === "volume" ? unit(container.capacity_unit) : "Prozentpunkte"}/${rateUnit(container.rate_time_unit)}`;
  // Jede Zyklus-ID erhält eine eigene Linie. Auch bei ausgedünnter Historie
  // kann dadurch kein Sprung zwischen zwei Betriebszyklen verbunden werden.
  const cycles = [
    ...new Set(items.map((m) => m.rate_cycle).filter((c) => c != null)),
  ];
  const data = items.map((m) => {
    const value =
      kind === "volume"
        ? m.operating_rate_absolute == null
          ? null
          : (m.operating_rate_absolute *
              (m.capacity_unit === "m3" ? 1000 : 1)) /
            (container.capacity_unit === "m3" ? 1000 : 1)
        : m.operating_rate_percent;
    return {
      time: new Date(m.measured_at).getTime(),
      [`cycle_${m.rate_cycle}`]:
        m.rate_revision === container.calculated_revision && value != null
          ? value * factor
          : null,
    };
  });
  const first = data[0]?.time,
    last = data[data.length - 1]?.time;
  return (
    <section className="panel history-panel">
      <div className="panel-head">
        <div>
          <h2>
            {container.display_mode === "fill" ? "Füllrate" : "Verbrauch"} im
            Verlauf
          </h2>
          <p>
            Zeitgewichtetes Fenster: {number(container.smoothing_minutes, 0)}{" "}
            Minuten · {label}
          </p>
        </div>
        <select
          aria-label="Einheit des Ratendiagramms"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          <option value="volume">Volumenrate</option>
          <option value="percent">Prozentpunkte</option>
        </select>
      </div>
      {container.rate_status !== "ready" ||
      items.some((m) => m.rate_revision !== container.calculated_revision) ? (
        <p className="operations-message" role="status">
          Wird neu berechnet …
        </p>
      ) : loading ? (
        <p className="operations-message">Messwerte werden geladen …</p>
      ) : !items.some((m) => m.operating_rate_percent != null) ? (
        <p className="operations-message">
          Noch keine Rate im gewählten Zeitraum. Zwei gültige Messungen
          innerhalb des Glättungsfensters sind erforderlich.
        </p>
      ) : (
        <div className="chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={data}
              margin={{ top: 28, right: 24, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 5" vertical={false} />
              <XAxis
                dataKey="time"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v) =>
                  new Intl.DateTimeFormat("de-DE", {
                    timeZone: container.camera.timezone,
                    day: "2-digit",
                    month: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  }).format(new Date(v))
                }
                minTickGap={60}
              />
              <YAxis padding={{bottom: 4}} domain={[0, (maximum: number) => maximum > 0 ? maximum : 1]} tickFormatter={(v) => number(v)} />
              <Tooltip
                labelFormatter={(v) =>
                  date(
                    new Date(Number(v)).toISOString(),
                    container.camera.timezone,
                  )
                }
                formatter={(v) => [
                  `${number(Number(v), 3)} ${label}`,
                  container.display_mode === "fill" ? "Füllrate" : "Verbrauch",
                ]}
              />
              {cycles.map((c) => (
                <Line
                  key={c}
                  dataKey={`cycle_${c}`}
                  stroke="#237c60"
                  strokeWidth={2}
                  type="linear"
                  dot={items.length < 20}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ))}
              {events
                .filter(
                  (e) =>
                    new Date(e.occurred_at).getTime() >= first &&
                    new Date(e.occurred_at).getTime() <= last,
                )
                .map((e) => (
                  <ReferenceLine
                    key={e.id}
                    x={new Date(e.occurred_at).getTime()}
                    stroke="#b07831"
                    strokeDasharray="4 4"
                    label={{
                      value: e.kind === "emptying" ? "Leerung" : "Nachfüllung",
                      position: "insideTop",
                      fontSize: 10,
                    }}
                  />
                ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

export function Events({
  container,
  events,
  admin,
  onChanged,
}: {
  container: Container;
  events: ContainerEvent[];
  admin: boolean;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<ContainerEvent | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const localTime = (iso: string) => {
    const d = new Date(iso);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  };
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setBusy(true);
    setError("");
    try {
      await api(
        `/containers/${container.id}/events${editing ? `/${editing.id}` : ""}`,
        write(
          {
            occurred_at: new Date(String(f.get("time"))).toISOString(),
            kind: f.get("kind"),
            note: f.get("note"),
          },
          editing ? "PUT" : "POST",
        ),
      );
      setEditing(null);
      form.reset();
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(event: ContainerEvent) {
    setBusy(true);
    setError("");
    try {
      await api(`/containers/${container.id}/events/${event.id}`, {
        method: "DELETE",
      });
      if (editing?.id === event.id) setEditing(null);
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel operations-panel">
      <div className="panel-head">
        <div>
          <h2>Leerungen & Nachfüllungen</h2>
          <p>
            Ereignisse unterbrechen die Rate. Der Füllstand bleibt unverändert.
          </p>
        </div>
      </div>
      <Error value={error} />
      {admin && (
        <form
          key={`${container.id}-${editing?.id || "new"}`}
          onSubmit={submit}
          className="operations-form"
        >
          <div className="form-grid">
            <Field label="Zeitpunkt (Browser-Zeitzone)">
              <input
                name="time"
                type="datetime-local"
                required
                defaultValue={
                  editing
                    ? localTime(editing.occurred_at)
                    : localTime(new Date().toISOString())
                }
              />
            </Field>
            <Field label="Ereignis">
              <select
                name="kind"
                defaultValue={
                  editing?.kind ||
                  (container.display_mode === "fill" ? "emptying" : "refill")
                }
              >
                <option value="emptying">Leerung</option>
                <option value="refill">Nachfüllung</option>
              </select>
            </Field>
          </div>
          <Field label="Notiz">
            <input name="note" maxLength={500} defaultValue={editing?.note} />
          </Field>
          <div className="operations-actions">
            <Button disabled={busy}>
              {editing ? "Ereignis korrigieren" : "Ereignis markieren"}
            </Button>
            {editing && (
              <Button
                type="button"
                variant="outline"
                onClick={() => setEditing(null)}
              >
                Abbrechen
              </Button>
            )}
          </div>
        </form>
      )}
      <div className="event-list">
        {events.length === 0 ? (
          <p>Keine Ereignisse vorhanden.</p>
        ) : (
          [...events].reverse().map((e) => (
            <div key={e.id}>
              <span>
                <strong>
                  {e.kind === "emptying" ? "Leerung" : "Nachfüllung"}
                </strong>{" "}
                · {date(e.occurred_at, container.camera.timezone)}
                <small>
                  {e.source === "manual" ? "Manuell" : "Automatisch"}
                  {e.note ? ` · ${e.note}` : ""}
                </small>
              </span>
              {admin && (
                <span className="operations-actions">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => setEditing(e)}
                  >
                    Korrigieren
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => remove(e)}
                  >
                    Entfernen
                  </Button>
                </span>
              )}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

type Rules = {
  fill_notice: number;
  fill_warning: number;
  consumption_notice: number;
  consumption_warning: number;
  email_enabled: boolean;
  email_recipients: string[];
};
export function ContainerRules({
  container,
  onChanged,
}: {
  container: Container;
  onChanged: () => Promise<void>;
}) {
  const [rules, setRules] = useState<Rules | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let active = true;
    setRules(null);
    setError("");
    setSaved(false);
    api<Rules>(`/containers/${container.id}/alert-rules`)
      .then((v) => {
        if (active) setRules(v);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [container.id]);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await api(
        `/containers/${container.id}/alert-rules`,
        write(
          {
            fill_notice: Number(f.get("fill_notice")),
            fill_warning: Number(f.get("fill_warning")),
            consumption_notice: Number(f.get("consumption_notice")),
            consumption_warning: Number(f.get("consumption_warning")),
            email_enabled: f.has("email_enabled"),
            email_recipients: String(f.get("recipients"))
              .split(/[;,\s]+/)
              .filter(Boolean),
          },
          "PUT",
        ),
      );
      setSaved(true);
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel operations-panel">
      <div className="panel-head">
        <div>
          <h2>Warnregeln · {container.name}</h2>
          <p>Feste Füllstandsschwellen · 2 Prozentpunkte Hysterese</p>
        </div>
      </div>
      <Error value={error} />
      {rules && (
        <form key={container.id} onSubmit={submit} className="operations-form">
          <div className="form-grid">
            {(
              [
                ["fill_notice", "Füllen: Hinweis ab (%)"],
                ["fill_warning", "Füllen: Warnung ab (%)"],
                ["consumption_notice", "Verbrauch: Hinweis bis (%)"],
                ["consumption_warning", "Verbrauch: Warnung bis (%)"],
              ] as const
            ).map(([key, label]) => (
              <Field key={key} label={label}>
                <input
                  type="number"
                  name={key}
                  min={0}
                  max={100}
                  step="any"
                  required
                  defaultValue={rules[key]}
                />
              </Field>
            ))}
          </div>
          <Field label="E-Mail-Empfänger (durch Komma oder Zeilenumbruch getrennt)">
            <textarea
              name="recipients"
              rows={2}
              defaultValue={rules.email_recipients.join("\n")}
            />
          </Field>
          <label className="check-field">
            <input
              type="checkbox"
              name="email_enabled"
              defaultChecked={rules.email_enabled}
            />
            E-Mail-Versand für diesen Behälter aktivieren
          </label>
          <p className="muted">
            Portalwarnungen funktionieren unabhängig vom E-Mail-Versand. SMTP
            wird unter System eingerichtet.
          </p>
          <Button disabled={busy}>Warnregeln speichern</Button>
          {saved && <span role="status"> Gespeichert.</span>}
        </form>
      )}
    </section>
  );
}

type SMTP = {
  host: string;
  port: number;
  encryption: string;
  username: string;
  sender: string;
  has_password: boolean;
  ready: boolean;
};
export function SMTPSettings() {
  const [config, setConfig] = useState<SMTP | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    api<SMTP>("/system/smtp")
      .then(setConfig)
      .catch((e) => setError(e.message));
  }, []);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      setConfig(
        await api<SMTP>(
          "/system/smtp",
          write(
            {
              host: f.get("host"),
              port: Number(f.get("port")),
              encryption: f.get("encryption"),
              username: f.get("username"),
              password: f.get("password") || null,
              sender: f.get("sender"),
              clear_password: f.has("clear_password"),
            },
            "PUT",
          ),
        ),
      );
      (form.elements.namedItem("password") as HTMLInputElement).value = "";
      (form.elements.namedItem("clear_password") as HTMLInputElement).checked =
        false;
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel operations-panel">
      <div className="panel-head">
        <div>
          <h2>E-Mail-Versand (SMTP)</h2>
          <p>
            {config?.ready
              ? "Konfiguration vollständig. Versand erfolgt im Worker."
              : "Noch nicht vollständig eingerichtet. Portalwarnungen bleiben aktiv."}
          </p>
        </div>
      </div>
      <Error value={error} />
      {config && (
        <form className="operations-form" onSubmit={submit}>
          <div className="form-grid">
            <Field label="Server">
              <input name="host" maxLength={253} defaultValue={config.host} />
            </Field>
            <Field label="Port">
              <input
                name="port"
                type="number"
                min={1}
                max={65535}
                required
                defaultValue={config.port}
              />
            </Field>
            <Field label="Verschlüsselung">
              <select name="encryption" defaultValue={config.encryption}>
                <option value="starttls">STARTTLS</option>
                <option value="ssl">TLS (SSL)</option>
                <option value="none">Keine (lokaler Mailserver)</option>
              </select>
            </Field>
            <Field label="Absenderadresse">
              <input
                name="sender"
                type="email"
                maxLength={254}
                defaultValue={config.sender}
              />
            </Field>
            <Field label="Benutzer (optional)">
              <input
                name="username"
                maxLength={254}
                autoComplete="off"
                defaultValue={config.username}
              />
            </Field>
            <Field
              label={
                config.has_password
                  ? "Neues Passwort (leer = behalten)"
                  : "Passwort"
              }
            >
              <input
                name="password"
                type="password"
                autoComplete="new-password"
                maxLength={1000}
              />
            </Field>
          </div>
          <label className="check-field">
            <input type="checkbox" name="clear_password" />
            Gespeichertes Passwort entfernen
          </label>
          <Button disabled={busy}>SMTP speichern</Button>
          {saved && <span role="status"> Gespeichert.</span>}
        </form>
      )}
    </section>
  );
}

type Notice = {
  id: string;
  container_id: string;
  container_name: string;
  severity: number;
  mode: string;
  measured_at: string;
  fill_level: number;
  threshold: number;
  active: boolean;
  read: boolean;
  deliveries: {
    id: string;
    recipient: string;
    status: string;
    attempts: number;
    last_error: string | null;
  }[];
};
export function Notifications({ revision }: { revision: number }) {
  const [rows, setRows] = useState<Notice[]>([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () =>
    api<Notice[]>(`/notifications?offset=${offset}`)
      .then(setRows)
      .catch((e) => setError(e.message));
  useEffect(() => {
    let active = true;
    setError("");
    setBusy(true);
    api<Notice[]>(`/notifications?offset=${offset}`)
      .then((v) => {
        if (active) setRows(v);
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [offset, revision]);
  async function read(id: string) {
    setError("");
    try {
      await api(`/notifications/${id}/read`, write({}));
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const statuses: Record<string, string> = {
    cancelled: "Abgebrochen (Versand deaktiviert)",
    pending: "Wartet",
    sending: "Wird gesendet",
    retry: "Erneuter Versuch geplant",
    failed: "Fehlgeschlagen",
    sent: "Gesendet",
  };
  return (
    <section className="panel operations-panel">
      <div className="panel-head">
        <div>
          <h2>Benachrichtigungen</h2>
          <p>Aktive Meldungen und Historie · Gelesen-Status je Benutzer</p>
        </div>
      </div>
      <Error value={error} />
      {busy ? (
        <p className="operations-message">Wird geladen …</p>
      ) : rows.length === 0 ? (
        <p className="operations-message">Keine Meldungen vorhanden.</p>
      ) : (
        rows.map((n) => (
          <article
            key={n.id}
            className={`notification ${n.read ? "read" : "unread"}`}
          >
            <div>
              <strong>
                {n.severity === 2 ? "Warnung" : "Hinweis"} · {n.container_name}
              </strong>
              <span className={`badge ${n.active ? "amber" : "neutral"}`}>
                {n.active ? "Aktiv" : "Historie"}
              </span>
            </div>
            <p>
              {n.mode === "fill" ? "Überlaufen droht" : "Leerstand droht"}:{" "}
              {number(n.fill_level)} % · Schwelle {number(n.threshold)} %
            </p>
            <small>Messzeit: {date(n.measured_at)}</small>
            {n.deliveries.map((d) => (
              <p className="delivery" key={d.id}>
                {d.recipient} · {statuses[d.status] || d.status} · {d.attempts}{" "}
                Versuch(e){d.last_error && ` · ${d.last_error}`}
              </p>
            ))}
            {!n.read && (
              <Button size="sm" variant="outline" onClick={() => read(n.id)}>
                Als gelesen markieren
              </Button>
            )}
          </article>
        ))
      )}
      <div className="operations-actions">
        <Button
          variant="outline"
          disabled={!offset || busy}
          onClick={() => setOffset(Math.max(0, offset - 100))}
        >
          Neuere Meldungen
        </Button>
        <Button
          variant="outline"
          disabled={rows.length < 100 || busy}
          onClick={() => setOffset(offset + 100)}
        >
          Ältere Meldungen
        </Button>
      </div>
    </section>
  );
}
