"use client";

import { useEffect, useRef, useState } from "react";
import { LoaderCircle, PlugZap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, write, type FTPConnection, type FTPTestResult } from "@/lib/api";

export default function FTPTest({ connection, connections }: {
  connection?: FTPConnection; connections?: FTPConnection[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const revision = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FTPTestResult | null>(null);
  const [error, setError] = useState("");
  const camera = connections !== undefined;
  useEffect(() => {
    const form = ref.current?.closest("form");
    const invalidate = (event: Event) => {
      const name = (event.target as HTMLInputElement).name;
      if (!(camera ? ["ftp_connection_id", "ftp_root"] : ["host", "port", "username", "password", "protocol", "root_path"]).includes(name)) return;
      revision.current++;
      setResult(null); setError("");
    };
    form?.addEventListener("input", invalidate);
    form?.addEventListener("change", invalidate);
    return () => {
      revision.current++;
      controller.current?.abort();
      form?.removeEventListener("input", invalidate);
      form?.removeEventListener("change", invalidate);
    };
  }, [camera]);

  async function run() {
    const form = ref.current?.closest("form");
    if (!form || busy) return;
    const data = new FormData(form);
    const get = (name: string) => String(data.get(name) ?? "");
    const selected = camera ? connections?.find((c) => c.id === get("ftp_connection_id")) : connection;
    if (camera && !selected) { setError("Bitte zuerst eine FTP-Verbindung auswählen."); return; }
    if (!camera) {
      for (const name of ["host", "port", "username", "password", "root_path"]) {
        const field = form.elements.namedItem(name) as HTMLInputElement | null;
        if (field && !field.reportValidity()) return;
      }
    }
    const payload = {
      connection_id: selected?.id || null,
      name: camera ? selected!.name : get("name") || "FTP-Test",
      host: camera ? selected!.host : get("host"),
      port: camera ? selected!.port : Number(get("port")),
      username: camera ? selected!.username : get("username"),
      password: camera ? null : get("password") || (selected ? null : ""),
      protocol: camera ? selected!.protocol : get("protocol"),
      root_path: camera ? selected!.root_path : get("root_path"),
      ftp_root: camera ? get("ftp_root") : "",
    };
    const version = ++revision.current;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await api<FTPTestResult>("/ftp-connections/test", { ...write(payload), signal: abort.signal });
      if (revision.current === version) setResult(response);
    } catch (error) {
      if (revision.current === version && !abort.signal.aborted) setError((error as Error).message);
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }
  return <div className="ftp-test" ref={ref}>
    <Button type="button" variant="outline" disabled={busy} onClick={() => void run()}>
      {busy ? <LoaderCircle size={16} className="spin" /> : <PlugZap size={16} />}
      {busy ? "Verbindung wird geprüft …" : camera ? "FTP-Ordner testen" : "Verbindung testen"}
    </Button>
    <p className="hint">Prüft die aktuellen Eingaben, ohne sie zu speichern. Nur die erste Verzeichnisebene wird gezählt.</p>
    <div aria-live="polite" aria-atomic="true">
      {error && <p className="error-notice">{error}</p>}
      {result && <div className={`ftp-test-result ${result.listed ? "is-success" : "is-error"}`}>
        <strong>{result.listed ? "Verbindung erfolgreich" : result.connected ? "Verbunden · Auflistung nicht vollständig" : "Verbindung nicht bestätigt"}</strong>
        <span>Geprüftes Verzeichnis: {result.path}</span>
        {result.listed ? <span>{result.folders} Ordner · {result.images} {result.images === 1 ? "Bilddatei" : "Bilddateien"} auf erster Ebene</span>
          : <span>{result.message}</span>}
      </div>}
    </div>
  </div>;
}
