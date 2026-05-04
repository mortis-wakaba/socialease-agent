import type React from "react";
import type { Citation, RiskLevel } from "@/lib/types";

export function PageHeader({
  title,
  description
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="mb-5">
      <h1 className="text-2xl font-semibold text-ink">{title}</h1>
      <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">
        {description}
      </p>
    </div>
  );
}

export function Panel({
  title,
  children,
  action
}: {
  title?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-line bg-white p-4 shadow-sm">
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title ? <h2 className="font-semibold text-ink">{title}</h2> : <div />}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Badge({
  children,
  tone = "neutral"
}: {
  children: React.ReactNode;
  tone?: "neutral" | "good" | "warn" | "danger" | "info";
}) {
  const tones = {
    neutral: "border-slate-300 bg-slate-50 text-slate-700",
    good: "border-emerald-300 bg-emerald-50 text-emerald-700",
    warn: "border-amber-300 bg-amber-50 text-amber-800",
    danger: "border-rose-300 bg-rose-50 text-rose-700",
    info: "border-sky-300 bg-sky-50 text-sky-700"
  };
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-1 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function riskTone(risk?: RiskLevel) {
  if (risk === "crisis" || risk === "high") {
    return "danger" as const;
  }
  if (risk === "medium") {
    return "warn" as const;
  }
  if (risk === "low") {
    return "good" as const;
  }
  return "neutral" as const;
}

export function Button({
  children,
  disabled,
  onClick,
  type = "button",
  variant = "primary"
}: {
  children: React.ReactNode;
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: "primary" | "secondary" | "danger";
}) {
  const variants = {
    primary: "border-brand bg-brand text-white hover:bg-[#176052]",
    secondary: "border-line bg-white text-slate-700 hover:border-brand hover:text-brand",
    danger: "border-rose-500 bg-rose-600 text-white hover:bg-rose-700"
  };
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-md border px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${variants[variant]}`}
    >
      {children}
    </button>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none focus:border-brand ${props.className ?? ""}`}
    />
  );
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`min-h-28 w-full resize-y rounded-md border border-line bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-brand ${props.className ?? ""}`}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={`w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none focus:border-brand ${props.className ?? ""}`}
    />
  );
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) {
    return null;
  }
  return (
    <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
      {message}
    </div>
  );
}

export function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations.length) {
    return <p className="text-sm text-slate-500">No citations returned.</p>;
  }
  return (
    <div className="space-y-2">
      {citations.map((citation, index) => (
        <div key={`${citation.title}-${index}`} className="rounded-md border border-line bg-panel p-3">
          <div className="text-sm font-medium text-ink">{citation.title}</div>
          <div className="text-xs text-slate-500">{citation.source}</div>
          <p className="mt-2 text-sm leading-6 text-slate-700">{citation.snippet}</p>
        </div>
      ))}
    </div>
  );
}
