"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  currentUserId,
  isAuthenticatedForFrontend,
  subscribeAuthState
} from "@/lib/auth";

export type RequiredAuthState = {
  ready: boolean;
  authenticated: boolean;
  userId: string | null;
};

export function useRequireAuth(): RequiredAuthState {
  const router = useRouter();
  const [state, setState] = useState<RequiredAuthState>({
    ready: false,
    authenticated: false,
    userId: null
  });

  useEffect(() => {
    function refresh() {
      const authenticated = isAuthenticatedForFrontend();
      setState({
        ready: true,
        authenticated,
        userId: authenticated ? currentUserId() : null
      });
      if (!authenticated) {
        router.replace("/login");
      }
    }
    refresh();
    return subscribeAuthState(refresh);
  }, [router]);

  return state;
}
