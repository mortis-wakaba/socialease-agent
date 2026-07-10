"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  isAuthenticatedForFrontend,
  subscribeAuthState
} from "@/lib/auth";
import { EmptyState } from "@/components/ui";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [allowed, setAllowed] = useState<boolean | null>(null);

  useEffect(() => {
    function refresh() {
      const nextAllowed = isAuthenticatedForFrontend();
      setAllowed(nextAllowed);
      if (!nextAllowed) {
        router.replace("/login");
      }
    }
    refresh();
    return subscribeAuthState(refresh);
  }, [router]);

  if (allowed === null) {
    return (
      <EmptyState
        title="正在检查登录状态"
        description="请稍候。"
      />
    );
  }

  if (allowed === false) {
    return (
      <EmptyState
        title="需要登录"
        description="正在跳转到登录页。"
      />
    );
  }

  return <>{children}</>;
}
