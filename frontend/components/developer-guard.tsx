"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useDeveloperAccess } from "@/lib/use-developer-access";
import { EmptyState } from "@/components/ui";

export function DeveloperGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const developerAccess = useDeveloperAccess();

  useEffect(() => {
    if (developerAccess.ready && !developerAccess.authenticated) {
      router.replace("/login");
    }
  }, [developerAccess.authenticated, developerAccess.ready, router]);

  if (!developerAccess.ready) {
    return (
      <EmptyState
        title="正在检查开发者权限"
        description="请稍候。"
      />
    );
  }

  if (!developerAccess.authenticated) {
    return (
      <EmptyState
        title="需要登录"
        description="正在跳转到登录页。"
      />
    );
  }

  if (!developerAccess.allowed) {
    return (
      <EmptyState
        title="需要开发者权限"
        description="Trace 是开发者排查入口，普通用户界面不会展示内部流程细节。"
      />
    );
  }

  return <>{children}</>;
}
