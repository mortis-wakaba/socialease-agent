"use client";

import Link from "next/link";
import { showTraceLinks } from "@/lib/diagnostics";
import { useDeveloperAccess } from "@/lib/use-developer-access";

const baseNavItems = [
  { href: "/dashboard", label: "工作台" },
  { href: "/", label: "首页" },
  { href: "/onboarding", label: "开始" },
  { href: "/chat", label: "对话" },
  { href: "/practice", label: "练习" },
  { href: "/worksheet", label: "反思" },
  { href: "/progress", label: "进度" },
  { href: "/history", label: "历史" },
  { href: "/memory", label: "记忆" },
  { href: "/support", label: "资源" },
  { href: "/settings", label: "设置" },
  { href: "/privacy", label: "隐私" },
  { href: "/terms", label: "知情说明" }
];

export function MainNav() {
  const developerAccess = useDeveloperAccess();
  const showTrace = showTraceLinks() && developerAccess.allowed;

  const navItems = showTrace
    ? [...baseNavItems, { href: "/trace", label: "Trace" }]
    : baseNavItems;

  return (
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
  );
}
