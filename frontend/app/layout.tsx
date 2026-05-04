import type { Metadata } from "next";
import type React from "react";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "SocialEase Agent",
  description: "Safe agentic workflows for university social practice."
};

const navItems = [
  { href: "/chat", label: "Chat" },
  { href: "/practice", label: "Practice" },
  { href: "/worksheet", label: "Worksheet" },
  { href: "/progress", label: "Progress" },
  { href: "/trace", label: "Trace" }
];

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="min-h-screen">
          <header className="border-b border-line bg-white">
            <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
              <Link href="/chat" className="text-lg font-semibold tracking-normal">
                SocialEase Agent
              </Link>
              <nav className="flex flex-wrap gap-2 text-sm">
                {navItems.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="rounded-md border border-line px-3 py-1.5 text-slate-700 hover:border-brand hover:text-brand"
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            </div>
          </header>
          <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
