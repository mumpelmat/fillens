"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, LoaderCircle } from "lucide-react";
import { api, write } from "@/lib/api";
import { Button } from "@/components/ui/button";

export default function PasswordRecovery({ onBack }: { onBack: (message?: string) => void }) {
  const [step, setStep] = useState<"request" | "confirm">("request");
  const [available, setAvailable] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<{ available: boolean }>("/auth/password-reset/status", { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setAvailable(value.available); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError("");
    if (step === "confirm" && data.get("password") !== data.get("repeat")) {
      setError("Die Passwörter stimmen nicht überein."); return;
    }
    setBusy(true);
    try {
      if (step === "request") {
        const response = await api<{ message: string }>("/auth/password-reset/request", write({ email: data.get("email") }));
        setMessage(response.message); setStep("confirm");
      } else {
        const response = await api<{ message: string }>("/auth/password-reset/confirm", write({ code: data.get("code"), password: data.get("password") }));
        onBack(response.message);
      }
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }

  return <form onSubmit={submit} className="password-recovery">
    <span className="eyebrow">FÜLLSTANDSPORTAL</span>
    <h2>{step === "request" ? "Passwort vergessen?" : "Neues Passwort vergeben"}</h2>
    <p>{step === "request" ? "Fordern Sie einen Einmalcode an die E-Mail-Adresse Ihres Kontos an." : "Fügen Sie den Code aus der E-Mail ein. Er ist 30 Minuten gültig und kann einmal verwendet werden."}</p>
    {error && <div className="error-notice" role="alert">{error}</div>}
    {message && <div className="success-notice" role="status">{message}</div>}
    {step === "request" ? <>
      {available === false && <p className="error-notice" role="status">Passwortzurücksetzung per E-Mail ist noch nicht eingerichtet. Bitte den Administrator kontaktieren.</p>}
      <label className="field"><span>E-Mail</span><input type="email" name="email" autoComplete="username" required maxLength={254} placeholder="name@unternehmen.de" /></label>
      <Button type="submit" className="w-full" disabled={busy || available !== true}>
        {busy && <LoaderCircle size={16} className="spin" />} Rücksetzcode anfordern
      </Button>
      <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={() => { setError(""); setStep("confirm"); }}>Ich habe bereits einen Code</Button>
    </> : <>
      <label className="field"><span>Rücksetzcode</span><input name="code" autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} required maxLength={256} /></label>
      <label className="field"><span>Neues Passwort</span><input name="password" type="password" autoComplete="new-password" required minLength={12} maxLength={256} /><small>Mindestens 12 Zeichen.</small></label>
      <label className="field"><span>Passwort wiederholen</span><input name="repeat" type="password" autoComplete="new-password" required minLength={12} maxLength={256} /></label>
      <Button type="submit" className="w-full" disabled={busy}>{busy && <LoaderCircle size={16} className="spin" />} Passwort ändern</Button>
      <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={() => { setStep("request"); setError(""); setMessage(""); }}>Neuen Code anfordern</Button>
    </>}
    <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={() => onBack()}><ArrowLeft size={15} /> Zur Anmeldung</Button>
  </form>;
}
