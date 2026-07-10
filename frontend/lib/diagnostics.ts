// Client-side helpers for developer diagnostic visibility.

export function showDiagnostics(): boolean {
  return process.env.NEXT_PUBLIC_SOCIALEASE_SHOW_DIAGNOSTICS === "true";
}

export function showTraceLinks(): boolean {
  return (
    process.env.NEXT_PUBLIC_SOCIALEASE_SHOW_TRACE === "true" ||
    showDiagnostics()
  );
}
